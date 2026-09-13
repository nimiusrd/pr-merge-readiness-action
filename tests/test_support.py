"""収集・判定・表示・公開テストで共有する観測とpolicyの例。"""

HEAD = "a" * 40
BASE = "b" * 40
MERGE = "c" * 40


def policy():
    return {
        "stale_change_review_days": 30,
        "minimum_approvals": 0,
        "require_resolved_threads": True,
        "required_checks": [{"kind": "check_run", "name": "Test", "app_id": 1}],
    }


def facts():
    return {
        "schema_version": 1,
        "observed_at": "2026-09-11T12:00:00+00:00",
        "repository": "example/project",
        "collection_errors": [],
        "stable": True,
        "pr": {
            "number": 1,
            "state": "OPEN",
            "draft": False,
            "head_sha": HEAD,
            "base_sha": BASE,
            "merge_sha": MERGE,
            "mergeable": "MERGEABLE",
            "merge_state": "CLEAN",
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
        "ci_history": [],
        "ci_definition_changes": [],
        "checks": [
            {
                "kind": "check_run",
                "name": "Test",
                "app_id": 1,
                "sha": HEAD,
                "id": 1,
                "status": "completed",
                "conclusion": "success",
            }
        ],
    }
