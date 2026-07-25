"""Atomic, resumable checkpoints with experiment and RNG validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import pickle
import random
import subprocess
import tempfile
from typing import Any, Mapping, Sequence


CHECKPOINT_FORMAT_VERSION = "raziel-temporal-reranker-v1"
CRITICAL_RESUME_FIELDS = (
    "dataset_manifest_hash",
    "split_hash",
    "feature_cache_version",
    "model_revision_hashes",
)


@dataclass(frozen=True)
class CheckpointMetadata:
    run_id: str
    parent_run_id: str | None
    epoch: int
    global_step: int
    microbatch_position: int
    gradient_accumulation_position: int
    sampler_epoch: int
    sampler_offset: int
    resolved_config: Mapping[str, Any]
    command_line: tuple[str, ...]
    git_commit: str
    git_dirty: bool
    dataset_manifest_hash: str
    split_hash: str
    feature_cache_version: str
    model_revision_hashes: Mapping[str, str]
    best_metric: float | None
    early_stopping_state: Mapping[str, Any]
    evaluation_history: tuple[Mapping[str, Any], ...]
    device: str
    precision: str
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        parent_run_id: str | None,
        epoch: int,
        global_step: int,
        microbatch_position: int,
        gradient_accumulation_position: int,
        sampler_epoch: int,
        sampler_offset: int,
        resolved_config: Mapping[str, Any],
        command_line: Sequence[str],
        dataset_manifest_hash: str,
        split_hash: str,
        feature_cache_version: str,
        model_revision_hashes: Mapping[str, str],
        best_metric: float | None,
        early_stopping_state: Mapping[str, Any],
        evaluation_history: Sequence[Mapping[str, Any]],
        device: str,
        precision: str,
        repository: str | Path = ".",
    ) -> "CheckpointMetadata":
        commit, dirty = git_state(repository)
        return cls(
            run_id=run_id,
            parent_run_id=parent_run_id,
            epoch=epoch,
            global_step=global_step,
            microbatch_position=microbatch_position,
            gradient_accumulation_position=gradient_accumulation_position,
            sampler_epoch=sampler_epoch,
            sampler_offset=sampler_offset,
            resolved_config=dict(resolved_config),
            command_line=tuple(command_line),
            git_commit=commit,
            git_dirty=dirty,
            dataset_manifest_hash=dataset_manifest_hash,
            split_hash=split_hash,
            feature_cache_version=feature_cache_version,
            model_revision_hashes=dict(model_revision_hashes),
            best_metric=best_metric,
            early_stopping_state=dict(early_stopping_state),
            evaluation_history=tuple(dict(item) for item in evaluation_history),
            device=device,
            precision=precision,
            created_at=datetime.now(timezone.utc).isoformat(),
        )


@dataclass
class CheckpointPayload:
    metadata: CheckpointMetadata
    model_state: Mapping[str, Any]
    optimizer_state: Mapping[str, Any]
    scheduler_state: Mapping[str, Any]
    amp_scaler_state: Mapping[str, Any] | None
    rng_state: Mapping[str, Any]
    format_version: str = CHECKPOINT_FORMAT_VERSION


@dataclass(frozen=True)
class ResumeValidation:
    allowed: bool
    exact_continuation: bool
    blockers: tuple[str, ...]
    child_run_reasons: tuple[str, ...]


def capture_rng_state() -> dict[str, Any]:
    state: dict[str, Any] = {"python": random.getstate()}
    try:
        import numpy

        state["numpy"] = numpy.random.get_state()
    except ImportError:
        state["numpy"] = None
    try:
        import torch

        state["torch_cpu"] = torch.get_rng_state()
        state["torch_cuda"] = (
            torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
        )
    except ImportError:
        state["torch_cpu"] = None
        state["torch_cuda"] = None
    return state


def validate_rng_state(state: Mapping[str, Any]) -> None:
    required = {"python", "numpy", "torch_cpu", "torch_cuda"}
    missing = required.difference(state)
    if missing:
        raise ValueError(f"checkpoint RNG state is incomplete: {sorted(missing)}")
    probe = random.Random()
    try:
        probe.setstate(state["python"])
    except Exception as exc:
        raise ValueError("invalid Python RNG state") from exc


def restore_rng_state(state: Mapping[str, Any]) -> None:
    validate_rng_state(state)
    random.setstate(state["python"])
    if state["numpy"] is not None:
        try:
            import numpy
        except ImportError as exc:
            raise RuntimeError("NumPy RNG exists in checkpoint but NumPy is unavailable") from exc
        numpy.random.set_state(state["numpy"])
    if state["torch_cpu"] is not None:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("PyTorch RNG exists in checkpoint but PyTorch is unavailable") from exc
        torch.set_rng_state(state["torch_cpu"])
        if state["torch_cuda"] is not None:
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA RNG exists in checkpoint but CUDA is unavailable")
            torch.cuda.set_rng_state_all(state["torch_cuda"])


def save_checkpoint_atomic(path: str | Path, payload: CheckpointPayload) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    validate_checkpoint_payload(payload)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        _fsync_directory(destination.parent)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return destination


def load_checkpoint(path: str | Path) -> CheckpointPayload:
    # Checkpoints are trusted local experiment artifacts; never load untrusted files.
    with Path(path).open("rb") as handle:
        payload = pickle.load(handle)
    if not isinstance(payload, CheckpointPayload):
        raise ValueError("not a RAZIEL temporal-reranker checkpoint")
    validate_checkpoint_payload(payload)
    return payload


def validate_checkpoint_payload(payload: CheckpointPayload) -> None:
    if payload.format_version != CHECKPOINT_FORMAT_VERSION:
        raise ValueError(
            f"checkpoint format {payload.format_version!r} is not {CHECKPOINT_FORMAT_VERSION!r}"
        )
    metadata = payload.metadata
    if not metadata.run_id:
        raise ValueError("checkpoint run_id is required")
    for name in CRITICAL_RESUME_FIELDS:
        if not getattr(metadata, name):
            raise ValueError(f"checkpoint metadata field {name!r} is required")
    if metadata.global_step < 0 or metadata.epoch < 0:
        raise ValueError("checkpoint progress counters must be nonnegative")
    validate_rng_state(payload.rng_state)


def validate_resume(
    saved: CheckpointMetadata,
    current: CheckpointMetadata,
    *,
    migration_record: str | None = None,
) -> ResumeValidation:
    critical_mismatches: list[str] = []
    for name in CRITICAL_RESUME_FIELDS:
        if getattr(saved, name) != getattr(current, name):
            critical_mismatches.append(f"{name} mismatch")
    if critical_mismatches and not migration_record:
        return ResumeValidation(
            allowed=False,
            exact_continuation=False,
            blockers=tuple(critical_mismatches),
            child_run_reasons=(),
        )

    blockers: list[str] = []
    child_reasons: list[str] = []
    if saved.resolved_config != current.resolved_config:
        child_reasons.append("resolved hyperparameters changed")
    if saved.device != current.device:
        child_reasons.append("device changed")
    if saved.precision != current.precision:
        child_reasons.append("precision changed")
    if critical_mismatches and migration_record:
        child_reasons.append(f"recorded migration: {migration_record}")
    if child_reasons and current.parent_run_id != saved.run_id:
        blockers.append("changed experiment must use a child run whose parent_run_id is the saved run")
    if not child_reasons and current.run_id != saved.run_id:
        blockers.append("exact continuation must retain the immutable run_id")
    return ResumeValidation(
        allowed=not blockers,
        exact_continuation=not child_reasons and not blockers,
        blockers=tuple(blockers),
        child_run_reasons=tuple(child_reasons),
    )


def find_auto_resume(run_directory: str | Path) -> Path | None:
    candidate = Path(run_directory) / "last.ckpt"
    return candidate if candidate.is_file() else None


def rotate_numbered_checkpoints(
    run_directory: str | Path, *, keep_last: int = 3
) -> tuple[Path, ...]:
    if keep_last < 0:
        raise ValueError("keep_last must be nonnegative")
    directory = Path(run_directory)
    numbered = sorted(
        directory.glob("step-*.ckpt"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    removed: list[Path] = []
    for path in numbered[keep_last:]:
        # Only reproducible numbered checkpoints are pruned; best/last/milestones are untouched.
        path.unlink()
        removed.append(path)
    return tuple(removed)


def git_state(repository: str | Path) -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.CalledProcessError):
        return "not_available", True


def metadata_dict(metadata: CheckpointMetadata) -> dict[str, Any]:
    return asdict(metadata)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
