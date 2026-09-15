"""単一writerのラベル更新と、次回実行での失敗回復を検証する。"""

import pytest
import copy
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import unquote
from pr_merge_readiness.publish import (
    DECISION_LABELS,
    LABEL_COLORS,
    LABEL_DESCRIPTIONS,
    MANAGED_LABELS,
    GitHub,
    PublishError,
    cleanup_closed,
    encoded_label,
    ensure_labels,
    publish_pr,
)
from tests.test_support import BASE, HEAD

WAITING = DECISION_LABELS["WAITING"]
READY = DECISION_LABELS["SHADOW_CONDITIONS_MET"]
UNKNOWN = DECISION_LABELS["INSUFFICIENT_DATA"]


def report(decision="WAITING", number=1):
    return {
        "decision": decision,
        "label_assessment": {"decision": decision, "conditions": []},
        "observations": {
            "repository": "example/project",
            "pr": {
                "number": number,
                "head_sha": HEAD,
                "base_sha": BASE,
                "base_ref": "main",
                "state": "OPEN",
                "draft": False,
            },
        },
    }


def pull(labels=(), state="open"):
    return {
        "head": {"sha": HEAD, "repo": {"id": 1, "fork": False}},
        "base": {"sha": BASE, "ref": "main", "repo": {"id": 1, "fork": False}},
        "user": {"login": "contributor", "type": "User"},
        "state": state,
        "draft": False,
        "labels": [{"name": name} for name in labels],
    }


class FixtureAPI:
    repository = "example/project"
    prefix = "/repos/example/project"

    def __init__(self, labels=()):
        self.pulls = {1: pull(labels)}
        self.definitions = set(MANAGED_LABELS)
        self.definition_metadata = {
            name: {
                "name": name,
                "color": LABEL_COLORS[name],
                "description": LABEL_DESCRIPTIONS[name],
            }
            for name in DECISION_LABELS.values()
        }
        self.calls = []
        self.fail = None
        self.closed = []

    def names(self, number=1):
        return [item["name"] for item in self.pulls[number]["labels"]]

    def request(self, path, body=None, method=None):
        verb = method or ("POST" if body is not None else "GET")
        self.calls.append((verb, path, body))
        if self.fail and self.fail(verb, path):
            raise PublishError("API failure")
        route = unquote(path.removeprefix(self.prefix)).split("/")
        if route[1] == "pulls":
            return copy.deepcopy(self.pulls[int(route[2])])
        if route[1] == "labels":
            if verb == "POST":
                self.definitions.add(body["name"])
                self.definition_metadata[body["name"]] = copy.deepcopy(body)
                return body
            name = "/".join(route[2:])
            if name not in self.definitions:
                raise PublishError("HTTP 404")
            if verb == "PATCH":
                self.definition_metadata[name].update(copy.deepcopy(body))
            return copy.deepcopy(self.definition_metadata[name])
        if route[1] == "issues":
            number = int(route[2])
            labels = self.pulls[number]["labels"]
            if verb == "POST":
                labels.extend(
                    ({"name": name} for name in body["labels"] if name not in self.names(number))
                )
            elif verb == "DELETE":
                name = "/".join(route[4:])
                labels[:] = [item for item in labels if item["name"] != name]
            else:
                raise AssertionError(verb)
            return None
        raise AssertionError(path)

    def pages(self, path):
        self.calls.append(("GET", path, None))
        return self.closed


def test_all_decisions_replace_only_managed_labels_and_are_idempotent():
    for decision, label in DECISION_LABELS.items():
        api = FixtureAPI(["enhancement", *MANAGED_LABELS])
        assert publish_pr(api, 1, report(decision)) == "updated"
        assert sorted(api.names()) == sorted(["enhancement", label])
        api.calls.clear()
        assert publish_pr(api, 1, report(decision)) == "unchanged"
        assert all((verb == "GET" for verb, _, _ in api.calls))
    assert all((len(text) <= 100 for text in LABEL_DESCRIPTIONS.values()))


@pytest.mark.parametrize(
    "key,value",
    {
        "head_sha": "d" * 40,
        "base_sha": "d" * 40,
        "base_ref": "release",
    }.items(),
)
def test_review_target_changes_are_marked_for_reobservation(key, value):
    api = FixtureAPI([READY])
    data = report("SHADOW_CONDITIONS_MET")
    data["observations"]["pr"][key] = value
    publish_pr(api, 1, data)
    assert api.names() == [UNKNOWN]


def test_collection_failure_without_pr_facts_expires_ready_label():
    api = FixtureAPI([READY])
    data = report("INSUFFICIENT_DATA")
    del data["observations"]["pr"]
    publish_pr(api, 1, data)
    assert api.names() == [UNKNOWN]


def test_invalid_decision_or_wrong_report_identity_never_writes():
    variants = [report("INVALID"), report(number=2), report()]
    variants[-1]["observations"]["repository"] = "other/repo"
    for data in variants:
        api = FixtureAPI([READY])
        with pytest.raises(PublishError):
            publish_pr(api, 1, data)
        assert api.names() == [READY]
        assert all((verb == "GET" for verb, _, _ in api.calls))


def test_closed_pr_removes_all_managed_labels():
    api = FixtureAPI(["bug", *MANAGED_LABELS])
    api.pulls[1]["state"] = "closed"
    publish_pr(api, 1, report())
    assert api.names() == ["bug"]


@pytest.mark.parametrize("decision", DECISION_LABELS)
@pytest.mark.parametrize("head_repository", [{"id": 2, "fork": True}, None])
def test_fork_or_deleted_source_removes_managed_labels_without_adding(decision, head_repository):
    api = FixtureAPI(["bug", *MANAGED_LABELS])
    api.pulls[1]["head"]["repo"] = head_repository
    assert publish_pr(api, 1, report(decision)) == "updated"
    assert api.names() == ["bug"]
    assert all(verb in {"GET", "DELETE"} for verb, _, _ in api.calls)
    api.calls.clear()
    assert publish_pr(api, 1, report(decision)) == "unchanged"
    assert all(verb == "GET" for verb, _, _ in api.calls)


def test_same_repository_pr_is_labeled_even_when_repository_is_a_fork():
    api = FixtureAPI()
    api.pulls[1]["head"]["repo"]["fork"] = True
    api.pulls[1]["base"]["repo"]["fork"] = True
    assert publish_pr(api, 1, report()) == "updated"
    assert api.names() == [WAITING]


@pytest.mark.parametrize("decision", DECISION_LABELS)
def test_dependabot_pr_removes_managed_labels_without_adding(decision):
    api = FixtureAPI(["dependencies", *MANAGED_LABELS])
    api.pulls[1]["user"] = {"login": "dependabot[bot]", "type": "Bot"}
    assert publish_pr(api, 1, report(decision)) == "updated"
    assert api.names() == ["dependencies"]
    assert all(verb in {"GET", "DELETE"} for verb, _, _ in api.calls)
    api.calls.clear()
    assert publish_pr(api, 1, report(decision)) == "unchanged"
    assert all(verb == "GET" for verb, _, _ in api.calls)


@pytest.mark.parametrize(
    "author",
    [{"login": "contributor", "type": "User"}, {"login": "renovate[bot]", "type": "Bot"}],
)
def test_dependencies_label_does_not_exclude_other_authors(author):
    api = FixtureAPI(["dependencies"])
    api.pulls[1]["user"] = author
    assert publish_pr(api, 1, report()) == "updated"
    assert api.names() == ["dependencies", WAITING]


def test_failed_add_preserves_old_label_then_next_run_repairs():
    api = FixtureAPI([READY])
    api.fail = lambda verb, path: verb == "POST"
    with pytest.raises(PublishError):
        publish_pr(api, 1, report())
    assert api.names() == [READY]
    api.fail = None
    publish_pr(api, 1, report())
    assert api.names() == [WAITING]


def test_failed_delete_leaves_new_label_then_next_run_repairs():
    api = FixtureAPI([READY, "bug"])
    api.fail = lambda verb, path: verb == "DELETE"
    with pytest.raises(PublishError):
        publish_pr(api, 1, report())
    assert sorted(api.names()) == sorted([READY, WAITING, "bug"])
    api.fail = None
    publish_pr(api, 1, report())
    assert sorted(api.names()) == sorted([WAITING, "bug"])


def test_cleanup_deduplicates_closed_prs_and_ignores_issues_and_reopened_prs():
    api = FixtureAPI()
    api.pulls = {1: pull([READY, "bug"], "closed"), 2: pull([WAITING])}
    api.closed = [
        {"number": 1, "pull_request": {}},
        {"number": 2, "pull_request": {}},
        {"number": 3},
    ]
    cleanup_closed(api)
    assert api.names(1) == ["bug"]
    assert api.names(2) == [WAITING]
    assert sum(("/pulls/1" in path for _, path, _ in api.calls)) == 1


def test_definitions_created_once_then_reused():
    api = FixtureAPI()
    api.definitions.clear()
    ensure_labels(api)
    assert api.definitions == set(DECISION_LABELS.values())
    ensure_labels(api)
    assert sum((verb == "POST" for verb, _, _ in api.calls)) == 4


def test_http_delete_encodes_japanese_label_and_sends_no_body():
    with patch("pr_merge_readiness.publish.urlopen") as opened:
        opened.return_value.__enter__.return_value.read.return_value = b""
        api = GitHub("example/project")
        path = f"{api.prefix}/issues/1/labels/{encoded_label(WAITING)}"
        assert api.request(path, method="DELETE") is None
        req = opened.call_args.args[0]
        assert req.method == "DELETE"
        assert req.data is None
        assert "待ち" not in req.full_url


def test_http_failure_is_not_success():
    error = HTTPError("https://example.com", 403, "Forbidden", {}, None)
    with patch("pr_merge_readiness.publish.urlopen", side_effect=error):
        with pytest.raises(PublishError, match="HTTP 403"):
            GitHub("example/project").request("/repos/example/project/labels", {"name": WAITING})


def test_closed_listing_reads_all_pages_and_rejects_truncation():
    api = GitHub("example/project")
    with patch.object(api, "request", side_effect=[[{}] * 100, [{"number": 101}]]) as request:
        assert len(api.pages("/issues?state=closed")) == 101
        assert "&per_page=100&page=2" in request.call_args.args[0]
    with (
        patch("pr_merge_readiness.publish.MAX_PAGES", 2),
        patch.object(api, "request", return_value=[{}] * 100),
    ):
        with pytest.raises(PublishError, match="pagination limit"):
            api.pages("/issues?state=closed")


def test_manual_sync_removes_retired_ci_labels_without_recreating_them():
    api = FixtureAPI(["shadow/CI・レビュー待ち", "shadow/要マージ判断", "bug"])
    api.definitions.clear()
    ensure_labels(api)
    publish_pr(api, 1, report("SHADOW_CONDITIONS_MET"))
    assert api.names() == ["bug", "shadow/レビュー条件充足"]
    assert api.definitions == set(DECISION_LABELS.values())


def test_existing_label_descriptions_are_updated_once():
    api = FixtureAPI()
    name = DECISION_LABELS["HUMAN_REVIEW_REQUIRED"]
    api.definition_metadata[name]["description"] = "競合・CI失敗などに対応が必要"
    ensure_labels(api)
    assert api.definition_metadata[name]["description"] == LABEL_DESCRIPTIONS[name]
    ensure_labels(api)
    assert sum(verb == "PATCH" for verb, _, _ in api.calls) == 1


def test_server_normalized_color_does_not_cause_repeated_updates():
    api = FixtureAPI()
    for definition in api.definition_metadata.values():
        definition["color"] = definition["color"].lower()
    ensure_labels(api)
    assert all(verb == "GET" for verb, _, _ in api.calls)


@pytest.mark.parametrize("decision", ["WAITING", "HUMAN_REVIEW_REQUIRED", "INSUFFICIENT_DATA"])
def test_labels_use_review_assessment_even_when_reference_assessment_differs(decision):
    api = FixtureAPI([UNKNOWN])
    api.pulls[1]["draft"] = True
    data = report("SHADOW_CONDITIONS_MET")
    data["decision"] = decision
    # 観測後のDraft・open/closed状態の変更は、レビュー対象が同じならラベルに影響しない。
    data["observations"]["pr"].update(state="CLOSED", draft=False)
    publish_pr(api, 1, data)
    assert api.names() == [READY]


@pytest.mark.parametrize("assessment", [None, {}, {"decision": []}, {"decision": "INVALID"}])
def test_invalid_label_assessment_never_falls_back_to_overall_decision(assessment):
    api = FixtureAPI([WAITING])
    data = report("SHADOW_CONDITIONS_MET")
    data["label_assessment"] = assessment
    with pytest.raises(PublishError):
        publish_pr(api, 1, data)
    assert all(verb == "GET" for verb, _, _ in api.calls)
    assert api.names() == [WAITING]
