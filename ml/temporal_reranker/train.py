"""Train the Temporal Evidence Reranker from frozen feature records.

This CLI never downloads a model.  It refuses to start before the exact data
gate passes and supports atomic ``--resume auto`` checkpoints.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import random
import signal
import sys
import time
from typing import Any, Mapping, Sequence

from ml.temporal_reranker.checkpoint import (
    CheckpointMetadata,
    CheckpointPayload,
    capture_rng_state,
    find_auto_resume,
    load_checkpoint,
    restore_rng_state,
    rotate_numbered_checkpoints,
    save_checkpoint_atomic,
    validate_resume,
)
from ml.temporal_reranker.dataset import (
    DatasetManifest,
    evaluate_training_data_gate,
    load_jsonl,
)
from ml.temporal_reranker.jobs import GPULease, LocalJobRegistry
from ml.temporal_reranker.model import (
    MultiTaskLossConfig,
    TemporalRerankerConfig,
    build_torch_model,
    compute_multitask_loss,
)


class StopAfterStep:
    requested = False

    def request(self, signum: int, frame: Any) -> None:
        self.requested = True


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--parent-run-id")
    parser.add_argument("--resume", default="none", help="'auto', 'none', or a checkpoint path")
    parser.add_argument("--migration-record")
    parser.add_argument("--gate-only", action="store_true")
    parser.add_argument("--relation-logic-head", action="store_true")
    parser.add_argument("--architecture", choices=("gru", "transformer"), default="gru")
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--gradient-accumulation", type=int, default=1)
    parser.add_argument("--checkpoint-steps", type=int, default=200)
    parser.add_argument("--checkpoint-seconds", type=float, default=600.0)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--precision", choices=("fp32", "fp16", "bf16"), default="fp32")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--lease-path", default=".raziel/gpu.lease")
    parser.add_argument("--job-registry", default=".raziel/jobs.sqlite")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.batch_size <= 0 or args.epochs <= 0 or args.gradient_accumulation <= 0:
        raise ValueError("batch size, epochs, and gradient accumulation must be positive")
    if args.checkpoint_steps <= 0:
        raise ValueError("checkpoint_steps must be positive")
    if not 600 <= args.checkpoint_seconds <= 900:
        raise ValueError("checkpoint_seconds must remain within the plan's 10-15 minute window")
    records = load_jsonl(args.dataset)
    manifest = DatasetManifest.load(args.manifest)
    gate = evaluate_training_data_gate(
        records,
        manifest,
        request_relation_logic_head=args.relation_logic_head,
    )
    print(json.dumps(asdict(gate), indent=2, sort_keys=True))
    if not gate.passed:
        return 2
    if args.gate_only:
        return 0
    return run_training(args, records, manifest)


def run_training(
    args: argparse.Namespace,
    records: Sequence[Any],
    manifest: DatasetManifest,
) -> int:
    try:
        import torch
    except ImportError:
        print(
            "PyTorch is unavailable. Use the locked training environment; no model was trained.",
            file=sys.stderr,
        )
        return 3

    train_records = [record for record in records if record.split == "train"]
    if not train_records:
        raise RuntimeError("training split is empty")
    feature_order = sorted(train_records[0].sequence_features)
    if any(sorted(record.sequence_features) != feature_order for record in train_records):
        raise ValueError("all records must use the same frozen feature schema")
    atom_order = sorted({key for record in train_records for key in record.atom_features})
    relation_order = sorted(
        {key for record in train_records for key in record.relation_features}
    )
    resolved_config = {
        "architecture": args.architecture,
        "hidden_dim": args.hidden_dim,
        "batch_size": args.batch_size,
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "weight_decay": args.weight_decay,
        "gradient_accumulation": args.gradient_accumulation,
        "feature_order": feature_order,
        "atom_order": atom_order,
        "relation_order": relation_order,
        "relation_logic_head": args.relation_logic_head,
        "seed": args.seed,
    }
    metadata = CheckpointMetadata.create(
        run_id=args.run_id,
        parent_run_id=args.parent_run_id,
        epoch=0,
        global_step=0,
        microbatch_position=0,
        gradient_accumulation_position=0,
        sampler_epoch=0,
        sampler_offset=0,
        resolved_config=resolved_config,
        command_line=sys.argv,
        dataset_manifest_hash=manifest.dataset_manifest_hash,
        split_hash=manifest.split_hash,
        feature_cache_version=manifest.feature_cache_version,
        model_revision_hashes=manifest.model_revision_hashes,
        best_metric=None,
        early_stopping_state={},
        evaluation_history=(),
        device=args.device,
        precision=args.precision,
    )
    config = TemporalRerankerConfig(
        input_dim=len(feature_order),
        hidden_dim=args.hidden_dim,
        architecture=args.architecture,
        relation_logic_head=args.relation_logic_head,
    )
    model = build_torch_model(config).to(args.device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=max(1, args.epochs)
    )
    amp_enabled = args.device.startswith("cuda") and args.precision in {"fp16", "bf16"}
    scaler = torch.amp.GradScaler("cuda", enabled=amp_enabled and args.precision == "fp16")
    loss_config = MultiTaskLossConfig(
        relation_support_weight=1.0 if args.relation_logic_head else 0.0
    )

    start_epoch = 0
    global_step = 0
    sampler_offset = 0
    resume_path = (
        find_auto_resume(args.run_dir)
        if args.resume == "auto"
        else (Path(args.resume) if args.resume != "none" else None)
    )
    if resume_path is not None:
        payload = load_checkpoint(resume_path)
        validation = validate_resume(
            payload.metadata, metadata, migration_record=args.migration_record
        )
        if not validation.allowed:
            raise RuntimeError("resume refused: " + "; ".join(validation.blockers))
        model.load_state_dict(payload.model_state)
        optimizer.load_state_dict(payload.optimizer_state)
        scheduler.load_state_dict(payload.scheduler_state)
        if payload.amp_scaler_state is not None:
            scaler.load_state_dict(payload.amp_scaler_state)
        restore_rng_state(payload.rng_state)
        start_epoch = payload.metadata.epoch
        global_step = payload.metadata.global_step
        sampler_offset = payload.metadata.sampler_offset
    else:
        random.seed(args.seed)
        torch.manual_seed(args.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(args.seed)

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    registry = LocalJobRegistry(args.job_registry)
    try:
        registry.enqueue(
            run_id=args.run_id,
            parent_run_id=args.parent_run_id,
            mode="train",
            payload={"run_dir": str(run_dir), "resolved_config": resolved_config},
        )
    except Exception:
        # Existing immutable run IDs are expected on resume.
        existing = registry.get(args.run_id)
        if existing["state"] not in {"queued", "running"}:
            raise
    registry.start(args.run_id)

    stop = StopAfterStep()
    previous_handlers = {
        sig: signal.signal(sig, stop.request)
        for sig in (signal.SIGINT, signal.SIGTERM)
        if hasattr(signal, sig.name)
    }
    query_numbers = {
        query_id: index
        for index, query_id in enumerate(sorted({record.query_id for record in train_records}))
    }
    last_save = time.monotonic()
    best_metric: float | None = None
    evaluation_history: list[Mapping[str, Any]] = []
    microbatch_position = 0

    try:
        with GPULease(args.lease_path, mode="train", owner=args.run_id):
            for epoch in range(start_epoch, args.epochs):
                order = list(range(len(train_records)))
                random.Random(args.seed + epoch).shuffle(order)
                if epoch == start_epoch and sampler_offset:
                    order = order[sampler_offset:]
                model.train()
                optimizer.zero_grad(set_to_none=True)
                epoch_losses: list[float] = []
                for batch_start in range(0, len(order), args.batch_size):
                    indices = order[batch_start : batch_start + args.batch_size]
                    batch_records = [train_records[index] for index in indices]
                    tensors = _collate(
                        batch_records,
                        feature_order,
                        atom_order,
                        relation_order,
                        query_numbers,
                        torch,
                        args.device,
                    )
                    dtype = (
                        torch.float16
                        if args.precision == "fp16"
                        else torch.bfloat16
                        if args.precision == "bf16"
                        else torch.float32
                    )
                    with torch.autocast(
                        device_type=args.device.split(":")[0],
                        enabled=amp_enabled,
                        dtype=dtype,
                    ):
                        output = model(**tensors["inputs"])
                        loss, _ = compute_multitask_loss(
                            output, tensors["targets"], tensors["masks"], loss_config
                        )
                        scaled_loss = loss / args.gradient_accumulation
                    scaler.scale(scaled_loss).backward()
                    microbatch_position += 1
                    final_batch = batch_start + len(indices) >= len(order)
                    optimizer_step_ready = (
                        microbatch_position >= args.gradient_accumulation or final_batch
                    )
                    if optimizer_step_ready:
                        scaler.step(optimizer)
                        scaler.update()
                        optimizer.zero_grad(set_to_none=True)
                        global_step += 1
                        microbatch_position = 0
                    sampler_offset = batch_start + len(indices)
                    epoch_losses.append(float(loss.detach().cpu()))
                    yield_requested = registry.yield_requested(args.run_id)
                    checkpoint_due = optimizer_step_ready and (
                        global_step % args.checkpoint_steps == 0
                        or time.monotonic() - last_save >= args.checkpoint_seconds
                        or stop.requested
                        or yield_requested
                    )
                    if checkpoint_due:
                        checkpoint = _save_training_checkpoint(
                            run_dir,
                            metadata,
                            model,
                            optimizer,
                            scheduler,
                            scaler,
                            epoch=epoch,
                            global_step=global_step,
                            sampler_offset=sampler_offset,
                            gradient_accumulation_position=0,
                            best_metric=best_metric,
                            evaluation_history=evaluation_history,
                        )
                        registry.heartbeat(
                            args.run_id,
                            checkpoint_path=str(checkpoint),
                            telemetry={"checkpoint_age_s": 0},
                        )
                        last_save = time.monotonic()
                        if yield_requested:
                            registry.mark_yielded(args.run_id, str(checkpoint))
                            return 0
                        if stop.requested:
                            registry.mark_yielded(args.run_id, str(checkpoint))
                            return 130
                scheduler.step()
                sampler_offset = 0
                mean_loss = sum(epoch_losses) / max(1, len(epoch_losses))
                evaluation_history.append({"epoch": epoch, "train_loss": mean_loss})
                if best_metric is None or mean_loss < best_metric:
                    best_metric = mean_loss
                    checkpoint = _build_payload(
                        metadata,
                        model,
                        optimizer,
                        scheduler,
                        scaler,
                        epoch=epoch + 1,
                        global_step=global_step,
                        sampler_offset=0,
                        gradient_accumulation_position=0,
                        best_metric=best_metric,
                        evaluation_history=evaluation_history,
                    )
                    save_checkpoint_atomic(run_dir / "best.ckpt", checkpoint)
            final = _save_training_checkpoint(
                run_dir,
                metadata,
                model,
                optimizer,
                scheduler,
                scaler,
                epoch=args.epochs,
                global_step=global_step,
                sampler_offset=0,
                gradient_accumulation_position=0,
                best_metric=best_metric,
                evaluation_history=evaluation_history,
            )
            registry.complete(args.run_id, exit_code=0)
            print(json.dumps({"status": "complete", "checkpoint": str(final)}))
            return 0
    except Exception as exc:
        registry.complete(args.run_id, exit_code=1, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)


def _collate(
    records: Sequence[Any],
    feature_order: Sequence[str],
    atom_order: Sequence[str],
    relation_order: Sequence[str],
    query_numbers: Mapping[str, int],
    torch: Any,
    device: str,
) -> Mapping[str, Mapping[str, Any]]:
    batch = len(records)
    time_steps = max(record.length for record in records)
    sequence = torch.zeros(batch, time_steps, len(feature_order), device=device)
    relative_time = torch.zeros(batch, time_steps, device=device)
    sequence_mask = torch.zeros(batch, time_steps, dtype=torch.bool, device=device)
    atoms = torch.zeros(batch, len(atom_order), time_steps, 1, device=device)
    atom_mask = torch.zeros(batch, len(atom_order), dtype=torch.bool, device=device)
    relations = torch.zeros(batch, len(relation_order), time_steps, 1, device=device)
    relation_mask = torch.zeros(
        batch, len(relation_order), dtype=torch.bool, device=device
    )
    relevance = torch.zeros(batch, device=device)
    start = torch.full((batch,), -1, dtype=torch.long, device=device)
    end = torch.full((batch,), -1, dtype=torch.long, device=device)
    frame_targets = torch.zeros(batch, time_steps, device=device)
    frame_mask = torch.zeros(batch, time_steps, dtype=torch.bool, device=device)
    atom_targets = torch.zeros(batch, len(atom_order), device=device)
    atom_target_mask = torch.zeros(
        batch, len(atom_order), dtype=torch.bool, device=device
    )
    relation_targets = torch.zeros(batch, len(relation_order), device=device)
    relation_target_mask = torch.zeros(
        batch, len(relation_order), dtype=torch.bool, device=device
    )
    query_groups = torch.zeros(batch, dtype=torch.long, device=device)
    atom_index = {name: index for index, name in enumerate(atom_order)}
    relation_index = {name: index for index, name in enumerate(relation_order)}
    for row, record in enumerate(records):
        length = record.length
        sequence[row, :length] = torch.tensor(
            record.ordered_feature_matrix(feature_order), device=device
        )
        relative_time[row, :length] = torch.tensor(
            record.sequence_features["relative_time"], device=device
        )
        sequence_mask[row, :length] = True
        for name, values in record.atom_features.items():
            column = atom_index[name]
            atoms[row, column, :length, 0] = torch.tensor(values, device=device)
            atom_mask[row, column] = True
        for name, values in record.relation_features.items():
            column = relation_index[name]
            relations[row, column, :length, 0] = torch.tensor(values, device=device)
            relation_mask[row, column] = True
        relevance[row] = record.labels.relevance
        if record.labels.start_index is not None:
            start[row] = record.labels.start_index
        if record.labels.end_index is not None:
            end[row] = record.labels.end_index
        for tick, label in enumerate(record.labels.frame_relevance):
            if label is not None:
                frame_targets[row, tick] = label
                frame_mask[row, tick] = True
        for name, label in record.labels.atom_support.items():
            if label is not None:
                column = atom_index[name]
                atom_targets[row, column] = label
                atom_target_mask[row, column] = True
        for name, label in record.labels.relation_support.items():
            if label is not None:
                column = relation_index[name]
                relation_targets[row, column] = label
                relation_target_mask[row, column] = True
        query_groups[row] = query_numbers[record.query_id]
    return {
        "inputs": {
            "sequence_features": sequence,
            "relative_time": relative_time,
            "sequence_mask": sequence_mask,
            "atom_features": atoms,
            "atom_mask": atom_mask,
            "relation_features": relations if relation_order else None,
            "relation_mask": relation_mask if relation_order else None,
        },
        "targets": {
            "relevance": relevance,
            "query_group_ids": query_groups,
            "start_index": start,
            "end_index": end,
            "frame_relevance": frame_targets,
            "atom_support": atom_targets,
            "relation_support": relation_targets,
        },
        "masks": {
            "frame_relevance": frame_mask,
            "atom_support": atom_target_mask,
            "relation_support": relation_target_mask,
        },
    }


def _save_training_checkpoint(
    run_dir: Path,
    metadata: CheckpointMetadata,
    model: Any,
    optimizer: Any,
    scheduler: Any,
    scaler: Any,
    **progress: Any,
) -> Path:
    payload = _build_payload(
        metadata, model, optimizer, scheduler, scaler, **progress
    )
    numbered = run_dir / f"step-{progress['global_step']:09d}.ckpt"
    save_checkpoint_atomic(numbered, payload)
    # Load verification occurs before last.ckpt advances or numbered retention runs.
    load_checkpoint(numbered)
    save_checkpoint_atomic(run_dir / "last.ckpt", payload)
    rotate_numbered_checkpoints(run_dir, keep_last=3)
    return run_dir / "last.ckpt"


def _build_payload(
    metadata: CheckpointMetadata,
    model: Any,
    optimizer: Any,
    scheduler: Any,
    scaler: Any,
    *,
    epoch: int,
    global_step: int,
    sampler_offset: int,
    gradient_accumulation_position: int,
    best_metric: float | None,
    evaluation_history: Sequence[Mapping[str, Any]],
) -> CheckpointPayload:
    updated = CheckpointMetadata(
        **{
            **metadata.__dict__,
            "epoch": epoch,
            "global_step": global_step,
            "sampler_epoch": epoch,
            "sampler_offset": sampler_offset,
            "gradient_accumulation_position": gradient_accumulation_position,
            "best_metric": best_metric,
            "evaluation_history": tuple(evaluation_history),
        }
    )
    return CheckpointPayload(
        metadata=updated,
        model_state=model.state_dict(),
        optimizer_state=optimizer.state_dict(),
        scheduler_state=scheduler.state_dict(),
        amp_scaler_state=scaler.state_dict(),
        rng_state=capture_rng_state(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
