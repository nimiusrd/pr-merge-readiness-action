#!/usr/bin/env python3
"""自身と同じ checkout のパッケージだけを使う CLI エントリポイント。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pr_merge_readiness.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
