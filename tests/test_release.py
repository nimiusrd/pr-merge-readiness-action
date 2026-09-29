"""ローカル bare remote と偽の GitHub CLI で、タグの検証と公開の境界を確認する。"""

import hashlib
import json
import os
import subprocess
import sys

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
        "GITHUB_REF": "refs/tags/v1.2.3",
        "GITHUB_RUN_ID": "123",
        "GITHUB_REPOSITORY": "example/action",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "RELEASE_VERSION": "v1.2.3",
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
    artifact = source / "dist/linux-x64"
    artifact.mkdir(parents=True)
    contents = b"compiled fixture linux-x64"
    binary = artifact / "pr-merge-readiness"
    binary.write_bytes(contents)
    binary.chmod(0o755)
    (artifact / "SHA256SUMS").write_text(
        hashlib.sha256(contents).hexdigest() + "  pr-merge-readiness\n"
    )
    git("add", "-f", "dist")
    git("commit", "-qm", "Prepare release")
    env["GITHUB_SHA"] = git("rev-parse", "HEAD")
    git("tag", "v1.2.3")
    git("init", "--bare", "-q", str(remote))
    git("remote", "add", "origin", str(remote))
    git("push", "-q", "origin", "main", "v1.2.3")
    tools = tmp_path / "tools"
    tools.mkdir()
    gh = tools / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, tarfile\n"
        "args = sys.argv[1:]\n"
        "assert args[:2] == ['release', 'create']\n"
        "record = {'args': args}\n"
        "record['notes'] = pathlib.Path(args[args.index('--notes-file') + 1]).read_text()\n"
        "with tarfile.open(args[-1]) as archive:\n"
        "    record['members'] = [\n"
        "        [m.name, m.mode, archive.extractfile(m).read().decode()]\n"
        "        for m in archive.getmembers()]\n"
        "with open(os.environ['TEST_GH_LOG'], 'a') as log:\n"
        "    log.write(json.dumps(record) + '\\n')\n"
        "if os.environ.get('TEST_RELEASE_FAILURE'):\n"
        "    sys.exit(1)\n"
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


@pytest.mark.parametrize("annotated", [False, True])
@pytest.mark.parametrize("main_advanced", [False, True])
def test_release_publishes_tagged_binary_without_changing_refs(
    release, tmp_path, annotated, main_advanced
):
    source, remote, env, git, publish = release
    if annotated:
        git("tag", "-fa", "v1.2.3", "-m", "Release")
        git("push", "-q", "--force", "origin", "refs/tags/v1.2.3")
    if main_advanced:
        next_main = git("commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "Next main")
        git("push", "-q", "origin", next_main + ":refs/heads/main")
    git("checkout", "--detach", env["GITHUB_SHA"])
    refs = git("--git-dir=" + str(remote), "show-ref")
    result = publish()
    assert result.returncode == 0, result.stdout + result.stderr
    assert git("--git-dir=" + str(remote), "show-ref") == refs
    assert git("rev-parse", "HEAD") == env["GITHUB_SHA"]
    assert git("status", "--porcelain") == ""
    calls = [json.loads(line) for line in (tmp_path / "gh.jsonl").read_text().splitlines()]
    assert len(calls) == 1
    call = calls[0]
    assert call["args"][:3] == ["release", "create", "v1.2.3"]
    assert "--verify-tag" in call["args"]
    assert call["args"][-1].endswith("/pr-merge-readiness-linux-x64.tar.gz")
    assert call["members"] == [
        ["pr-merge-readiness", 0o755, "compiled fixture linux-x64"],
        ["SHA256SUMS", 0o644, (source / "dist/linux-x64/SHA256SUMS").read_text()],
    ]
    assert "配布用コミット: " + env["GITHUB_SHA"] in call["notes"]
    assert "検証 run: https://github.com/example/action/actions/runs/123" in call["notes"]
    assert (tmp_path / "summary").read_text() == call["notes"]


@pytest.mark.parametrize(
    "failure",
    [
        "version",
        "branch",
        "tag-name",
        "head",
        "dirty",
        "missing-tag",
        "moved-tag",
        "not-on-main",
        "remote",
        "checksum",
        "missing",
        "arm64",
        "mode",
        "symlink",
    ],
)
def test_release_refuses_invalid_inputs_without_changing_refs(release, tmp_path, failure):
    source, remote, env, git, publish = release
    if failure == "version":
        env["RELEASE_VERSION"] = "v01.2.3"
        env["GITHUB_REF"] = "refs/tags/v01.2.3"
    elif failure == "branch":
        env["GITHUB_REF"] = "refs/heads/main"
    elif failure == "tag-name":
        env["GITHUB_REF"] = "refs/tags/v1.2.4"
    elif failure == "head":
        env["GITHUB_SHA"] = "0" * 40
    elif failure == "dirty":
        (source / "cli.py").write_text("modified\n")
    elif failure == "missing-tag":
        git("push", "origin", ":refs/tags/v1.2.3")
    elif failure == "moved-tag":
        git("push", "--force", "origin", "HEAD^:refs/tags/v1.2.3")
    elif failure == "not-on-main":
        git("push", "--force", "origin", "HEAD^:refs/heads/main")
    elif failure == "remote":
        git("remote", "set-url", "origin", str(tmp_path / "missing.git"))
    else:
        binary = source / "dist/linux-x64/pr-merge-readiness"
        if failure == "checksum":
            binary.write_text("corrupted")
        elif failure == "missing":
            binary.unlink()
        elif failure == "arm64":
            previous = source / "dist/linux-arm64"
            previous.mkdir()
            (previous / "pr-merge-readiness").write_bytes(b"previous binary")
        elif failure == "mode":
            binary.chmod(0o644)
        elif failure == "symlink":
            binary.unlink()
            binary.symlink_to("../../cli.py")
        git("add", "-f", "dist")
        git("commit", "-qm", "Invalid distribution")
        git("tag", "-f", "v1.2.3")
        git("push", "--force", "origin", "main", "refs/tags/v1.2.3")
        env["GITHUB_SHA"] = git("rev-parse", "HEAD")
    refs = git("--git-dir=" + str(remote), "show-ref")
    original = git("rev-parse", "HEAD")
    result = publish()
    assert result.returncode != 0
    assert git("rev-parse", "HEAD") == original
    assert git("--git-dir=" + str(remote), "show-ref") == refs
    assert not (tmp_path / "gh.jsonl").exists()
    assert not (tmp_path / "summary").exists()


def test_release_creation_failure_preserves_refs_and_can_retry(release, tmp_path):
    _, remote, env, git, publish = release
    refs = git("--git-dir=" + str(remote), "show-ref")
    env["TEST_RELEASE_FAILURE"] = "1"
    assert publish().returncode != 0
    assert git("--git-dir=" + str(remote), "show-ref") == refs
    assert not (tmp_path / "summary").exists()
    del env["TEST_RELEASE_FAILURE"]
    result = publish()
    assert result.returncode == 0, result.stdout + result.stderr
    assert git("--git-dir=" + str(remote), "show-ref") == refs
    calls = [json.loads(line) for line in (tmp_path / "gh.jsonl").read_text().splitlines()]
    assert calls[0]["members"] == calls[1]["members"]
