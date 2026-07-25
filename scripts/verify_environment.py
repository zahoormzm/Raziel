"""Record the reproducibility surface without claiming optional tools exist."""

from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def command_version(command: list[str]) -> str | None:
    if shutil.which(command[0]) is None:
        return None
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or result.stderr).splitlines()[0]


def main() -> int:
    report = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "os": platform.platform(),
        "python": sys.version,
        "executables": {
            "ffmpeg": command_version(["ffmpeg", "-version"]),
            "ffprobe": command_version(["ffprobe", "-version"]),
            "git": command_version(["git", "--version"]),
            "nvidia_smi": command_version(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]),
        },
        "python_modules": {
            name: bool(importlib.util.find_spec(name))
            for name in [
                "av",
                "fastapi",
                "numpy",
                "pydantic",
                "torch",
                "transformers",
                "uvicorn",
            ]
        },
        "status": "inventory_only_not_a_gate_result",
    }
    output = Path("artifacts/environment_report.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
