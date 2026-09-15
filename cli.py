#!/usr/bin/env python3
"""自身と同じ checkout のパッケージだけを使う CLI エントリポイント。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pr_merge_readiness.cli import main
from pr_merge_readiness.replay_worker import main as replay_worker

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "_replay-worker":
        raise SystemExit(replay_worker(sys.argv[2]))
    raise SystemExit(main())
