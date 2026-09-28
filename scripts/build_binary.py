"""uv.lock の PyInstaller で Linux x64 用の配布物を生成する。"""

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("Build on Linux x64 with Python 3.14")
    if sys.version_info[:2] != (3, 14):
        raise SystemExit("Build with Python 3.14")
    target = "linux-x64"
    destination = ROOT / "dist" / target
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--noupx",
            "--name=pr-merge-readiness",
            "--distpath=" + str(destination),
            "--workpath=" + str(ROOT / "build" / target),
            "--specpath=" + str(ROOT / "build"),
            str(ROOT / "cli.py"),
        ],
        cwd=ROOT,
        check=True,
    )
    binary = destination / "pr-merge-readiness"
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (destination / "SHA256SUMS").write_text(f"{digest}  pr-merge-readiness\n")
    print(json.dumps({"platform": target, "binary": str(binary), "sha256": digest}))


if __name__ == "__main__":
    main()
