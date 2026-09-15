"""PyInstaller のライブラリ検索設定を外部コマンドへ持ち込まない。"""

import os
import sys


def system_environment() -> dict[str, str]:
    environment = dict(os.environ)
    if getattr(sys, "frozen", False):
        original = environment.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            environment.pop("LD_LIBRARY_PATH", None)
        else:
            environment["LD_LIBRARY_PATH"] = original
    return environment
