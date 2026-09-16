"""複数PRの履歴予算と、観測失敗・対象なしを検証する。"""

from unittest.mock import patch

import pytest

from pr_merge_readiness.observe import observe
from tests.test_change_history import AT, MultiPRHistoryAPI
from tests.test_collect import FixtureAPI
from tests.test_support import policy


@pytest.mark.parametrize("shared", [True, False])
def test_ten_prs_share_one_hundred_request_budget_and_cache(shared):
    api = MultiPRHistoryAPI(shared_paths=shared)
    with patch("pr_merge_readiness.collect.datetime") as clock:
        clock.now.return_value = AT
        reports = observe(api, policy(), {}, None)
    assert len(api.history_requests) == 100
    assert len(reports) == 10
    assert reports[-1][1]["decision"] == (
        "HUMAN_REVIEW_REQUIRED" if shared else "INSUFFICIENT_DATA"
    )
    if not shared:
        assert "100/100 used, 100 needed" in reports[-1][1]["observations"]["collection_errors"][0]


def test_no_targets_and_listing_failure_are_distinct():
    api = FixtureAPI()
    assert observe(api, policy(), {}, None) == []
    with patch.object(api, "pages", side_effect=ValueError("API 403")):
        with pytest.raises(ValueError, match="API 403"):
            observe(api, policy(), {}, None)
