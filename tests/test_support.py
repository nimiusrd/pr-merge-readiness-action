"""収集・判定・表示・公開テストで共有する観測とpolicyの例。"""

HEAD = "a" * 40
BASE = "b" * 40
MERGE = "c" * 40


def pr_event(action="opened"):
    return {
        "action": action,
        "pull_request": {
            "number": 1,
            "head": {"sha": HEAD, "repo": {"id": 1}},
            "base": {"sha": BASE, "repo": {"id": 1}},
            "user": {"login": "contributor", "type": "User"},
        },
    }


def policy():
    return {
        "stale_change_review_days": 30,
        "minimum_approvals": 0,
        "require_resolved_threads": True,
    }


def facts():
    return {
        "schema_version": 2,
        "observed_at": "2026-09-11T12:00:00+00:00",
        "repository": "example/project",
        "collection_errors": [],
        "stable": True,
        "review_stable": True,
        "pr": {
            "number": 1,
            "state": "OPEN",
            "draft": False,
            "head_sha": HEAD,
            "base_sha": BASE,
            "mergeable": "MERGEABLE",
            "review_decision": None,
        },
        "change": {
            "additions": 10,
            "deletions": 2,
            "changed_files": 1,
            "types": {"MODIFIED": 1},
        },
        "files": [
            {
                "path": "whatever.rs",
                "changeType": "MODIFIED",
                "additions": 10,
                "deletions": 2,
            }
        ],
        "change_history": {
            "base_sha": BASE,
            "files": [
                {
                    "path": "whatever.rs",
                    "history_path": "whatever.rs",
                    "last_commit_sha": BASE,
                    "last_changed_at": "2026-09-01T12:00:00Z",
                }
            ],
        },
        "reviews": [],
        "unresolved_threads": 0,
        "ci_definition_changes": [],
    }
