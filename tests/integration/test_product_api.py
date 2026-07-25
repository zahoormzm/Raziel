from __future__ import annotations

from pathlib import Path
import time

from fastapi.testclient import TestClient

from api.main import RazielService, create_app
from packages.contracts.search_result import SearchResult


def test_public_api_and_local_ui_contract() -> None:
    app = create_app(RazielService())
    client = TestClient(app)

    brand = client.get("/config/brand")
    assert brand.status_code == 200
    assert brand.json() == {
        "product_name": "RAZIEL",
        "product_subtitle": "Temporal Evidence Intelligence",
        "retrieval_name": "Eyes of God",
    }

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["fallback_mode"] == "retrieval_only"

    benchmark = client.get("/benchmark/current")
    assert benchmark.status_code == 200
    assert benchmark.json()["status"] == "not_yet_measured"
    assert benchmark.json()["data_integrity"]["ok"] is True

    page = client.get("/")
    assert page.status_code == 200
    # Product strings must come from shared config, not be duplicated in HTML.
    assert "RAZIEL" not in page.text
    assert "Eyes of God" not in page.text


def test_query_endpoint_never_fabricates_an_unconfigured_result() -> None:
    client = TestClient(create_app(RazielService()))
    response = client.post(
        "/query",
        json={"text": "a person with a black bag", "camera_ids": ["gate-01"]},
    )
    assert response.status_code == 200
    job = response.json()
    assert job["state"] == "failed"
    assert "not configured" in job["error"]


def test_export_artifact_route_is_resolver_bounded(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    def resolve(export_id: str, kind: str) -> Path | None:
        if export_id != "known":
            return None
        return {"clip": clip, "manifest": manifest}.get(kind)

    client = TestClient(create_app(RazielService(export_resolver=resolve)))
    assert client.get("/exports/known/clip").content == b"clip"
    assert client.get("/exports/known/manifest").json() == {}
    assert client.get("/exports/unknown/clip").status_code == 404
    assert client.get("/exports/known/not-a-kind").status_code == 404


def test_async_query_exposes_interpretation_and_pollable_progress() -> None:
    def query_handler(body: dict[str, object]) -> dict[str, object]:
        time.sleep(0.01)
        return {"echo": body["text"]}

    service = RazielService(
        query_handler=query_handler,
        query_interpreter=lambda body: {
            "query_text": body["text"],
            "state": "clear",
            "atoms": [],
        },
        async_queries=True,
    )
    client = TestClient(create_app(service))
    interpretation = client.post("/query/interpret", json={"text": "red object"})
    assert interpretation.status_code == 200
    assert interpretation.json()["query_text"] == "red object"

    submitted = client.post("/query", json={"text": "red object"}).json()
    assert submitted["state"] in {"queued", "running", "complete"}
    for _attempt in range(100):
        status = client.get(f"/query/{submitted['job_id']}").json()
        if status["state"] == "complete":
            break
        time.sleep(0.005)
    assert status["state"] == "complete"
    assert status["result"] == {"echo": "red object"}
    assert any(event["stage"] == "retrieval" for event in status["events"])
