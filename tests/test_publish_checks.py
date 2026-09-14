"""Checkの現在SHA・公開順・権限境界を、実APIを書き換えず検証する。"""

import pytest
from copy import deepcopy
from unittest.mock import patch
from pr_merge_readiness.evaluate import assess
from pr_merge_readiness.publish import PublishError
from pr_merge_readiness.publish_checks import (
    CHECK_PREFIX,
    managed_checks,
    publish_report,
)
from tests.test_support import BASE, HEAD, MERGE, facts, policy

AT = "2026-09-11T12:00:00+00:00"
LATER = "2026-09-11T13:00:00+00:00"
URL = "https://github.com/example/project/actions/runs/42/attempts/1"
ARTIFACT = "pr-merge-readiness-42-1"


def report(number=1, at=AT):
    data = facts()
    data["observed_at"] = at
    data["pr"].update(number=number, base_ref="main", updated_at=AT)
    return assess(data, policy())


class FixtureAPI:
    repository = "example/project"
    prefix = "/repos/example/project"

    def __init__(self):
        self.prs = {
            n: {
                "number": n,
                "head": {"sha": HEAD},
                "base": {"sha": BASE, "ref": "main"},
                "state": "open",
                "draft": False,
                "updated_at": AT,
                "merged_at": None,
                "merge_commit_sha": MERGE,
            }
            for n in (1, 2)
        }
        self.checks = []
        self.writes = []
        self.drift = None
        self.reads = 0
        self.failure = None

    def pages(self, path):
        assert path == "/pulls?state=open"
        return deepcopy(list(self.prs.values()))

    def request(self, path, body=None, method=None):
        if self.failure:
            raise PublishError(self.failure)
        if body is not None:
            self.writes.append((path, deepcopy(body), method))
            if method == "PATCH":
                record = next((r for r in self.checks if r["id"] == int(path.rsplit("/", 1)[1])))
                record.update(deepcopy(body))
            else:
                self.checks.append(
                    {**deepcopy(body), "id": len(self.checks) + 1, "app": {"id": 15368}}
                )
            return {}
        if "/pulls/" in path:
            self.reads += 1
            pr = self.prs[int(path.rsplit("/", 1)[1])]
            if self.drift and self.reads == 2:
                pr.update(self.drift)
            return deepcopy(pr)
        if "/commits/" in path:
            head = path.split("/commits/")[1].split("/")[0]
            records = [r for r in self.checks if r["head_sha"] == head]
            return {"total_count": len(records), "check_runs": deepcopy(records)}
        raise AssertionError(path)


@pytest.mark.parametrize(
    "decision", ("SHADOW_CONDITIONS_MET", "WAITING", "HUMAN_REVIEW_REQUIRED", "INSUFFICIENT_DATA")
)
def test_four_decisions_are_neutral_and_link_to_exact_artifact(decision):
    api = FixtureAPI()
    value = report()
    value["decision"] = decision
    original = deepcopy(value)
    assert publish_report(api, 1, value, URL, ARTIFACT) == "published"
    body = api.writes[0][1]
    assert (body["head_sha"], body["status"], body["conclusion"]) == (HEAD, "completed", "neutral")
    assert decision in body["output"]["title"]
    assert body["details_url"] == URL
    for text in (AT, HEAD, BASE, ARTIFACT, value["policy_sha256"], "pr-1.json"):
        assert text in body["output"]["summary"]
    assert value == original


def test_older_reports_do_not_replace_newer_observation():
    api = FixtureAPI()
    publish_report(api, 1, report(at=LATER), URL, ARTIFACT)
    publish_report(api, 1, report(), URL, ARTIFACT)
    assert len(api.writes) == 1
    assert "SHADOW_CONDITIONS_MET" in api.checks[0]["output"]["title"]


def test_new_head_never_inherits_old_conditions_met():
    api = FixtureAPI()
    publish_report(api, 1, report(), URL, ARTIFACT)
    api.prs[1]["head"]["sha"] = "d" * 40
    publish_report(api, 1, report(), URL, ARTIFACT)
    body = api.writes[-1][1]
    assert body["head_sha"] == "d" * 40
    assert "古いSHA" in body["output"]["title"]
    assert "SHADOW_CONDITIONS_MET" not in body["output"]["title"]


@pytest.mark.parametrize(
    "change",
    (
        {"base": {"sha": "d" * 40, "ref": "other"}},
        {"draft": True},
        {"updated_at": LATER},
        {"state": "closed"},
    ),
)
def test_base_draft_updated_at_and_closed_changes_require_reobservation(change):
    api = FixtureAPI()
    api.prs[1].update(change)
    publish_report(api, 1, report(), URL, ARTIFACT)
    assert "再観測" in api.writes[0][1]["output"]["title"]


def test_same_timestamp_report_expires_existing_ready_check_after_base_change():
    api = FixtureAPI()
    publish_report(api, 1, report(), URL, ARTIFACT)
    api.prs[1]["base"]["sha"] = "d" * 40
    publish_report(api, 1, report(), URL, ARTIFACT)
    assert len(api.checks) == 1
    assert "再観測" in api.checks[0]["output"]["title"]


def test_terminal_report_is_published_as_record():
    api = FixtureAPI()
    api.prs[1].update(state="closed", merged_at=LATER)
    value = report()
    value["observations"]["pr"]["state"] = "MERGED"
    value["decision"] = "WAITING"
    publish_report(api, 1, value, URL, ARTIFACT)
    assert "観測済み" in api.writes[0][1]["output"]["title"]


def test_same_head_two_prs_get_separate_checks():
    api = FixtureAPI()
    for number in (1, 2):
        publish_report(api, number, report(number), URL, ARTIFACT)
    assert {c["name"] for c in api.checks} == {CHECK_PREFIX + "1", CHECK_PREFIX + "2"}


def test_foreign_check_is_not_modified():
    api = FixtureAPI()
    publish_report(api, 1, report(), URL, ARTIFACT)
    foreign = deepcopy(api.checks[0])
    api.checks[0]["app"]["id"] = 7
    publish_report(api, 1, report(at=LATER), URL, ARTIFACT)
    assert len(api.checks) == 2
    assert api.checks[0]["external_id"] == foreign["external_id"]


def test_invalid_identity_and_read_failures_never_write():
    for change in ({"repository": "other/project"}, {"pr": {"number": 3}}):
        api = FixtureAPI()
        value = report()
        value["observations"].update(change)
        with pytest.raises(PublishError):
            publish_report(api, 1, value, URL, ARTIFACT)
        assert not api.writes
    api = FixtureAPI()
    api.failure = "API GET /pulls/1: HTTP 403"
    with pytest.raises(PublishError):
        publish_report(api, 1, report(), URL, ARTIFACT)
    assert not api.writes


def test_last_moment_pr_change_never_writes():
    api = FixtureAPI()
    api.drift = {"head": {"sha": "d" * 40}}
    with pytest.raises(PublishError, match="changed before"):
        publish_report(api, 1, report(), URL, ARTIFACT)
    assert not api.writes


def test_review_text_is_escaped():
    api = FixtureAPI()
    value = report()
    value["conditions"][0]["detail"] = "<script>@someone</script>"
    publish_report(api, 1, value, URL, ARTIFACT)
    summary = api.writes[0][1]["output"]["summary"]
    assert "<script>" not in summary
    assert "&lt;script&gt;" in summary


def test_check_pagination_cannot_return_truncated_success():
    api = FixtureAPI()
    with patch.object(api, "request", return_value={"total_count": 2, "check_runs": []}):
        with pytest.raises(PublishError, match="count mismatch"):
            managed_checks(api, 1, HEAD)
