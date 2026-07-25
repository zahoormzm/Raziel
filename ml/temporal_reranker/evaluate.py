"""Evaluate the held-out ship decision from externally computed metrics."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Sequence

from ml.temporal_reranker.gates import evaluate_ship_gate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--rejection-material-tolerance", type=float)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args(argv)
    metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
    report = evaluate_ship_gate(
        metrics,
        rejection_material_tolerance=args.rejection_material_tolerance,
    )
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 0 if report.passed or not args.require_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
