"""Seal a real footage-session manifest from recorded files.

Person 5 runs this after recording. It measures what is actually on disk — streamed
SHA-256 and FFprobe container facts — merges that with the frozen plan in
``data/session_plan.json``, and writes a sealed manifest to ``data/manifests/``.

Nothing here invents a value. Every field is either measured from the file, read from
the plan, or supplied explicitly on the command line. Authorization and consent have no
defaults: they must be asserted by the person who collected the footage (§21.7).

Usage
-----
Inspect what would be written (no file touched)::

    python data/tools/prepare_session_manifest.py --session sess_001 \
        --collected-by "person5" --collection-date 2026-08-03 \
        --retention-policy "delete-after-event" --consent-recorded --dry-run

Write the sealed manifest::

    python data/tools/prepare_session_manifest.py --session sess_001 \
        --collected-by "person5" --collection-date 2026-08-03 \
        --retention-policy "delete-after-event" --consent-recorded

Check every already-sealed manifest still matches its files::

    python data/tools/prepare_session_manifest.py --verify-all
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from eval import schema as S  # noqa: E402

PLAN_PATH = _REPO / "data" / "session_plan.json"
MANIFESTS_DIR = _REPO / "data" / "manifests"

# Gate G1 (§11.4) requires one uninterrupted hour.
G1_MIN_DURATION_S = 3600.0

VIDEO_SUFFIXES = (".mp4", ".mkv", ".mov", ".avi", ".ts")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------- #
# Locating ffprobe (offline rule §8.4 — prefer the vendored binary)
# --------------------------------------------------------------------------- #

def find_ffprobe() -> str:
    vendored = _REPO / "tools" / "ffmpeg" / "ffmpeg-master-latest-win64-gpl" / "bin"
    for name in ("ffprobe.exe", "ffprobe"):
        candidate = vendored / name
        if candidate.exists():
            return str(candidate)
    found = shutil.which("ffprobe")
    if found:
        return found
    raise SystemExit(
        "ffprobe not found. Expected the vendored binary under "
        "tools/ffmpeg/ffmpeg-master-latest-win64-gpl/bin/ or ffprobe on PATH."
    )


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #

def streamed_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    """Streamed SHA-256. Must equal ingestion's hash for the same file (Member 1)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_rational(value: str | None) -> float | None:
    """FFprobe reports frame rates as 'num/den'."""
    if not value or value == "0/0":
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            num_f, den_f = float(num), float(den)
        except ValueError:
            return None
        return num_f / den_f if den_f else None
    try:
        return float(value)
    except ValueError:
        return None


def probe(path: Path, ffprobe: str) -> dict:
    """Container facts straight from FFprobe. No inference, no defaults."""
    result = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "format=duration,format_name",
            "-show_entries", "stream=codec_name,r_frame_rate,avg_frame_rate,time_base,nb_frames",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed on {path}:\n{result.stderr.strip()}")

    data = json.loads(result.stdout or "{}")
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    if not streams:
        raise SystemExit(f"{path} contains no video stream.")
    stream = streams[0]

    duration = fmt.get("duration")
    if duration is None:
        raise SystemExit(f"{path} has no container duration; cannot seal a manifest.")

    container = (fmt.get("format_name") or "").split(",")[0]
    fps = _parse_rational(stream.get("r_frame_rate")) or _parse_rational(
        stream.get("avg_frame_rate")
    )

    return {
        "duration_s": round(float(duration), 3),
        "codec": stream.get("codec_name") or "",
        "container": container,
        "declared_fps": round(fps, 6) if fps else 0.0,
        "timebase": stream.get("time_base"),
        "avg_frame_rate": stream.get("avg_frame_rate"),
        "r_frame_rate": stream.get("r_frame_rate"),
    }


# --------------------------------------------------------------------------- #
# Plan access
# --------------------------------------------------------------------------- #

def load_plan() -> dict:
    if not PLAN_PATH.exists():
        raise SystemExit(f"Missing recording plan: {PLAN_PATH}")
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def all_planned(plan: dict) -> list[dict]:
    """Staged sessions plus any external generalization-probe sessions."""
    return list(plan["sessions"]) + list(
        plan.get("external_pool", {}).get("sessions", [])
    )


def plan_session(plan: dict, session_id: str) -> dict:
    for entry in all_planned(plan):
        if entry["session_id"] == session_id:
            return entry
    known = ", ".join(e["session_id"] for e in all_planned(plan))
    raise SystemExit(f"Unknown session '{session_id}'. Planned sessions: {known}")


def session_files(plan: dict, entry: dict) -> list[Path]:
    directory = _REPO / plan["footage_root"] / entry["session_dir"]
    if not directory.exists():
        raise SystemExit(
            f"No footage directory for {entry['session_id']}: {directory}\n"
            "Record the session first, or check the path in data/session_plan.json."
        )
    files = sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    )
    if not files:
        raise SystemExit(f"No video files found in {directory}")
    return files


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def _authorization_notes(args: argparse.Namespace, entry: dict) -> str:
    """Free-text notes, with external-pool provenance recorded verbatim."""
    base = args.authorization_notes or ""
    if entry.get("pool") != "external":
        return base
    recorded = (
        f"source={args.source_name}; url={args.source_url}; "
        f"licence={args.source_licence}; consent_basis={args.consent_basis}; "
        f"licence_checked_on={args.licence_checked_on}"
    )
    return f"{base} | {recorded}".strip(" |")


def build_manifest(plan: dict, entry: dict, args: argparse.Namespace) -> tuple[dict, list[str]]:
    """Return (sealed manifest, warnings). Raises SystemExit on a hard failure."""
    ffprobe = find_ffprobe()
    files = session_files(plan, entry)
    warnings: list[str] = []

    cameras = entry["camera_ids"]
    if len(files) > len(cameras):
        warnings.append(
            f"{len(files)} files but {len(cameras)} planned camera(s); "
            "each extra file is attributed to the first camera - verify before sealing."
        )

    footage_files = []
    for index, path in enumerate(files):
        facts = probe(path, ffprobe)
        camera_id = cameras[index] if index < len(cameras) else cameras[0]
        rel = path.relative_to(_REPO).as_posix()

        footage_files.append({
            "file_id": f"{entry['session_id']}_{path.stem}",
            "camera_id": camera_id,
            "logical_path": rel,
            "source_sha256": streamed_sha256(path),
            "duration_s": facts["duration_s"],
            "codec": facts["codec"],
            "container": facts["container"],
            "declared_fps": facts["declared_fps"],
            "recording_start": args.recording_start,
            "timebase": facts["timebase"],
            "synthetic": False,
        })

        if facts["r_frame_rate"] != facts["avg_frame_rate"]:
            warnings.append(
                f"{path.name}: variable frame rate "
                f"(r={facts['r_frame_rate']} avg={facts['avg_frame_rate']}). "
                "PTS-safe ingestion prefers constant frame rate."
            )

    # ---- Gate G1: the hour must be ONE uninterrupted file --------------------
    if entry.get("g1_anchor"):
        if len(footage_files) != 1:
            raise SystemExit(
                f"G1 FAILURE: {entry['session_id']} is the Gate G1 anchor and must be exactly "
                f"one continuous file, but {len(footage_files)} were found.\n"
                "The camera almost certainly split the recording. This cannot be repaired by "
                "concatenation: G1 requires one file with monotonic PTS. Re-record."
            )
        actual = footage_files[0]["duration_s"]
        if actual < G1_MIN_DURATION_S:
            raise SystemExit(
                f"G1 FAILURE: {entry['session_id']} is {actual:.1f} s; Gate G1 (plan 11.4) "
                f"requires at least {G1_MIN_DURATION_S:.0f} s uninterrupted.\n"
                "A 1800 s or 2700 s result means the camera hit a file-split limit. Re-record."
            )

    planned = entry.get("planned_duration_s")
    if planned:
        total = sum(f["duration_s"] for f in footage_files)
        if total < planned * 0.9:
            warnings.append(
                f"recorded {total:.0f} s against a planned {planned} s "
                "- confirm no scene from the shot list was dropped."
            )

    manifest = {
        "manifest_schema_version": "1.1.0",
        "session_id": entry["session_id"],
        "scenario_id": entry["scenario_id"],
        "pool": entry["pool"],
        "camera_ids": cameras,
        "footage_files": footage_files,
        "authorization": {
            "status": args.authorization_status,
            "consent_recorded": bool(args.consent_recorded),
            "retention_policy": args.retention_policy,
            "notes": _authorization_notes(args, entry),
        },
        "provenance": {
            "collected_by": args.collected_by,
            "collection_date": args.collection_date,
            "staged_scenario_description": entry.get("staged_scenario_description", ""),
            "organizer_delivery_ref": args.organizer_delivery_ref,
        },
        "synthetic": False,
        "notes": args.notes or f"Split: {entry['split']}. Sealed by prepare_session_manifest.py.",
        "created_at": _dt.datetime.now().replace(microsecond=0).isoformat(),
    }

    if entry["pool"] == "organizer" and not args.organizer_delivery_ref:
        warnings.append(
            "organizer pool without --organizer-delivery-ref; provenance is incomplete."
        )

    if entry["pool"] == "external":
        # External footage is other people's video. Its licence and consent basis
        # ARE its provenance, so these block rather than warn -- a warning is
        # satisfied by ignoring it, and a sealed manifest asserting authorization
        # over footage whose source is unrecorded is exactly the claim this tool
        # exists to prevent. Dedicated flags, not substring-matched free text:
        # the old check passed if the words "licence" and "source" appeared
        # anywhere in any order.
        missing = [
            name
            for name, value in (
                ("--source-name", args.source_name),
                ("--source-url", args.source_url),
                ("--source-licence", args.source_licence),
                ("--consent-basis", args.consent_basis),
                ("--licence-checked-on", args.licence_checked_on),
            )
            if not value
        ]
        if missing:
            raise SystemExit(
                "external pool: refusing to seal without recorded provenance.\n"
                f"  missing: {', '.join(missing)}\n"
                "Person 5 reads the actual licence and consent documentation before\n"
                "downloading, and records what was found. Do not rely on a summary,\n"
                "including one written inside this repository."
            )
        if not _ISO_DATE.match(args.licence_checked_on):
            raise SystemExit("--licence-checked-on must be an ISO date, YYYY-MM-DD")
        if "PLACEHOLDER" in entry.get("staged_scenario_description", ""):
            raise SystemExit(
                "external pool: session_plan.json still holds the PLACEHOLDER description "
                f"for {entry['session_id']}. Replace it with the real source, licence and "
                "consent basis before sealing."
            )

    return S.seal_manifest(manifest), warnings


def validate(manifest: dict) -> list[str]:
    """Schema + cross-field validation. Also re-checks the seal."""
    return list(S.validate_footage_manifest(manifest).errors)


# --------------------------------------------------------------------------- #
# Verify mode
# --------------------------------------------------------------------------- #

def verify_all() -> int:
    """Re-check every sealed manifest against the bytes still on disk."""
    plan = load_plan()
    planned_ids = {e["session_id"] for e in plan["sessions"]}
    failures = 0
    checked = 0

    for path in sorted(MANIFESTS_DIR.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("synthetic"):
            continue  # synthetic seed manifests reference no real bytes
        checked += 1

        if not S.verify_manifest_hash(manifest):
            print(f"  FAIL {path.name}: content_hash mismatch - manifest was edited after sealing")
            failures += 1
            continue

        for record in manifest.get("footage_files", []):
            logical = record.get("logical_path")
            if not logical:
                continue
            target = _REPO / logical
            if not target.exists():
                print(f"  WARN {path.name}: {logical} is not on this machine (footage is untracked)")
                continue
            actual = streamed_sha256(target)
            if actual != record["source_sha256"]:
                print(f"  FAIL {path.name}: {logical} hash changed since sealing")
                failures += 1
            else:
                print(f"  ok   {path.name}: {logical}")

        if manifest.get("session_id") not in planned_ids:
            print(f"  note {path.name}: session not in data/session_plan.json")

    if checked == 0:
        print("No real (non-synthetic) manifests sealed yet.")
        return 0
    print(f"\n{checked} real manifest(s) checked, {failures} failure(s).")
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seal a real footage-session manifest from recorded files.",
    )
    parser.add_argument("--session", help="session_id from data/session_plan.json")
    parser.add_argument("--collected-by", help="Who collected the footage (Person 5)")
    parser.add_argument("--collection-date", help="ISO date, YYYY-MM-DD")
    parser.add_argument("--retention-policy", help='e.g. "delete-after-event"')
    parser.add_argument(
        "--consent-recorded", action="store_true",
        help="Assert that every visible participant consented BEFORE recording",
    )
    parser.add_argument(
        "--authorization-status", default="authorized",
        choices=["authorized", "pending", "unauthorized"],
    )
    parser.add_argument("--authorization-notes", default=None)
    parser.add_argument("--organizer-delivery-ref", default=None)
    # External-pool provenance. Required to seal an external session; see
    # build_manifest. Recorded into authorization.notes so the sealed manifest
    # carries the licence basis rather than a free-text assurance.
    parser.add_argument("--source-name", default=None, help="external: dataset/source name")
    parser.add_argument("--source-url", default=None, help="external: source URL")
    parser.add_argument("--source-licence", default=None, help="external: licence identifier")
    parser.add_argument("--consent-basis", default=None,
                        help="external: how the source documented participant consent")
    parser.add_argument("--licence-checked-on", default=None,
                        help="external: ISO date you read the licence yourself")
    parser.add_argument("--recording-start", default=None,
                        help="Absolute recording start if known; never inferred")
    parser.add_argument("--notes", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Print, do not write")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing manifest")
    parser.add_argument("--verify-all", action="store_true",
                        help="Re-hash footage and check every sealed manifest")
    parser.add_argument("--list", action="store_true", help="List planned sessions and exit")

    args = parser.parse_args(argv)

    if args.verify_all:
        return verify_all()

    plan = load_plan()

    if args.list:
        print(f"{'session':<10} {'scenario':<18} {'split':<6} {'cam':<11} {'planned':>8}  recorded")
        for entry in all_planned(plan):
            directory = _REPO / plan["footage_root"] / entry["session_dir"]
            files = []
            if directory.exists():
                files = [p for p in directory.iterdir()
                         if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES]
            state = f"{len(files)} file(s)" if files else "-- empty --"
            sealed = (MANIFESTS_DIR / f"{entry['session_id']}.json").exists()
            print(
                f"{entry['session_id']:<10} {entry['scenario_id']:<18} {entry['split']:<6} "
                f"{entry['camera_ids'][0]:<11} {entry['planned_duration_s']:>7}s  "
                f"{state}{'  [sealed]' if sealed else ''}"
                f"{'  [external probe]' if entry['pool'] == 'external' else ''}"
            )
        return 0

    missing = [
        name for name, value in (
            ("--session", args.session),
            ("--collected-by", args.collected_by),
            ("--collection-date", args.collection_date),
            ("--retention-policy", args.retention_policy),
        ) if not value
    ]
    if missing:
        parser.error("missing required argument(s): " + ", ".join(missing))

    if not _ISO_DATE.match(args.collection_date):
        parser.error("--collection-date must be an ISO date, YYYY-MM-DD")

    # Consent is asserted, never defaulted. Staged footage of real people does not
    # get a sealed manifest without it (plan 21.7).
    if args.authorization_status == "authorized" and not args.consent_recorded:
        parser.error(
            "--authorization-status=authorized requires --consent-recorded.\n"
            "If consent is not yet on file, seal with --authorization-status=pending; "
            "the footage must not be used for labelling until it is."
        )

    entry = plan_session(plan, args.session)
    destination = MANIFESTS_DIR / f"{entry['session_id']}.json"

    if destination.exists() and not (args.force or args.dry_run):
        raise SystemExit(
            f"{destination} already exists. Manifests are immutable once sealed.\n"
            "Re-sealing after annotation has begun destroys the guarantee that the footage "
            "was unmodified during labelling. Use --force only if nothing has been annotated yet."
        )

    manifest, warnings = build_manifest(plan, entry, args)
    errors = validate(manifest)

    print(json.dumps(manifest, indent=2, sort_keys=True))
    print()

    for warning in warnings:
        print(f"WARNING: {warning}")

    if errors:
        print("\nSCHEMA ERRORS - nothing written:")
        for error in errors:
            print(f"  - {error}")
        return 1

    total = sum(f["duration_s"] for f in manifest["footage_files"])
    print(
        f"{entry['session_id']}: {len(manifest['footage_files'])} file(s), "
        f"{total:.1f} s ({total / 60:.1f} min), split={entry['split']}, pool={entry['pool']}"
    )
    if entry.get("g1_anchor"):
        print(f"Gate G1 anchor: PASSED the one-continuous-file and >= {G1_MIN_DURATION_S:.0f}s checks.")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    destination.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"\nSealed -> {destination.relative_to(_REPO).as_posix()}")
    print("This manifest is now immutable. Person 1 may begin watching.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
