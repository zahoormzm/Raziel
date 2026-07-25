"""Lazy PyTorch implementation of the small Temporal Evidence Reranker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TemporalRerankerConfig:
    input_dim: int
    atom_input_dim: int = 1
    relation_input_dim: int = 1
    hidden_dim: int = 128
    architecture: str = "gru"
    num_layers: int = 2
    dropout: float = 0.1
    relation_logic_head: bool = False
    evidence_completeness_head: bool = False

    def validate(self) -> None:
        if self.input_dim <= 0 or self.atom_input_dim <= 0:
            raise ValueError("input dimensions must be positive")
        if self.hidden_dim <= 0 or self.num_layers != 2:
            raise ValueError("the plan requires a positive hidden size and exactly two temporal layers")
        if self.architecture == "gru" and self.hidden_dim % 2:
            raise ValueError("bidirectional GRU hidden_dim must be even")
        if self.architecture not in {"gru", "transformer"}:
            raise ValueError("architecture must be 'gru' or 'transformer'")


@dataclass
class TemporalRerankerOutput:
    candidate_relevance: Any
    atom_support_logits: Any
    relation_support_logits: Any | None
    frame_relevance_logits: Any
    start_logits: Any
    end_logits: Any
    evidence_completeness: Any | None


def build_torch_model(config: TemporalRerankerConfig) -> Any:
    """Build the trainable model without importing torch at module import time."""

    config.validate()
    try:
        import torch
        from torch import nn
    except ImportError as exc:  # pragma: no cover - dependency-dependent path
        raise RuntimeError(
            "PyTorch is optional and must be installed in the locked training environment"
        ) from exc

    class _TemporalEvidenceReranker(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.config = config
            self.input_projection = nn.Linear(config.input_dim, config.hidden_dim)
            self.relative_time_projection = nn.Linear(1, config.hidden_dim)
            if config.architecture == "gru":
                self.temporal = nn.GRU(
                    config.hidden_dim,
                    config.hidden_dim // 2,
                    num_layers=2,
                    batch_first=True,
                    dropout=config.dropout,
                    bidirectional=True,
                )
            else:
                layer = nn.TransformerEncoderLayer(
                    d_model=config.hidden_dim,
                    nhead=_compatible_head_count(config.hidden_dim),
                    dim_feedforward=config.hidden_dim * 4,
                    dropout=config.dropout,
                    batch_first=True,
                    activation="gelu",
                )
                self.temporal = nn.TransformerEncoder(layer, num_layers=2)
            self.atom_projection = nn.Linear(config.atom_input_dim, config.hidden_dim)
            self.relation_projection = nn.Linear(config.relation_input_dim, config.hidden_dim)
            self.candidate_head = nn.Linear(config.hidden_dim, 1)
            self.atom_head = nn.Linear(config.hidden_dim, 1)
            self.relation_head = (
                nn.Linear(config.hidden_dim, 1) if config.relation_logic_head else None
            )
            self.frame_head = nn.Linear(config.hidden_dim, 1)
            self.start_head = nn.Linear(config.hidden_dim, 1)
            self.end_head = nn.Linear(config.hidden_dim, 1)
            self.completeness_head = (
                nn.Linear(config.hidden_dim, 1)
                if config.evidence_completeness_head
                else None
            )

        def forward(
            self,
            sequence_features: Any,
            relative_time: Any,
            sequence_mask: Any,
            atom_features: Any,
            atom_mask: Any,
            relation_features: Any | None = None,
            relation_mask: Any | None = None,
        ) -> TemporalRerankerOutput:
            hidden = self.input_projection(sequence_features)
            hidden = hidden + self.relative_time_projection(relative_time.unsqueeze(-1))
            if config.architecture == "gru":
                hidden, _ = self.temporal(hidden)
            else:
                hidden = self.temporal(hidden, src_key_padding_mask=~sequence_mask.bool())

            weights = sequence_mask.to(hidden.dtype).unsqueeze(-1)
            pooled = (hidden * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1.0)
            if atom_features.ndim != 4:
                raise ValueError("atom_features must have shape [batch, atoms, time, features]")
            atom_context = self.atom_projection(atom_features)
            atom_context = atom_context + hidden.unsqueeze(1)
            atom_time_weights = sequence_mask.to(hidden.dtype)[:, None, :, None]
            atom_context = (torch.tanh(atom_context) * atom_time_weights).sum(dim=2)
            atom_context = atom_context / atom_time_weights.sum(dim=2).clamp_min(1.0)
            atom_logits = self.atom_head(atom_context).squeeze(-1)
            atom_logits = atom_logits.masked_fill(~atom_mask.bool(), 0.0)

            relation_logits = None
            if self.relation_head is not None:
                if relation_features is None or relation_mask is None:
                    raise ValueError("relation features and mask are required when the head is enabled")
                if relation_features.ndim != 4:
                    raise ValueError(
                        "relation_features must have shape [batch, relations, time, features]"
                    )
                relation_context = self.relation_projection(relation_features)
                relation_context = relation_context + hidden.unsqueeze(1)
                relation_time_weights = sequence_mask.to(hidden.dtype)[:, None, :, None]
                relation_context = (
                    torch.tanh(relation_context) * relation_time_weights
                ).sum(dim=2)
                relation_context = relation_context / relation_time_weights.sum(
                    dim=2
                ).clamp_min(1.0)
                relation_logits = self.relation_head(relation_context).squeeze(-1)
                relation_logits = relation_logits.masked_fill(~relation_mask.bool(), 0.0)

            return TemporalRerankerOutput(
                candidate_relevance=self.candidate_head(pooled).squeeze(-1),
                atom_support_logits=atom_logits,
                relation_support_logits=relation_logits,
                frame_relevance_logits=self.frame_head(hidden).squeeze(-1),
                start_logits=self.start_head(hidden).squeeze(-1),
                end_logits=self.end_head(hidden).squeeze(-1),
                evidence_completeness=(
                    self.completeness_head(pooled).squeeze(-1)
                    if self.completeness_head is not None
                    else None
                ),
            )

    return _TemporalEvidenceReranker()


@dataclass(frozen=True)
class MultiTaskLossConfig:
    start_weight: float = 1.0
    end_weight: float = 1.0
    frame_relevance_weight: float = 1.0
    atom_support_weight: float = 1.0
    relation_support_weight: float = 0.0

    def validate(self, *, relation_head_enabled: bool) -> None:
        weights = (
            self.start_weight,
            self.end_weight,
            self.frame_relevance_weight,
            self.atom_support_weight,
            self.relation_support_weight,
        )
        if any(weight < 0 for weight in weights):
            raise ValueError("loss weights must be nonnegative")
        if self.relation_support_weight and not relation_head_enabled:
            raise ValueError("relation loss cannot be enabled without its gated model head")


def compute_multitask_loss(
    output: TemporalRerankerOutput,
    targets: Mapping[str, Any],
    masks: Mapping[str, Any],
    config: MultiTaskLossConfig,
) -> tuple[Any, Mapping[str, Any]]:
    """Compute the plan's pairwise rank + boundary/frame/atom/relation loss."""

    try:
        import torch
        import torch.nn.functional as functional
    except ImportError as exc:  # pragma: no cover - dependency-dependent path
        raise RuntimeError("PyTorch is required to compute training losses") from exc

    config.validate(relation_head_enabled=output.relation_support_logits is not None)
    scores = output.candidate_relevance
    relevance = targets["relevance"].to(scores.dtype)
    query_ids = targets["query_group_ids"]
    pair_losses = []
    for query_id in torch.unique(query_ids):
        group = query_ids == query_id
        positives = scores[group & (relevance > 0.5)]
        negatives = scores[group & (relevance <= 0.5)]
        if positives.numel() and negatives.numel():
            pair_losses.append(
                -functional.logsigmoid(
                    positives.reshape(-1, 1) - negatives.reshape(1, -1)
                ).mean()
            )
    pairwise = torch.stack(pair_losses).mean() if pair_losses else scores.sum() * 0.0

    start = _safe_cross_entropy(
        output.start_logits, targets["start_index"], functional
    )
    end = _safe_cross_entropy(output.end_logits, targets["end_index"], functional)
    frame = _masked_bce(
        output.frame_relevance_logits,
        targets["frame_relevance"],
        masks["frame_relevance"],
        functional,
    )
    atom = _masked_bce(
        output.atom_support_logits,
        targets["atom_support"],
        masks["atom_support"],
        functional,
    )
    relation = scores.sum() * 0.0
    if output.relation_support_logits is not None and config.relation_support_weight:
        relation = _masked_bce(
            output.relation_support_logits,
            targets["relation_support"],
            masks["relation_support"],
            functional,
        )

    components = {
        "pairwise_rank": pairwise,
        "start": start,
        "end": end,
        "frame_relevance": frame,
        "atom_support": atom,
        "relation_support": relation,
    }
    total = (
        pairwise
        + config.start_weight * start
        + config.end_weight * end
        + config.frame_relevance_weight * frame
        + config.atom_support_weight * atom
        + config.relation_support_weight * relation
    )
    return total, components


def _masked_bce(logits: Any, targets: Any, mask: Any, functional: Any) -> Any:
    losses = functional.binary_cross_entropy_with_logits(
        logits, targets.to(logits.dtype), reduction="none"
    )
    weights = mask.to(losses.dtype)
    return (losses * weights).sum() / weights.sum().clamp_min(1.0)


def _safe_cross_entropy(logits: Any, targets: Any, functional: Any) -> Any:
    valid = targets >= 0
    if not valid.any():
        return logits.sum() * 0.0
    return functional.cross_entropy(logits[valid], targets[valid])


def _compatible_head_count(hidden_dim: int) -> int:
    for count in (8, 4, 2, 1):
        if hidden_dim % count == 0:
            return count
    return 1
