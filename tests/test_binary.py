"""配布する実行ファイルを、Python/uv のない PATH と利用側 cwd で検証する。"""

import base64
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import pytest

from pr_merge_readiness.config import ACTION_REPOSITORY
from pr_merge_readiness.publish import DECISION_LABELS
from tests.test_collect import FixtureAPI as Reader
from tests.test_config import ROOT, config_text
from tests.test_publish import FixtureAPI as Writer
from tests.test_support import ACTION_SHA, BASE, pr_event


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
    # Git・uv・Python が PATH に無くても動き、利用側の Python 設定も参照しない。
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


@pytest.mark.parametrize("days", [1, 30, 99])
def test_binary_validates_config_without_python_or_consumer_imports(
    binary, tmp_path, isolated, days
):
    configuration = tmp_path / "config with spaces.toml"
    configuration.write_text(
        config_text().replace("stale_change_review_days = 30", f"stale_change_review_days = {days}")
    )
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


def test_binary_rejects_local_action_without_executing_git(binary, tmp_path, isolated):
    result = invoke(binary, tmp_path, isolated, "action", code=1)
    assert "local Actions are unsupported" in json.loads(result.stdout)["error"]


def test_binary_reads_config_over_http(binary, tmp_path, isolated):
    text = config_text()
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
                    "GITHUB_EVENT_NAME": "push",
                    "GITHUB_SHA": BASE,
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


def test_binary_observes_and_updates_labels_over_http(binary, tmp_path, isolated):
    reader, writer = Reader(), Writer()
    writer.pulls[1]["base"]["ref"] = reader.state["baseRefName"]
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            paths.append(self.path)
            path = urlsplit(self.path).path
            body = (
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.headers.get("Content-Length")
                else None
            )
            assert self.headers["Authorization"] == "Bearer test-token"
            if path == "/graphql":
                assert not {"reviewThreads", "reviewDecision", "mergeable", "isDraft"} & set(
                    body["query"].replace("(", " ").split()
                )
                pr = reader.state
                value = {"data": {"repository": {"pullRequest": pr}}}
            elif path == reader.prefix:
                value = {"default_branch": "trunk"}
            elif path == reader.prefix + "/git/ref/heads/trunk":
                value = {"object": {"sha": BASE}}
            elif "/contents/" in path:
                value = {
                    "type": "file",
                    "encoding": "base64",
                    "content": base64.b64encode(config_text().encode()).decode(),
                }
            elif "/commits?" in self.path:
                value = reader.request(self.path)
            elif path.endswith(("/files", "/reviews")):
                value = reader.pages(path.removeprefix(reader.prefix))
            else:
                value = writer.request(path, body, method=self.command)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        do_GET = do_POST = respond

        def log_message(self, *_):
            pass

    event = tmp_path / "event.json"
    event.write_text('{"inputs":{"pr-number":"1"}}')
    summary = tmp_path / "summary"
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}"
            result = invoke(
                binary,
                tmp_path,
                isolated,
                "action",
                env={
                    "GITHUB_REPOSITORY": reader.repository,
                    "GITHUB_API_URL": endpoint,
                    "GITHUB_GRAPHQL_URL": endpoint + "/graphql",
                    "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "GITHUB_EVENT_PATH": str(event),
                    "GITHUB_STEP_SUMMARY": str(summary),
                    "GH_TOKEN": "test-token",
                    "PMR_SOURCE_REF": ACTION_SHA,
                    "PMR_SOURCE_REPOSITORY": ACTION_REPOSITORY,
                },
            )
            assert json.loads(result.stdout) == {"pr": 1, "publication": "updated"}
            assert writer.names() == [DECISION_LABELS["SHADOW_CONDITIONS_MET"]]
            assert "SHADOW_CONDITIONS_MET" in summary.read_text()
            assert sum("/contents/" in path for path in paths) == 1
            assert not any("check-runs" in path or "/reviews" in path for path in paths)
        finally:
            server.shutdown()
            thread.join()
