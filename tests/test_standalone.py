"""共通 workflow・利用側 checkout なしで、任意の repository を観測・公開する。"""

import base64
import json
import os
from unittest.mock import patch

import pytest

from pr_merge_readiness import runtime
from pr_merge_readiness.config import ACTION_REPOSITORY
from tests.test_collect import FixtureAPI as Reader
from tests.test_config import ROOT, config
from tests.test_publish_checks import FixtureAPI as Writer
from tests.test_support import BASE, HEAD


@pytest.mark.parametrize("repository", ("sample-org/service", "another-owner/library"))
def test_direct_action_observes_and_publishes_in_callers_repository(
    tmp_path, monkeypatch, repository
):
    # 空の作業ディレクトリ。利用側ソースも共通 workflow の出所情報も与えない。
    monkeypatch.chdir(tmp_path)
    reader, writer = Reader(), Writer()
    for api in (reader, writer):
        api.repository = repository
        api.prefix = "/repos/" + repository
    writer.prs[1]["base"]["ref"] = reader.state["baseRefName"]
    writer.prs[1]["updated_at"] = reader.state["updatedAt"]
    settings = (ROOT / "examples/minimal.toml").read_bytes()
    source_ref = config()["action_ref"]
    config_requests = []
    request, pages = reader.request, reader.pages

    def read(path, body=None):
        assert path.startswith(reader.prefix)
        if path.startswith(reader.prefix + "/commits?"):
            return request(path, body)
        config_requests.append(path)
        if path == reader.prefix:
            return {"default_branch": "trunk"}
        if path == reader.prefix + "/git/ref/heads/trunk":
            return {"object": {"sha": BASE}}
        assert path == reader.prefix + f"/contents/settings/readiness.toml?ref={BASE}"
        return {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(settings).decode(),
        }

    def read_pages(path, key=None):
        records = pages(path, key)
        if path.startswith(f"/commits/{HEAD}/check-runs"):
            for record in records:
                record.update(name="test", app={"id": 15368})
        return records

    monkeypatch.setattr(reader, "request", read)
    monkeypatch.setattr(reader, "pages", read_pages)
    event = tmp_path / "event.json"
    event.write_text('{"inputs":{"pr-number":"1"}}')
    environment = {
        "GH_TOKEN": "test-token",
        "GITHUB_REPOSITORY": repository,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_RUN_ID": "42",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "PMR_SOURCE_REF": source_ref,
        "PMR_SOURCE_REPOSITORY": ACTION_REPOSITORY,
        "PMR_ACTION_REF": source_ref,
        "PMR_CONFIG_PATH": "settings/readiness.toml",
        "PMR_OPERATION": "prepare",
    }
    with (
        patch.dict(os.environ, environment, clear=True),
        patch.object(runtime, "GitHub", return_value=reader) as reader_factory,
        patch.object(runtime.publish, "GitHub", return_value=writer) as writer_factory,
    ):
        assert runtime.run_action() == 0
        prepared = dict(
            line.split("=", 1) for line in (tmp_path / "outputs").read_text().splitlines()
        )
        assert prepared == {
            "operation": "observe",
            "config-sha": BASE,
            "pr-number": "1",
            "checks": "true",
            "labels": "false",
        }
        os.environ.update(
            PMR_OPERATION=prepared["operation"],
            PMR_CONFIG_SHA=prepared["config-sha"],
            PMR_PR_NUMBER=prepared["pr-number"],
            PMR_REPORT_DIR="reports",
            PMR_ARTIFACT_NAME="pr-merge-readiness-42-2",
        )
        assert runtime.run_action() == 0
        report = json.loads((tmp_path / "reports/pr-1.json").read_text())
        manifest = json.loads((tmp_path / "reports/manifest.json").read_text())
        assert report["observations"]["repository"] == manifest["repository"] == repository
        assert report["decision"] == "SHADOW_CONDITIONS_MET"
        assert report["provenance"] == manifest["provenance"]
        assert report["provenance"] == {
            "evaluator": {
                "repository": ACTION_REPOSITORY,
                "sha": source_ref,
                "path": "pr_merge_readiness",
            },
            "config": {"repository": repository, "sha": BASE, "path": "settings/readiness.toml"},
            "workflow": None,
        }
        outputs = dict(
            line.split("=", 1) for line in (tmp_path / "outputs").read_text().splitlines()
        )
        assert outputs["config-sha"] == BASE
        assert "SHADOW_CONDITIONS_MET" in (tmp_path / "summary").read_text()

        # 次の job は観測時の設定 SHA と artifact だけを受け取る。
        os.environ.update(PMR_OPERATION="publish-checks", PMR_CONFIG_SHA=outputs["config-sha"])
        del os.environ["PMR_PR_NUMBER"]
        assert runtime.run_action() == 0
        assert len(writer.writes) == 1
        path, body, _ = writer.writes[0]
        assert path == f"/repos/{repository}/check-runs"
        assert body["conclusion"] == "neutral"
        assert body["output"]["title"] == "観測済み：SHADOW_CONDITIONS_MET"
        assert body["details_url"] == f"https://github.com/{repository}/actions/runs/42/attempts/2"
        reader_factory.assert_called_with(repository)
        writer_factory.assert_called_once_with(repository)
    assert config_requests == [
        reader.prefix,
        reader.prefix + "/git/ref/heads/trunk",
        reader.prefix + f"/contents/settings/readiness.toml?ref={BASE}",
        reader.prefix + f"/contents/settings/readiness.toml?ref={BASE}",
        reader.prefix + f"/contents/settings/readiness.toml?ref={BASE}",
    ]
