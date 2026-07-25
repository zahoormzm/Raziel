from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_init_and_replica_manifest_cli(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[2]
    script = project / "scripts" / "ingest_archive.py"
    database = tmp_path / "archive.sqlite"
    subprocess.run(
        [sys.executable, str(script), "init", "--db", str(database)],
        check=True,
        capture_output=True,
        text=True,
    )
    output = tmp_path / "replica.json"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "replica-manifest",
            "--root",
            str(tmp_path),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    paths = {entry["path"] for entry in manifest["files"]}
    assert "archive.sqlite" in paths
    assert len(manifest["manifest_sha256"]) == 64
