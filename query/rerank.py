"""Temporal reranker integration with a mandatory RRF fallback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

from ml.temporal_reranker.checkpoint import load_checkpoint
from ml.temporal_reranker.dataset import CandidateFeatureRecord
from ml.temporal_reranker.gates import ShipGateReport
from ml.temporal_reranker.model import TemporalRerankerConfig, build_torch_model
from packages.contracts.candidates import CandidateCluster, CandidateSet


@dataclass(frozen=True)
class RerankerResult:
    candidate_id: str
    relevance_score: float
    atom_support_logits: Mapping[str, float]
    relation_support_logits: Mapping[str, float] = field(default_factory=dict)
    evidence_frame_relevance: tuple[float, ...] = ()
    start_distribution: tuple[float, ...] = ()
    end_distribution: tuple[float, ...] = ()
    evidence_completeness_score: float | None = None
    model_revision: str = ""
    feature_cache_key: str = ""

    def validate(
        self,
        *,
        expected_atom_ids: set[str],
        expected_relation_ids: set[str],
        expected_frame_count: int,
    ) -> None:
        if not self.candidate_id or not self.model_revision or not self.feature_cache_key:
            raise ValueError("candidate ID, model revision, and feature cache key are required")
        if set(self.atom_support_logits) != expected_atom_ids:
            raise ValueError("reranker must return exactly one logit per atom")
        relation_ids = set(self.relation_support_logits)
        if relation_ids and relation_ids != expected_relation_ids:
            raise ValueError("enabled relation/logic head must return every expected ID")
        for name, values in (
            ("evidence_frame_relevance", self.evidence_frame_relevance),
            ("start_distribution", self.start_distribution),
            ("end_distribution", self.end_distribution),
        ):
            if values and len(values) != expected_frame_count:
                raise ValueError(f"{name} length does not match the candidate frame count")


class TemporalRerankerBackend(Protocol):
    @property
    def model_revision(self) -> str: ...

    def predict(
        self, features: CandidateFeatureRecord, *, feature_cache_key: str
    ) -> RerankerResult: ...


class TorchTemporalRerankerBackend:
    """Lazy local inference adapter for a gated reranker checkpoint."""

    def __init__(
        self,
        checkpoint_path: str,
        *,
        config: TemporalRerankerConfig,
        feature_order: Sequence[str],
        atom_order: Sequence[str],
        relation_order: Sequence[str] = (),
        model_revision: str,
        device: str = "cpu",
    ) -> None:
        try:
            import torch
        except ImportError as exc:  # pragma: no cover - dependency-dependent path
            raise RuntimeError("PyTorch is unavailable in the reranker environment") from exc
        self._torch = torch
        self._feature_order = tuple(feature_order)
        self._atom_order = tuple(atom_order)
        self._relation_order = tuple(relation_order)
        self._revision = model_revision
        self._device = device
        self._model = build_torch_model(config).to(device)
        checkpoint = load_checkpoint(checkpoint_path)
        self._model.load_state_dict(checkpoint.model_state)
        self._model.eval()

    @property
    def model_revision(self) -> str:
        return self._revision

    def predict(
        self, features: CandidateFeatureRecord, *, feature_cache_key: str
    ) -> RerankerResult:
        torch = self._torch
        length = features.length
        missing_atoms = set(self._atom_order).difference(features.atom_features)
        if missing_atoms:
            raise ValueError(f"candidate is missing atom features: {sorted(missing_atoms)}")
        missing_relations = set(self._relation_order).difference(features.relation_features)
        if missing_relations:
            raise ValueError(
                f"candidate is missing relation/logic features: {sorted(missing_relations)}"
            )
        sequence = torch.tensor(
            [features.ordered_feature_matrix(self._feature_order)],
            dtype=torch.float32,
            device=self._device,
        )
        relative_time = torch.tensor(
            [features.sequence_features["relative_time"]],
            dtype=torch.float32,
            device=self._device,
        )
        sequence_mask = torch.ones((1, length), dtype=torch.bool, device=self._device)
        atoms = torch.tensor(
            [
                [[[value] for value in features.atom_features[name]]
                 for name in self._atom_order]
            ],
            dtype=torch.float32,
            device=self._device,
        )
        atom_mask = torch.ones(
            (1, len(self._atom_order)), dtype=torch.bool, device=self._device
        )
        relations = None
        relation_mask = None
        if self._relation_order:
            relations = torch.tensor(
                [
                    [[[value] for value in features.relation_features[name]]
                     for name in self._relation_order]
                ],
                dtype=torch.float32,
                device=self._device,
            )
            relation_mask = torch.ones(
                (1, len(self._relation_order)),
                dtype=torch.bool,
                device=self._device,
            )
        with torch.no_grad():
            output = self._model(
                sequence_features=sequence,
                relative_time=relative_time,
                sequence_mask=sequence_mask,
                atom_features=atoms,
                atom_mask=atom_mask,
                relation_features=relations,
                relation_mask=relation_mask,
            )
        relation_logits = (
            output.relation_support_logits[0].detach().cpu().tolist()
            if output.relation_support_logits is not None
            else []
        )
        completeness = (
            float(torch.sigmoid(output.evidence_completeness[0]).detach().cpu())
            if output.evidence_completeness is not None
            else None
        )
        return RerankerResult(
            candidate_id=features.candidate_id,
            relevance_score=float(output.candidate_relevance[0].detach().cpu()),
            atom_support_logits=dict(
                zip(
                    self._atom_order,
                    output.atom_support_logits[0].detach().cpu().tolist(),
                )
            ),
            relation_support_logits=dict(zip(self._relation_order, relation_logits)),
            evidence_frame_relevance=tuple(
                torch.sigmoid(output.frame_relevance_logits[0]).detach().cpu().tolist()
            ),
            start_distribution=tuple(
                torch.softmax(output.start_logits[0], dim=-1).detach().cpu().tolist()
            ),
            end_distribution=tuple(
                torch.softmax(output.end_logits[0], dim=-1).detach().cpu().tolist()
            ),
            evidence_completeness_score=completeness,
            model_revision=self.model_revision,
            feature_cache_key=feature_cache_key,
        )


@dataclass(frozen=True)
class RerankBatchResult:
    ordered_cluster_ids: tuple[str, ...]
    baseline_cluster_ids: tuple[str, ...]
    results: Mapping[str, RerankerResult]
    mode: str
    fallback_used: bool
    failure_reason: str | None


def rerank_candidates(
    candidate_set: CandidateSet,
    *,
    features: Mapping[str, CandidateFeatureRecord],
    feature_cache_keys: Mapping[str, str],
    atom_ids: set[str],
    relation_logic_ids: set[str],
    backend: TemporalRerankerBackend | None,
    feature_enabled: bool,
    ship_gate: ShipGateReport | None,
) -> RerankBatchResult:
    baseline = tuple(
        cluster.cluster_id
        for cluster in sorted(
            candidate_set.clusters,
            key=lambda cluster: (-cluster.priority_score, cluster.cluster_id),
        )
    )
    if not feature_enabled or backend is None:
        return RerankBatchResult(
            ordered_cluster_ids=baseline,
            baseline_cluster_ids=baseline,
            results={},
            mode="disabled",
            fallback_used=True,
            failure_reason=None,
        )

    outputs: dict[str, RerankerResult] = {}
    try:
        for cluster in candidate_set.clusters:
            record = features[cluster.cluster_id]
            result = backend.predict(
                record, feature_cache_key=feature_cache_keys[cluster.cluster_id]
            )
            if result.candidate_id != cluster.cluster_id:
                raise ValueError("reranker candidate ID does not match the requested cluster")
            result.validate(
                expected_atom_ids=atom_ids,
                expected_relation_ids=relation_logic_ids,
                expected_frame_count=record.length,
            )
            outputs[cluster.cluster_id] = result
    except Exception as exc:
        return RerankBatchResult(
            ordered_cluster_ids=baseline,
            baseline_cluster_ids=baseline,
            results=outputs,
            mode="fallback",
            fallback_used=True,
            failure_reason=f"{type(exc).__name__}: {exc}",
        )

    if ship_gate is None or not ship_gate.passed:
        # Shadow mode records predictions but cannot alter verification order.
        return RerankBatchResult(
            ordered_cluster_ids=baseline,
            baseline_cluster_ids=baseline,
            results=outputs,
            mode="shadow",
            fallback_used=False,
            failure_reason=None,
        )
    ordered = tuple(
        candidate_id
        for candidate_id, _ in sorted(
            outputs.items(),
            key=lambda item: (-item[1].relevance_score, baseline.index(item[0])),
        )
    )
    return RerankBatchResult(
        ordered_cluster_ids=ordered,
        baseline_cluster_ids=baseline,
        results=outputs,
        mode="active",
        fallback_used=False,
        failure_reason=None,
    )


def cluster_by_id(clusters: Sequence[CandidateCluster]) -> Mapping[str, CandidateCluster]:
    indexed = {cluster.cluster_id: cluster for cluster in clusters}
    if len(indexed) != len(clusters):
        raise ValueError("candidate cluster IDs must be unique")
    return indexed
