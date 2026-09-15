"""Git はローカル bare remote、GitHub は偽物を使い、公開内容と失敗時の境界を確認する。"""

import hashlib
import json
import os
import subprocess
import sys
import tarfile

import pytest

from tests.test_config import ROOT


@pytest.fixture
def release(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    remote = tmp_path / "remote.git"
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ID": "123",
        "GITHUB_REPOSITORY": "example/action",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "RELEASE_VERSION": "v1.2.3",
        "TEST_ARTIFACTS": str(tmp_path / "artifacts"),
        "TEST_GH_LOG": str(tmp_path / "gh.jsonl"),
    }

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=source, env=env, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q", "--initial-branch=main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (source / ".gitignore").write_text("dist/\n")
    (source / "cli.py").write_text("# release source\n")
    git("add", ".")
    git("commit", "-qm", "Source")
    env["GITHUB_SHA"] = git("rev-parse", "HEAD")
    git("init", "--bare", "-q", str(remote))
    git("remote", "add", "origin", str(remote))
    git("push", "-q", "origin", "main")
    for platform in ("linux-x64", "linux-arm64"):
        artifact = tmp_path / "artifacts" / ("binary-" + platform)
        artifact.mkdir(parents=True)
        contents = ("compiled fixture " + platform).encode()
        (artifact / "pr-merge-readiness").write_bytes(contents)
        (artifact / "SHA256SUMS").write_text(
            hashlib.sha256(contents).hexdigest() + "  pr-merge-readiness\n"
        )
    tools = tmp_path / "tools"
    tools.mkdir()
    gh = tools / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, shutil, sys\n"
        "args = sys.argv[1:]\n"
        "record = {'args': args}\n"
        "if args[:2] == ['run', 'download']:\n"
        "    assert args[2] == os.environ['GITHUB_RUN_ID']\n"
        "    name = args[args.index('--name') + 1]\n"
        "    target = args[args.index('--dir') + 1]\n"
        "    shutil.copytree(pathlib.Path(os.environ['TEST_ARTIFACTS']) / name, target)\n"
        "elif args[:2] == ['release', 'create']:\n"
        "    record['notes'] = pathlib.Path(args[args.index('--notes-file') + 1]).read_text()\n"
        "else:\n"
        "    assert args == ['auth', 'setup-git']\n"
        "with open(os.environ['TEST_GH_LOG'], 'a') as log:\n"
        "    log.write(json.dumps(record) + '\\n')\n"
    )
    gh.chmod(0o755)
    env["PATH"] = str(tools) + os.pathsep + os.environ["PATH"]

    def publish():
        return subprocess.run(
            ["bash", str(ROOT / "scripts/publish_release.sh")],
            cwd=source,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    return source, remote, env, git, publish


def test_release_pins_verified_binaries_without_advancing_main(release, tmp_path):
    source, remote, env, git, publish = release
    result = publish()
    assert result.returncode == 0, result.stdout + result.stderr
    commit = git("rev-parse", "v1.2.3")
    assert git("rev-parse", commit + "^") == env["GITHUB_SHA"]
    assert git("--git-dir=" + str(remote), "rev-parse", "refs/heads/main") == env["GITHUB_SHA"]
    assert git("--git-dir=" + str(remote), "rev-parse", "refs/tags/v1.2.3") == commit
    assert (
        git("--git-dir=" + str(remote), "rev-parse", "refs/heads/codex/releases/v1.2.3") == commit
    )
    assert git("diff", "--name-only", env["GITHUB_SHA"], commit).splitlines() == [
        "dist/linux-arm64/SHA256SUMS",
        "dist/linux-arm64/pr-merge-readiness",
        "dist/linux-x64/SHA256SUMS",
        "dist/linux-x64/pr-merge-readiness",
    ]
    for platform in ("linux-x64", "linux-arm64"):
        assert git("ls-tree", commit, f"dist/{platform}/pr-merge-readiness").startswith("100755 ")
        with tarfile.open(source / "dist" / f"pr-merge-readiness-{platform}.tar.gz") as archive:
            assert archive.getnames() == ["pr-merge-readiness", "SHA256SUMS"]
            member = archive.getmember("pr-merge-readiness")
            assert member.mode == 0o755
            assert archive.extractfile(member).read() == ("compiled fixture " + platform).encode()
    calls = [json.loads(line) for line in (tmp_path / "gh.jsonl").read_text().splitlines()]
    assert [call["args"][:2] for call in calls] == [
        ["run", "download"],
        ["run", "download"],
        ["auth", "setup-git"],
        ["release", "create"],
    ]
    assert "--verify-tag" in calls[-1]["args"]
    assert calls[-1]["args"][-2:] == [
        "dist/pr-merge-readiness-linux-x64.tar.gz",
        "dist/pr-merge-readiness-linux-arm64.tar.gz",
    ]
    assert env["GITHUB_SHA"] in calls[-1]["notes"]
    assert commit in calls[-1]["notes"]
    assert "codex/releases/v1.2.3" in calls[-1]["notes"]
    assert (tmp_path / "summary").read_text() == calls[-1]["notes"]


@pytest.mark.parametrize(
    "failure",
    [
        "version",
        "branch",
        "head",
        "dirty",
        "existing-tag",
        "existing-release-branch",
        "remote",
        "checksum",
        "missing",
    ],
)
def test_release_refuses_invalid_inputs_before_publishing(release, tmp_path, failure):
    source, remote, env, git, publish = release
    if failure == "version":
        env["RELEASE_VERSION"] = "v01.2.3"
    elif failure == "branch":
        env["GITHUB_REF"] = "refs/heads/feature"
    elif failure == "head":
        env["GITHUB_SHA"] = "0" * 40
    elif failure == "dirty":
        (source / "cli.py").write_text("modified\n")
    elif failure == "existing-tag":
        git("tag", "v1.2.3")
        git("push", "-q", "origin", "v1.2.3")
    elif failure == "existing-release-branch":
        git("push", "-q", "origin", "HEAD:refs/heads/codex/releases/v1.2.3")
    elif failure == "remote":
        git("remote", "set-url", "origin", str(tmp_path / "missing.git"))
    elif failure == "checksum":
        (tmp_path / "artifacts/binary-linux-arm64/pr-merge-readiness").write_text("corrupted")
    elif failure == "missing":
        (tmp_path / "artifacts/binary-linux-arm64/pr-merge-readiness").unlink()
    original = git("rev-parse", "HEAD")
    result = publish()
    assert result.returncode != 0
    assert git("rev-parse", "HEAD") == original
    assert git("--git-dir=" + str(remote), "rev-parse", "refs/heads/main") == original
    tags = git("--git-dir=" + str(remote), "tag", "--list")
    assert tags == ("v1.2.3" if failure == "existing-tag" else "")
    branches = git("--git-dir=" + str(remote), "for-each-ref", "--format=%(refname)", "refs/heads")
    assert branches.splitlines() == (
        ["refs/heads/codex/releases/v1.2.3", "refs/heads/main"]
        if failure == "existing-release-branch"
        else ["refs/heads/main"]
    )
    log = tmp_path / "gh.jsonl"
    if log.exists():
        assert all(
            json.loads(line)["args"][:2] == ["run", "download"]
            for line in log.read_text().splitlines()
        )


@pytest.mark.parametrize("rejected_ref", ["refs/heads/codex/releases/v1.2.3", "refs/tags/v1.2.3"])
def test_release_pushes_branch_and_tag_atomically(release, tmp_path, rejected_ref):
    _, remote, env, git, publish = release
    hook = remote / "hooks/update"
    hook.write_text(f'#!/bin/sh\n[ "$1" != "{rejected_ref}" ]\n')
    hook.chmod(0o755)
    result = publish()
    assert result.returncode != 0
    assert "atomic" in result.stderr
    assert git("--git-dir=" + str(remote), "show-ref").splitlines() == [
        env["GITHUB_SHA"] + " refs/heads/main"
    ]
    calls = [
        json.loads(line)["args"][:2] for line in (tmp_path / "gh.jsonl").read_text().splitlines()
    ]
    assert calls == [["run", "download"], ["run", "download"], ["auth", "setup-git"]]
