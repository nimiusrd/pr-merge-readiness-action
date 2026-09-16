"""run全体の履歴予算を共有し、判定結果をメモリ上で返す。"""

from typing import Any

from .collect import ChangeHistoryCollector, GitHub, collect, targets
from .contracts import Assessment, Policy
from .evaluate import assess


def observe(
    api: GitHub,
    policy: Policy,
    event: dict[str, Any],
    number: int | None,
    *,
    expected_head: str | None = None,
) -> list[tuple[int, Assessment]]:
    collector = ChangeHistoryCollector(api)
    return [
        (
            target,
            assess(
                collect(api, target, history_collector=collector, expected_head=expected_head),
                policy,
            ),
        )
        for target in targets(api, event, number)
    ]
