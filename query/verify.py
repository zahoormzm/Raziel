"""Structured, citation-safe candidate verification with one targeted recovery."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import base64
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.request import Request, urlopen

from pydantic import ValidationError

from packages.contracts.query_plan import QueryPlan
from packages.contracts.verification import (
    ConstraintEvidence,
    EvidenceState,
    MatchingSubinterval,
    VerificationResult,
)


ALLOWED_REASON_CODES = frozenset(
    {
        "visible_match",
        "visible_mismatch",
        "occlusion",
        "low_light",
        "out_of_frame",
        "insufficient_context",
        "inconsistent_output",
        "timeout",
        "model_error",
    }
)
RECOVERABLE_REASONS = ("insufficient_context", "inconsistent_output", "timeout")


@dataclass(frozen=True)
class EvidenceFrame:
    frame_id: int
    pts: float
    asset_path: str | None = None
    luminance: float | None = None
    sharpness: float | None = None
    missing: bool = False


@dataclass(frozen=True)
class EvidenceSubsegment:
    label: str
    start_frame_id: int
    end_frame_id: int


@dataclass(frozen=True)
class VerificationRequest:
    candidate_id: str
    source_hash: str
    candidate_bounds: tuple[float, float]
    frames: tuple[EvidenceFrame, ...]
    subsegments: tuple[EvidenceSubsegment, ...]
    query_plan: QueryPlan
    model_revision: str
    quantization: str
    prompt_schema_version: str
    decoding_parameters: Mapping[str, Any]
    pipeline_version: str
    operating_point_config_hash: str

    def validate(self) -> None:
        if not self.candidate_id or not self.source_hash:
            raise ValueError("candidate_id and source_hash are required")
        if self.candidate_bounds[0] > self.candidate_bounds[1]:
            raise ValueError("candidate bounds are reversed")
        if not 8 <= len(self.frames) <= 24:
            raise ValueError("a verifier call requires 8-24 selected evidence frames")
        frame_ids = [frame.frame_id for frame in self.frames]
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("evidence frame IDs must be unique")
        if [frame.pts for frame in self.frames] != sorted(frame.pts for frame in self.frames):
            raise ValueError("evidence frames must be ordered by source PTS")
        known = set(frame_ids)
        for subsegment in self.subsegments:
            if (
                subsegment.start_frame_id not in known
                or subsegment.end_frame_id not in known
            ):
                raise ValueError("subsegments must cite supplied frame IDs")
            if _pts_by_id(self.frames)[subsegment.start_frame_id] > _pts_by_id(self.frames)[
                subsegment.end_frame_id
            ]:
                raise ValueError("subsegment end precedes start in source PTS")

    @property
    def supplied_frame_ids(self) -> set[int]:
        return {frame.frame_id for frame in self.frames}

    @property
    def expected_ids(self) -> Mapping[str, tuple[str, ...]]:
        return {
            "atoms": tuple(atom.atom_id for atom in self.query_plan.atoms),
            "relations": tuple(relation.relation_id for relation in self.query_plan.relations),
            "logic_groups": tuple(group.group_id for group in self.query_plan.logic_groups),
        }

    def to_payload(
        self,
        *,
        recovery_instruction: str | None = None,
        target_constraint_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_hash": self.source_hash,
            "candidate_bounds": self.candidate_bounds,
            "model_revision": self.model_revision,
            "quantization": self.quantization,
            "prompt_schema_version": self.prompt_schema_version,
            "decoding_parameters": dict(self.decoding_parameters),
            "pipeline_version": self.pipeline_version,
            "operating_point_config_hash": self.operating_point_config_hash,
            "query_plan": self.query_plan.model_dump(mode="json"),
            "frames": [
                {
                    "frame_id": frame.frame_id,
                    "pts": frame.pts,
                    "asset_path": frame.asset_path,
                    "quality": {
                        "luminance": frame.luminance,
                        "sharpness": frame.sharpness,
                        "missing": frame.missing,
                    },
                }
                for frame in self.frames
            ],
            "subsegments": [subsegment.__dict__ for subsegment in self.subsegments],
            "atoms": [atom.model_dump(mode="json") for atom in self.query_plan.atoms],
            "relations": [
                relation.model_dump(mode="json") for relation in self.query_plan.relations
            ],
            "logic_groups": [
                group.model_dump(mode="json") for group in self.query_plan.logic_groups
            ],
            "instruction": (
                "Return one state per supplied constraint. Cite only frame_id values from "
                "this request. Do not calculate authoritative timestamps."
            ),
            "recovery_instruction": recovery_instruction,
            "target_constraint_ids": list(target_constraint_ids),
        }


class VerifierBackend(Protocol):
    def verify(
        self,
        request: VerificationRequest,
        *,
        recovery_instruction: str | None = None,
        target_constraint_ids: Sequence[str] = (),
    ) -> Mapping[str, Any] | str: ...


class QwenHTTPVerifierBackend:
    """Adapter for a separately managed, versioned local Qwen GPU worker."""

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_s: float = 60.0,
        embed_frame_assets: bool = True,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("Qwen worker endpoint must be HTTP(S)")
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.embed_frame_assets = embed_frame_assets

    def verify(
        self,
        request: VerificationRequest,
        *,
        recovery_instruction: str | None = None,
        target_constraint_ids: Sequence[str] = (),
    ) -> Mapping[str, Any]:
        payload = request.to_payload(
            recovery_instruction=recovery_instruction,
            target_constraint_ids=target_constraint_ids,
        )
        if self.embed_frame_assets:
            payload["frame_assets"] = [
                _encoded_frame_asset(frame)
                for frame in request.frames
                if frame.asset_path is not None
            ]
        wire_request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(wire_request, timeout=self.timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))


class LocalQwenTransformersBackend:
    """Lazy local-files-only Transformers adapter.

    A caller supplies prompt rendering and image loading because those choices are
    versioned experiment inputs.  Construction never downloads weights.
    """

    def __init__(
        self,
        model_path: str,
        *,
        processor_path: str | None = None,
        quantization_config: Any = None,
        render_inputs: Callable[[Any, Any, VerificationRequest, str | None, Sequence[str]], Any],
    ) -> None:
        try:
            import transformers
        except ImportError as exc:  # pragma: no cover - dependency-dependent path
            raise RuntimeError("Transformers is unavailable in the verifier environment") from exc
        model_class = getattr(
            transformers,
            "AutoModelForImageTextToText",
            getattr(transformers, "AutoModelForVision2Seq", None),
        )
        if model_class is None:
            raise RuntimeError("the pinned Transformers build has no compatible vision-text auto model")
        self.processor = transformers.AutoProcessor.from_pretrained(
            processor_path or model_path, local_files_only=True
        )
        self.model = model_class.from_pretrained(
            model_path,
            local_files_only=True,
            quantization_config=quantization_config,
            device_map="auto",
        )
        self.render_inputs = render_inputs

    def verify(
        self,
        request: VerificationRequest,
        *,
        recovery_instruction: str | None = None,
        target_constraint_ids: Sequence[str] = (),
    ) -> Mapping[str, Any]:
        inputs = self.render_inputs(
            self.processor,
            self.model,
            request,
            recovery_instruction,
            target_constraint_ids,
        )
        generated = self.model.generate(
            **inputs, **dict(request.decoding_parameters)
        )
        prompt_length = inputs["input_ids"].shape[-1]
        generated_only = generated[:, prompt_length:]
        decoded = self.processor.batch_decode(
            generated_only, skip_special_tokens=True
        )[0]
        return json.loads(decoded)


@dataclass(frozen=True)
class VerificationCacheKey:
    source_hash: str
    exact_frames: tuple[tuple[int, float], ...]
    candidate_bounds: tuple[float, float]
    model_revision: str
    quantization: str
    prompt_schema_version: str
    decoding_parameters: Mapping[str, Any]
    pipeline_version: str
    operating_point_config_hash: str
    query_plan_hash: str
    subsegments: tuple[tuple[str, int, int], ...]

    @classmethod
    def from_request(cls, request: VerificationRequest) -> "VerificationCacheKey":
        return cls(
            source_hash=request.source_hash,
            exact_frames=tuple((frame.frame_id, frame.pts) for frame in request.frames),
            candidate_bounds=request.candidate_bounds,
            model_revision=request.model_revision,
            quantization=request.quantization,
            prompt_schema_version=request.prompt_schema_version,
            decoding_parameters=dict(request.decoding_parameters),
            pipeline_version=request.pipeline_version,
            operating_point_config_hash=request.operating_point_config_hash,
            query_plan_hash=sha256(
                request.query_plan.model_dump_json().encode("utf-8")
            ).hexdigest(),
            subsegments=tuple(
                (item.label, item.start_frame_id, item.end_frame_id)
                for item in request.subsegments
            ),
        )

    @property
    def digest(self) -> str:
        payload = {
            "source_hash": self.source_hash,
            "exact_frames": self.exact_frames,
            "candidate_bounds": self.candidate_bounds,
            "model_revision": self.model_revision,
            "quantization": self.quantization,
            "prompt_schema_version": self.prompt_schema_version,
            "decoding_parameters": self.decoding_parameters,
            "pipeline_version": self.pipeline_version,
            "operating_point_config_hash": self.operating_point_config_hash,
            "query_plan_hash": self.query_plan_hash,
            "subsegments": self.subsegments,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()


class VerificationCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS verification_cache (
                    cache_key TEXT PRIMARY KEY,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def get(self, key: VerificationCacheKey) -> VerificationResult | None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            row = connection.execute(
                "SELECT response_json FROM verification_cache WHERE cache_key=?",
                (key.digest,),
            ).fetchone()
        if row is None:
            return None
        return VerificationResult.model_validate_json(row[0]).model_copy(update={"cached": True})

    def put(self, key: VerificationCacheKey, result: VerificationResult) -> None:
        stored = result.model_copy(update={"cached": False})
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO verification_cache(cache_key,response_json) VALUES(?,?)",
                (key.digest, stored.model_dump_json()),
            )


@dataclass(frozen=True)
class VerificationRun:
    result: VerificationResult
    pts_by_frame_id: Mapping[int, float]
    evidence_pts_by_constraint: Mapping[str, tuple[float, ...]]
    matching_subinterval_pts: tuple[tuple[float, float], ...]
    schema_retry_used: bool
    targeted_recovery_used: bool
    cache_hit: bool


def verify_candidate(
    request: VerificationRequest,
    backend: VerifierBackend,
    *,
    cache: VerificationCache | None = None,
    bypass_cache: bool = False,
    context_resampler: Callable[[VerificationRequest, Sequence[str]], VerificationRequest]
    | None = None,
) -> VerificationRun:
    request.validate()
    key = VerificationCacheKey.from_request(request)
    if cache is not None and not bypass_cache:
        cached = cache.get(key)
        if cached is not None:
            return _finalize(cached, request, schema_retry_used=False, recovery_used=False, cache_hit=True)

    schema_retry_used = False
    try:
        raw = backend.verify(request)
        result = _parse_result(raw, request, retry_count=0)
    except TimeoutError:
        result = _all_undetermined(request, "timeout", retry_count=0)
    except (ValueError, TypeError, KeyError, ValidationError, json.JSONDecodeError) as first_error:
        schema_retry_used = True
        try:
            raw = backend.verify(
                request,
                recovery_instruction=f"Schema validation failed: {first_error}",
            )
            result = _parse_result(raw, request, retry_count=1)
        except TimeoutError:
            result = _all_undetermined(request, "timeout", retry_count=1)
        except Exception:
            result = _all_undetermined(request, "inconsistent_output", retry_count=1)
    except Exception:
        result = _all_undetermined(request, "model_error", retry_count=0)

    recovery_used = False
    recoverable = _recoverable_constraints(result)
    if recoverable:
        recovery_used = True
        reason, target_ids = recoverable
        recovery_request = request
        if reason == "insufficient_context" and context_resampler is not None:
            recovery_request = context_resampler(request, target_ids)
            recovery_request.validate()
        instruction = _recovery_instruction(reason, recovery_request)
        try:
            raw_recovery = backend.verify(
                recovery_request,
                recovery_instruction=instruction,
                target_constraint_ids=target_ids,
            )
            recovered = _parse_result(
                raw_recovery,
                recovery_request,
                retry_count=result.retry_count,
                allowed_subset=set(target_ids),
            )
            result = _merge_recovery(result, recovered, set(target_ids))
            request = recovery_request
        except Exception:
            # The original undetermined result remains visible.
            pass

    if cache is not None:
        # A context recovery may have changed the exact frame/PTS bundle.
        cache.put(VerificationCacheKey.from_request(request), result)
    return _finalize(
        result,
        request,
        schema_retry_used=schema_retry_used,
        recovery_used=recovery_used,
        cache_hit=False,
    )


def _parse_result(
    raw: Mapping[str, Any] | str,
    request: VerificationRequest,
    *,
    retry_count: int,
    allowed_subset: set[str] | None = None,
) -> VerificationResult:
    if isinstance(raw, str):
        raw = json.loads(raw)
    normalized = dict(raw)
    normalized.setdefault("candidate_id", request.candidate_id)
    normalized.setdefault("model_revision", request.model_revision)
    normalized.setdefault("prompt_schema_version", request.prompt_schema_version)
    normalized["retry_count"] = retry_count
    normalized["cached"] = False
    expected = request.expected_ids
    known_frames = request.supplied_frame_ids
    seen: set[str] = set()
    for section in ("atoms", "relations", "logic_groups"):
        values = []
        for item in normalized.get(section, []):
            item = dict(item)
            constraint_id = str(item.get("constraint_id", item.get("atom_id", "")))
            item["constraint_id"] = constraint_id
            item.pop("atom_id", None)
            if item.get("reason_code") not in ALLOWED_REASON_CODES:
                raise ValueError(f"invalid reason_code for {constraint_id}")
            if constraint_id in seen:
                raise ValueError(f"duplicate constraint result: {constraint_id}")
            seen.add(constraint_id)
            citations = {int(frame_id) for frame_id in item.get("evidence_frame_ids", [])}
            if citations.difference(known_frames):
                item.update(
                    {
                        "state": "undetermined",
                        "reason_code": "inconsistent_output",
                        "evidence_frame_ids": [],
                        "rationale": "Verifier cited frame IDs outside the supplied evidence bundle.",
                    }
                )
            values.append(item)
        normalized[section] = values

    expected_all = {value for values in expected.values() for value in values}
    if allowed_subset is None:
        if seen != expected_all:
            raise ValueError(
                f"constraint output mismatch; missing={sorted(expected_all-seen)}, "
                f"unknown={sorted(seen-expected_all)}"
            )
    elif seen != allowed_subset:
        raise ValueError("targeted recovery must return exactly its targeted constraints")

    result = VerificationResult.model_validate(normalized)
    if result.candidate_id != request.candidate_id:
        raise ValueError("verifier returned a different candidate_id")
    if result.model_revision != request.model_revision:
        raise ValueError("verifier returned a different model revision")
    if result.prompt_schema_version != request.prompt_schema_version:
        raise ValueError("verifier returned a different prompt/schema version")
    for item in [*result.atoms, *result.relations, *result.logic_groups]:
        _validate_state_reason(item)
    _validate_matching_subinterval_ids(result.matching_subintervals, known_frames)
    result.validate_citations(known_frames)
    return result


def _all_undetermined(
    request: VerificationRequest, reason_code: str, *, retry_count: int
) -> VerificationResult:
    def items(ids: Sequence[str]) -> list[ConstraintEvidence]:
        return [
            ConstraintEvidence(
                constraint_id=constraint_id,
                state=EvidenceState.UNDETERMINED,
                reason_code=reason_code,
                rationale="The system could not determine this constraint.",
            )
            for constraint_id in ids
        ]

    expected = request.expected_ids
    return VerificationResult(
        candidate_id=request.candidate_id,
        model_revision=request.model_revision,
        prompt_schema_version=request.prompt_schema_version,
        atoms=items(expected["atoms"]),
        relations=items(expected["relations"]),
        logic_groups=items(expected["logic_groups"]),
        retry_count=retry_count,
    )


def _recoverable_constraints(
    result: VerificationResult,
) -> tuple[str, tuple[str, ...]] | None:
    all_constraints = [*result.atoms, *result.relations, *result.logic_groups]
    for reason in RECOVERABLE_REASONS:
        targets = tuple(
            item.constraint_id
            for item in all_constraints
            if item.state == EvidenceState.UNDETERMINED and item.reason_code == reason
        )
        if targets:
            return reason, targets
    return None


def _recovery_instruction(reason: str, request: VerificationRequest) -> str:
    if reason == "insufficient_context":
        return "Re-evaluate only the targeted constraints using the added contextual frames."
    if reason == "inconsistent_output":
        return (
            "Re-evaluate only the targeted constraints and cite only these valid frame IDs: "
            + ", ".join(str(value) for value in sorted(request.supplied_frame_ids))
        )
    return "Retry only the targeted constraints after the transient timeout."


def _merge_recovery(
    original: VerificationResult,
    recovered: VerificationResult,
    target_ids: set[str],
) -> VerificationResult:
    recovered_by_id = {
        item.constraint_id: item
        for item in [*recovered.atoms, *recovered.relations, *recovered.logic_groups]
        if item.constraint_id in target_ids
    }

    def merge(items: Sequence[ConstraintEvidence]) -> list[ConstraintEvidence]:
        return [recovered_by_id.get(item.constraint_id, item) for item in items]

    return original.model_copy(
        update={
            "atoms": merge(original.atoms),
            "relations": merge(original.relations),
            "logic_groups": merge(original.logic_groups),
            "matching_subintervals": (
                recovered.matching_subintervals or original.matching_subintervals
            ),
        }
    )


def _finalize(
    result: VerificationResult,
    request: VerificationRequest,
    *,
    schema_retry_used: bool,
    recovery_used: bool,
    cache_hit: bool,
) -> VerificationRun:
    pts = _pts_by_id(request.frames)
    # After a context recovery, `request` is the RESAMPLED bundle while
    # non-targeted constraints still cite frame ids validated against the
    # original one. VerificationRequest caps the bundle at 24 frames, so adding
    # context necessarily drops others, and a stale citation used to raise
    # KeyError here -- crashing the whole verification instead of returning the
    # result. A citation we can no longer resolve is dropped from the evidence
    # PTS rather than fabricated; the constraint's own state is untouched.
    evidence_pts = {
        item.constraint_id: tuple(
            pts[frame_id] for frame_id in item.evidence_frame_ids if frame_id in pts
        )
        for item in [*result.atoms, *result.relations, *result.logic_groups]
    }
    intervals = tuple(
        (pts[item.start_frame_id], pts[item.end_frame_id])
        for item in result.matching_subintervals
        if item.start_frame_id in pts and item.end_frame_id in pts
    )
    return VerificationRun(
        result=result,
        pts_by_frame_id=pts,
        evidence_pts_by_constraint=evidence_pts,
        matching_subinterval_pts=intervals,
        schema_retry_used=schema_retry_used,
        targeted_recovery_used=recovery_used,
        cache_hit=cache_hit,
    )


def _pts_by_id(frames: Sequence[EvidenceFrame]) -> dict[int, float]:
    return {frame.frame_id: frame.pts for frame in frames}


def _validate_matching_subinterval_ids(
    intervals: Sequence[MatchingSubinterval], known_frames: set[int]
) -> None:
    for interval in intervals:
        if (
            interval.start_frame_id not in known_frames
            or interval.end_frame_id not in known_frames
        ):
            raise ValueError("matching subinterval cites an unknown frame ID")


def _validate_state_reason(item: ConstraintEvidence) -> None:
    allowed = {
        EvidenceState.SUPPORTED: {"visible_match"},
        EvidenceState.CONTRADICTED: {"visible_mismatch"},
        EvidenceState.UNOBSERVABLE: {"occlusion", "low_light", "out_of_frame"},
        EvidenceState.UNDETERMINED: {
            "insufficient_context",
            "inconsistent_output",
            "timeout",
            "model_error",
        },
    }
    if item.reason_code not in allowed[item.state]:
        raise ValueError(
            f"reason_code {item.reason_code!r} is inconsistent with state {item.state.value!r}"
        )


def _encoded_frame_asset(frame: EvidenceFrame) -> Mapping[str, Any]:
    if frame.asset_path is None:
        raise ValueError("cannot encode an evidence frame without an asset path")
    raw = Path(frame.asset_path).read_bytes()
    return {
        "frame_id": frame.frame_id,
        "sha256": sha256(raw).hexdigest(),
        "base64": base64.b64encode(raw).decode("ascii"),
    }
