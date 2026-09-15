"""配布バイナリが起動する git に、同梱ライブラリを混ぜない。"""

import os
import sys

from pr_merge_readiness.process import system_environment


def test_frozen_git_environment_restores_original_library_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/system/lib")
    result = system_environment()
    assert result["LD_LIBRARY_PATH"] == "/system/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in result
    assert os.environ["LD_LIBRARY_PATH"] == "/bundle/lib"


def test_frozen_git_environment_removes_bundle_library_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/lib")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    assert "LD_LIBRARY_PATH" not in system_environment()


def test_source_execution_preserves_library_path(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/source/lib")
    assert system_environment()["LD_LIBRARY_PATH"] == "/source/lib"
