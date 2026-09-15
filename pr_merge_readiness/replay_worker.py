"""ネットワークを止め、Git から取り出した評価器だけを別 namespace で読み込む。"""

import importlib.util
import json
import socket
import sys
from pathlib import Path
from typing import Any


def denied(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("network forbidden during replay")


def main(directory: str) -> int:
    socket.socket.connect = denied  # type: ignore[method-assign]
    socket.socket.connect_ex = denied  # type: ignore[method-assign]
    socket.create_connection = denied
    socket.getaddrinfo = denied
    package = Path(directory) / "pr_merge_readiness"
    namespace = "_pmr_trusted_replay"
    for name in ("__init__", "contracts", "evaluate"):
        module_name = namespace if name == "__init__" else namespace + "." + name
        spec = importlib.util.spec_from_file_location(module_name, package / (name + ".py"))
        if spec is None or spec.loader is None:
            raise ValueError("cannot load trusted evaluator")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    report = json.load(sys.stdin)
    result = sys.modules[namespace + ".evaluate"].assess(report["observations"], report["policy"])
    result["provenance"] = report["provenance"]
    print(json.dumps(result))
    return 0
