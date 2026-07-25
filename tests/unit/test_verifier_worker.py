from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from PIL import Image

from api.verifier_worker import (
    _decode_assets,
    _extract_json,
    _generation_parameters,
    create_app,
)
from api.cluster_verifier import HTTPClusterVerifier


class FakeRuntime:
    def health(self) -> dict[str, object]:
        return {"status": "healthy", "model_revision": "fixture"}

    def verify(self, payload: dict[str, object]) -> dict[str, object]:
        return {"candidate_id": payload["candidate_id"], "atoms": []}


def _asset() -> dict[str, object]:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (255, 0, 0)).save(buffer, format="PNG")
    raw = buffer.getvalue()
    return {
        "frame_id": 7,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "base64": base64.b64encode(raw).decode("ascii"),
    }


def test_worker_helpers_are_bounded_and_hash_assets() -> None:
    assert _extract_json('prefix {"atoms":[]} suffix') == {"atoms": []}
    generation = _generation_parameters(
        {"max_new_tokens": 50_000, "temperature": 0.9, "num_beams": 1}
    )
    assert generation["max_new_tokens"] == 1024
    assert "temperature" not in generation
    assert _decode_assets([_asset()])[7].size == (2, 2)
    tampered = _asset()
    tampered["sha256"] = "0" * 64
    try:
        _decode_assets([tampered])
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("tampered frame asset must be rejected")


def test_worker_routes_do_not_load_a_model_when_runtime_is_supplied() -> None:
    client = TestClient(create_app(FakeRuntime()))
    assert client.get("/health").json()["model_revision"] == "fixture"
    response = client.post("/verify", json={"candidate_id": "c1"})
    assert response.status_code == 200
    assert response.json()["candidate_id"] == "c1"


def test_cluster_adapter_selects_bounded_pts_labeled_assets(tmp_path: Path) -> None:
    adapter = HTTPClusterVerifier(
        connection=sqlite3.connect(":memory:"),
        endpoint="http://127.0.0.1:1/verify",
        model_revision="fixture",
        operating_point_hash="fixture-op",
        asset_root=tmp_path / "assets",
        cache_path=tmp_path / "cache.sqlite3",
    )
    source = Path(__file__).resolve().parents[2] / "tests" / "golden" / "golden_synthetic.mp4"
    frames = adapter._evidence_frames(
        source_path=source,
        source_hash="a" * 64,
        candidate_id="candidate",
        t0=30.0,
        t1=34.0,
    )
    assert len(frames) == 8
    assert [frame.pts for frame in frames] == sorted(frame.pts for frame in frames)
    assert len({frame.frame_id for frame in frames}) == 8
    assert all(Path(frame.asset_path or "").is_file() for frame in frames)
