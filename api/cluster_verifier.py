"""Candidate-cluster to bounded Qwen verifier request adapter."""

from __future__ import annotations

import math
from pathlib import Path
import sqlite3
from typing import Any

from ingest.quality import assess_quality
from packages.contracts.query_plan import QueryPlan
from packages.contracts.verification import (
    ConstraintEvidence,
    EvidenceState,
    VerificationResult,
)
from query.verify import (
    EvidenceFrame,
    QwenHTTPVerifierBackend,
    VerificationCache,
    VerificationRequest,
    verify_candidate,
)


class HTTPClusterVerifier:
    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        endpoint: str,
        model_revision: str,
        operating_point_hash: str,
        asset_root: str | Path,
        cache_path: str | Path,
        timeout_s: float = 60.0,
        prompt_schema_version: str = "verify-v1",
        pipeline_version: str = "working-tree-uncommitted",
    ) -> None:
        self.connection = connection
        self.backend = QwenHTTPVerifierBackend(endpoint, timeout_s=timeout_s)
        self.model_revision = model_revision
        self.operating_point_hash = operating_point_hash
        self.asset_root = Path(asset_root).resolve()
        self.asset_root.mkdir(parents=True, exist_ok=True)
        self.cache = VerificationCache(cache_path)
        self.prompt_schema_version = prompt_schema_version
        self.pipeline_version = pipeline_version

    def __call__(self, cluster: Any, plan: QueryPlan) -> VerificationResult:
        source = self.connection.execute(
            "SELECT path,sha256 FROM videos WHERE video_id=?",
            (cluster.video_id,),
        ).fetchone()
        if source is None:
            return _undetermined(
                cluster.cluster_id,
                plan,
                self.model_revision,
                self.prompt_schema_version,
                reason_code="model_error",
                rationale="Source video metadata is unavailable.",
            )
        frames = self._evidence_frames(
            source_path=Path(str(source["path"])).resolve(),
            source_hash=str(source["sha256"]),
            candidate_id=cluster.cluster_id,
            t0=float(cluster.t0),
            t1=float(cluster.t1),
        )
        if len(frames) < 8:
            return _undetermined(
                cluster.cluster_id,
                plan,
                self.model_revision,
                self.prompt_schema_version,
                reason_code="insufficient_context",
                rationale="Fewer than eight distinct evidence frames were decodable.",
            )
        request = VerificationRequest(
            candidate_id=cluster.cluster_id,
            source_hash=str(source["sha256"]),
            candidate_bounds=(float(cluster.t0), float(cluster.t1)),
            frames=tuple(frames),
            subsegments=(),
            query_plan=plan,
            model_revision=self.model_revision,
            quantization="bitsandbytes-nf4-double-quant-bfloat16",
            prompt_schema_version=self.prompt_schema_version,
            decoding_parameters={
                "max_new_tokens": 512,
                "do_sample": False,
                "use_cache": True,
            },
            pipeline_version=self.pipeline_version,
            operating_point_config_hash=self.operating_point_hash,
        )
        return verify_candidate(
            request,
            self.backend,
            cache=self.cache,
            bypass_cache=False,
        ).result

    def _evidence_frames(
        self,
        *,
        source_path: Path,
        source_hash: str,
        candidate_id: str,
        t0: float,
        t1: float,
    ) -> list[EvidenceFrame]:
        import av

        duration = t1 - t0
        requested = min(24, max(8, math.ceil(duration)))
        decoded: list[tuple[float, Any]] = []
        with av.open(str(source_path)) as container:
            container.seek(
                int(max(0.0, t0) * 1_000_000),
                any_frame=False,
                backward=True,
            )
            for frame in container.decode(video=0):
                pts = float(frame.time or 0.0)
                if pts < t0:
                    continue
                if pts >= t1:
                    break
                decoded.append((pts, frame.to_image()))
        if not decoded:
            return []
        count = min(requested, len(decoded))
        targets = [t0 + duration * (index + 0.5) / count for index in range(count)]
        selected: list[tuple[float, Any]] = []
        used: set[int] = set()
        for target in targets:
            index = min(
                (idx for idx in range(len(decoded)) if idx not in used),
                key=lambda idx: abs(decoded[idx][0] - target),
            )
            used.add(index)
            selected.append(decoded[index])
        directory = self.asset_root / source_hash / candidate_id
        directory.mkdir(parents=True, exist_ok=True)
        evidence: list[EvidenceFrame] = []
        for ordinal, (pts, image) in enumerate(sorted(selected), start=1):
            # The bundle ID is deterministic for this source PTS and remains mapped
            # to authoritative source PTS by VerificationRun.
            frame_id = int(round(pts * 1_000_000)) + ordinal
            path = directory / f"{frame_id}.jpg"
            if not path.is_file():
                image.save(path, format="JPEG", quality=95)
            luminance, sharpness = assess_quality(image)
            evidence.append(
                EvidenceFrame(
                    frame_id=frame_id,
                    pts=pts,
                    asset_path=str(path),
                    luminance=luminance,
                    sharpness=sharpness,
                )
            )
        return evidence


def _undetermined(
    candidate_id: str,
    plan: QueryPlan,
    model_revision: str,
    prompt_schema_version: str,
    *,
    reason_code: str,
    rationale: str,
) -> VerificationResult:
    def items(ids: list[str]) -> list[ConstraintEvidence]:
        return [
            ConstraintEvidence(
                constraint_id=value,
                state=EvidenceState.UNDETERMINED,
                reason_code=reason_code,
                rationale=rationale,
            )
            for value in ids
        ]

    return VerificationResult(
        candidate_id=candidate_id,
        model_revision=model_revision,
        prompt_schema_version=prompt_schema_version,
        atoms=items([atom.atom_id for atom in plan.atoms]),
        relations=items([relation.relation_id for relation in plan.relations]),
        logic_groups=items([group.group_id for group in plan.logic_groups]),
    )
