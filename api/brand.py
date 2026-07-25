"""Load the three frozen public brand strings from one shared configuration."""

from __future__ import annotations

from pathlib import Path


REQUIRED_KEYS = {"product_name", "product_subtitle", "retrieval_name"}


def load_brand_config(path: Path | None = None) -> dict[str, str]:
    config_path = path or Path(__file__).resolve().parents[1] / "config" / "brand.yaml"
    values: dict[str, str] = {}
    in_brand = False
    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "brand:":
            in_brand = True
            continue
        if not in_brand or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if key in REQUIRED_KEYS:
            values[key] = raw_value.strip().strip('"').strip("'")
    missing = REQUIRED_KEYS.difference(values)
    if missing:
        raise ValueError(f"brand config is missing keys: {sorted(missing)}")
    return values
