"""Pinned local Qwen3-VL GPU worker for structured candidate verification."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import threading
from typing import Any, Mapping, Sequence


ALLOWED_GENERATION_KEYS = {
    "max_new_tokens",
    "do_sample",
    "num_beams",
    "repetition_penalty",
    "use_cache",
}


class QwenVerifierRuntime:
    def __init__(
        self,
        *,
        model_path: str | Path,
        model_revision: str,
        operating_point_hash: str,
    ) -> None:
        import torch
        from transformers import (
            AutoModelForImageTextToText,
            AutoProcessor,
            BitsAndBytesConfig,
        )

        self.torch = torch
        self.model_revision = model_revision
        self.operating_point_hash = operating_point_hash
        path = Path(model_path).resolve()
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        self.processor = AutoProcessor.from_pretrained(path, local_files_only=True)
        self.model = AutoModelForImageTextToText.from_pretrained(
            path,
            local_files_only=True,
            quantization_config=quantization,
            device_map="auto",
            dtype=torch.bfloat16,
        ).eval()
        self._lock = threading.Lock()

    def health(self) -> dict[str, Any]:
        free, total = self.torch.cuda.mem_get_info()
        return {
            "status": "healthy",
            "model_revision": self.model_revision,
            "operating_point_hash": self.operating_point_hash,
            "quantization": "bitsandbytes-nf4-double-quant-bfloat16",
            "vram_free_gib": free / 1024**3,
            "vram_total_gib": total / 1024**3,
        }

    def verify(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if payload.get("model_revision") != self.model_revision:
            raise ValueError("request model revision does not match loaded worker")
        images_by_id = _decode_assets(payload.get("frame_assets", ()))
        frames = list(payload.get("frames", ()))
        if not 8 <= len(frames) <= 24:
            raise ValueError("worker accepts 8-24 selected evidence frames")
        frame_ids = [int(frame["frame_id"]) for frame in frames]
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("frame IDs must be unique")
        if set(frame_ids).difference(images_by_id):
            raise ValueError("every selected frame requires an embedded asset")

        messages = _messages(payload, frames, images_by_id)
        prompt = self.processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        images = [
            content["image"]
            for content in messages[0]["content"]
            if content["type"] == "image"
        ]
        inputs = self.processor(
            text=[prompt],
            images=images,
            padding=True,
            return_tensors="pt",
        ).to("cuda")
        generation = _generation_parameters(payload.get("decoding_parameters", {}))
        with self._lock, self.torch.inference_mode():
            generated = self.model.generate(**inputs, **generation)
            self.torch.cuda.synchronize()
        generated_only = [
            output[len(input_ids) :]
            for input_ids, output in zip(inputs.input_ids, generated)
        ]
        decoded = self.processor.batch_decode(
            generated_only,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]
        try:
            result = _extract_json(decoded)
        except (ValueError, json.JSONDecodeError):
            # This deliberately triggers the client's one schema-retry path.
            result = {"atoms": [], "relations": [], "logic_groups": []}
        result.setdefault("candidate_id", payload.get("candidate_id"))
        result.setdefault("model_revision", self.model_revision)
        result.setdefault(
            "prompt_schema_version",
            payload.get("prompt_schema_version", "verify-v1"),
        )
        return result


def _decode_assets(values: Sequence[Mapping[str, Any]]) -> dict[int, Any]:
    from PIL import Image

    assets: dict[int, Any] = {}
    for value in values:
        frame_id = int(value["frame_id"])
        raw = base64.b64decode(value["base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != value["sha256"]:
            raise ValueError(f"frame asset hash mismatch: {frame_id}")
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        assets[frame_id] = image
    return assets


def _messages(
    payload: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
    images_by_id: Mapping[int, Any],
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "You are a bounded visual evidence verifier. Decide only from the "
                "supplied single-camera candidate frames. Do not infer identity, do "
                "not claim open-world absence, and do not calculate timestamps."
            ),
        }
    ]
    for frame in frames:
        frame_id = int(frame["frame_id"])
        content.extend(
            (
                {
                    "type": "text",
                    "text": f"frame_id={frame_id}; source_pts={float(frame['pts']):.6f}",
                },
                {"type": "image", "image": images_by_id[frame_id]},
            )
        )
    query_plan = payload.get("query_plan", {})
    target_ids = set(str(value) for value in payload.get("target_constraint_ids", ()))
    sections = {
        "atoms": [
            value
            for value in payload.get("atoms", query_plan.get("atoms", ()))
            if not target_ids or str(value.get("atom_id")) in target_ids
        ],
        "relations": [
            value
            for value in payload.get("relations", query_plan.get("relations", ()))
            if not target_ids or str(value.get("relation_id")) in target_ids
        ],
        "logic_groups": [
            value
            for value in payload.get("logic_groups", query_plan.get("logic_groups", ()))
            if not target_ids or str(value.get("group_id")) in target_ids
        ],
    }
    instruction = (
        "Evaluate every supplied constraint exactly once. Return only compact JSON "
        'with keys "atoms", "relations", "logic_groups", and optional '
        '"matching_subintervals". Each constraint item must use '
        '{"constraint_id":"the supplied ID","state":"supported|contradicted|'
        'unobservable|undetermined","reason_code":"visible_match|visible_mismatch|'
        'occlusion|low_light|out_of_frame|insufficient_context|inconsistent_output|'
        'timeout|model_error","evidence_frame_ids":[supplied integer IDs only],'
        '"rationale":"concise"}. Supported or contradicted states require citations. '
        f"Constraints: {json.dumps(sections, separators=(',', ':'))}."
    )
    if payload.get("subsegments"):
        instruction += (
            " Labeled subsegments: "
            + json.dumps(payload["subsegments"], separators=(",", ":"))
            + "."
        )
    if payload.get("recovery_instruction"):
        instruction += " Recovery instruction: " + str(payload["recovery_instruction"])
    content.append({"type": "text", "text": instruction})
    return [{"role": "user", "content": content}]


def _generation_parameters(raw: Mapping[str, Any]) -> dict[str, Any]:
    generation = {
        key: value for key, value in dict(raw).items() if key in ALLOWED_GENERATION_KEYS
    }
    generation["max_new_tokens"] = min(
        1024,
        max(128, int(generation.get("max_new_tokens", 512))),
    )
    generation.setdefault("do_sample", False)
    generation.setdefault("use_cache", True)
    return generation


def _extract_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model output contains no JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value


def create_app(runtime: QwenVerifierRuntime | None = None) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:
        raise RuntimeError("verifier worker requires FastAPI") from exc
    if runtime is None:
        import os

        model_path = os.environ.get("RAZIEL_VERIFIER_MODEL")
        revision = os.environ.get("RAZIEL_VERIFIER_REVISION")
        operating_hash = os.environ.get("RAZIEL_OPERATING_POINT_HASH")
        if not model_path or not revision or not operating_hash:
            raise RuntimeError(
                "RAZIEL_VERIFIER_MODEL, RAZIEL_VERIFIER_REVISION, and "
                "RAZIEL_OPERATING_POINT_HASH are required"
            )
        runtime = QwenVerifierRuntime(
            model_path=model_path,
            model_revision=revision,
            operating_point_hash=operating_hash,
        )
    app = FastAPI(title="RAZIEL verifier worker", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return runtime.health()

    @app.post("/verify")
    def verify(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return runtime.verify(body)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--operating-point-hash", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    runtime = QwenVerifierRuntime(
        model_path=args.model,
        model_revision=args.revision,
        operating_point_hash=args.operating_point_hash,
    )
    import uvicorn

    uvicorn.run(create_app(runtime), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
