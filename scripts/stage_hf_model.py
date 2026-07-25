"""Stage one exact Hugging Face repository revision into a local model directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from huggingface_hub import snapshot_download


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    resolved = snapshot_download(
        repo_id=args.repository,
        revision=args.revision,
        local_dir=output,
        ignore_patterns=("*.md", "*.png", "*.jpg", "*.jpeg", "*.gif"),
    )
    print(
        json.dumps(
            {
                "repository": args.repository,
                "revision": args.revision,
                "resolved_path": str(Path(resolved).resolve()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
