"""配布する実行ファイルを、Python/uv のない PATH と利用側 cwd で検証する。"""

import base64
import json
import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from pr_merge_readiness.artifacts import provenance
from pr_merge_readiness.config import ACTION_REPOSITORY
from pr_merge_readiness.evaluate import assess
from tests.test_config import ROOT, config_text
from tests.test_support import ACTION_SHA, BASE, facts, policy, pr_event


@pytest.fixture
def binary():
    path = os.environ.get("PMR_TEST_BINARY")
    if not path:
        pytest.skip("PMR_TEST_BINARY を指定するビルド検証で実行する")
    executable = Path(path).resolve()
    assert executable.is_file() and os.access(executable, os.X_OK)
    return executable


@pytest.fixture
def isolated(tmp_path):
    for name in ("json", "tomllib", "sitecustomize", "pr_merge_readiness"):
        (tmp_path / f"{name}.py").write_text('raise RuntimeError("consumer code executed")\n')
    (tmp_path / "pyproject.toml").write_text("invalid TOML = [")
    (tmp_path / "uv.toml").write_text("invalid TOML = [")
    (tmp_path / ".python-version").write_text("9.99\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "git").symlink_to(shutil.which("git"))
    # uv/Python が PATH に無くても動き、利用側の Python 設定も参照しない。
    return {
        "PATH": str(tools),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(tmp_path),
        "PYTHONHOME": str(tmp_path / "missing-python"),
        "UV_CONFIG_FILE": str(tmp_path / "uv.toml"),
        "UV_PROJECT": str(tmp_path),
        "UV_PYTHON": str(tmp_path / "missing-python"),
        "UV_OFFLINE": "1",
        "VIRTUAL_ENV": str(tmp_path / "missing-venv"),
    }


def invoke(binary, tmp_path, isolated, *args, env=None, code=0):
    result = subprocess.run(
        [str(binary), *args],
        cwd=tmp_path,
        env={**isolated, **(env or {})},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == code, result.stdout + result.stderr
    return result


@pytest.mark.parametrize("mode", ["auto", "manual", "off"])
def test_binary_validates_config_without_python_or_consumer_imports(
    binary, tmp_path, isolated, mode
):
    configuration = tmp_path / "config with spaces.toml"
    configuration.write_text(config_text().replace('labels = "manual"', f'labels = "{mode}"'))
    result = invoke(binary, tmp_path, isolated, "validate-config", "--config", str(configuration))
    assert json.loads(result.stdout) == {"valid": True}
    configuration.write_text("version = 1\n")
    result = invoke(
        binary, tmp_path, isolated, "validate-config", "--config", str(configuration), code=1
    )
    assert "error" in json.loads(result.stdout)


def test_binary_composite_launcher_preserves_arguments(binary, tmp_path, isolated):
    configuration = tmp_path / "config with spaces.toml"
    configuration.write_text(config_text())
    result = subprocess.run(
        ["bash", str(ROOT / "run-binary.sh"), "validate-config", "--config", str(configuration)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"valid": True}


@pytest.mark.parametrize("excluded", ["fork", "dependabot"])
def test_binary_skips_excluded_pr_without_api_or_python(binary, tmp_path, isolated, excluded):
    event = pr_event()
    if excluded == "fork":
        event["pull_request"]["head"]["repo"]["id"] = 2
    else:
        event["pull_request"]["user"]["login"] = "dependabot[bot]"
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    output = tmp_path / "output"
    env = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_OUTPUT": str(output),
        "PMR_SOURCE_REF": ACTION_SHA,
        "PMR_SOURCE_REPOSITORY": ACTION_REPOSITORY,
    }
    invoke(binary, tmp_path, isolated, "action", env=env)
    assert output.read_text() == "operation=skip\n"
    invoke(binary, tmp_path, isolated, "action", env={**env, "PMR_SOURCE_REF": "main"}, code=1)


@pytest.mark.parametrize("legacy_config", [True, False])
def test_binary_reads_config_over_http(binary, tmp_path, isolated, legacy_config):
    text = config_text()
    if not legacy_config:
        text = "\n".join(line for line in text.splitlines() if not line.startswith("action_ref ="))
    body = json.dumps(
        {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(text.encode()).decode(),
        }
    ).encode()
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = invoke(
                binary,
                tmp_path,
                isolated,
                "action",
                env={
                    "GITHUB_REPOSITORY": "example/project",
                    "GITHUB_API_URL": f"http://127.0.0.1:{server.server_port}",
                    "GH_TOKEN": "test-token",
                    "PMR_OPERATION": "validate-config",
                    "PMR_CONFIG_SHA": BASE,
                    "PMR_SOURCE_REF": ACTION_SHA,
                    "PMR_SOURCE_REPOSITORY": ACTION_REPOSITORY,
                },
            )
            assert json.loads(result.stdout) == {"valid": True, "config_sha": BASE}
            assert paths == [
                f"/repos/example/project/contents/.github/pr-merge-readiness.toml?ref={BASE}"
            ]
        finally:
            server.shutdown()
            thread.join()


def test_binary_replays_archived_evaluator_without_python(binary, tmp_path, isolated):
    source = tmp_path / "trusted-source"
    package = source / "pr_merge_readiness"
    package.mkdir(parents=True)
    for name in ("__init__.py", "contracts.py", "evaluate.py"):
        text = (ROOT / "pr_merge_readiness" / name).read_text()
        # バイナリ内の評価器では得られない結果を作り、Git のコード使用を確認する。
        text = text.replace("PR and reviews must agree in both samples", "ARCHIVED_EVALUATOR")
        (package / name).write_text(text)

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(source), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("add", "pr_merge_readiness")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "Trusted evaluator",
    )
    trusted = git("rev-parse", "HEAD")
    report = {
        **assess(facts(), policy()),
        "provenance": provenance("example/project", trusted, BASE, ".github/config.toml"),
    }
    report["conditions"][0]["detail"] = "ARCHIVED_EVALUATOR"
    report_path = tmp_path / "pr-1.json"
    report_path.write_text(json.dumps(report))
    (package / "evaluate.py").write_text('raise RuntimeError("untrusted working tree")\n')
    args = [
        "replay",
        "--report",
        str(report_path),
        "--source-dir",
        str(source),
        "--source-repository",
        ACTION_REPOSITORY,
        "--action-sha",
        trusted,
    ]
    result = invoke(binary, tmp_path, isolated, *args)
    assert json.loads(result.stdout)["matches"] is True
    report["decision"] = "WAITING"
    report_path.write_text(json.dumps(report))
    result = invoke(binary, tmp_path, isolated, *args, code=1)
    assert json.loads(result.stdout)["matches"] is False
