#!/usr/bin/env python3
"""
Run one dispatch interval, optionally resuming from a disk checkpoint.

This is the atomic building block for parameter-specific checkpoint chains:

- cold start: no checkpoint, run one segment, save its terminal checkpoint
- hot start: load previous terminal checkpoint, apply the new dispatch targets,
  run one segment, save the new terminal checkpoint
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import run_dispatch_tds as rdt
from compare_dispatch_pair_hotstart import (
    activate_dispatch_target_transition,
    apply_second_dispatch_targets,
    compute_bf,
    dispatch_offset,
    prepare_system,
    run_segment,
)
import hotstart_checkpoint as hcp


def summarize_series(t: np.ndarray, f_dev_hz: np.ndarray) -> dict[str, float | int]:
    imin = int(np.argmin(f_dev_hz))
    imax = int(np.argmax(f_dev_hz))
    return {
        "samples": int(len(t)),
        "t_end_s": float(t[-1]),
        "min_hz": float(f_dev_hz[imin]),
        "t_min_s": float(t[imin]),
        "max_hz": float(f_dev_hz[imax]),
        "t_max_s": float(t[imax]),
        "final_hz": float(f_dev_hz[-1]),
        "abs_mean_hz": float(np.mean(np.abs(f_dev_hz))),
        "rms_hz": float(np.sqrt(np.mean(np.square(f_dev_hz)))),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dispatch-json", type=Path, default=None,
                        help="Existing dispatch JSON to replay through TDS.")
    parser.add_argument("--next-dispatch-json", type=Path, default=None,
                        help="Optional next dispatch JSON used to build a smooth governor target trajectory.")
    parser.add_argument("--hour", type=int, default=13,
                        help="Dispatch hour used when recomputing from AMS.")
    parser.add_argument("--dispatch", type=int, default=2,
                        help="Dispatch interval used when recomputing from AMS.")
    parser.add_argument("--label", type=str, default=None,
                        help="Output label. Defaults to the dispatch label.")
    parser.add_argument("--checkpoint-in", type=Path, default=None,
                        help="Checkpoint directory from the previous dispatch boundary.")
    parser.add_argument("--checkpoint-out", type=Path, default=None,
                        help="Explicit checkpoint directory for the current dispatch end state.")
    parser.add_argument("--checkpoints-dir", type=Path, default=rdt.RESULTS / "checkpoints")
    parser.add_argument("--results-dir", type=Path, default=rdt.RESULTS / "hotstart_segments")
    parser.add_argument("--opf-case", type=Path, default=rdt.DEFAULT_OPF_CASE)
    parser.add_argument("--dyn-case", type=Path, default=rdt.DEFAULT_DYN_CASE)
    parser.add_argument("--stable-dyn-case", type=Path, default=rdt.DEFAULT_STABLE_DYN_CASE)
    parser.add_argument("--curve-file", type=Path, default=rdt.DEFAULT_CURVE_FILE)
    parser.add_argument("--duration-seconds", type=int, default=900)
    parser.add_argument("--agc-interval", type=int, default=4)
    parser.add_argument("--kp", type=float, default=0.03)
    parser.add_argument("--ki", type=float, default=0.01)
    parser.add_argument("--dispatch-target-ramp-seconds", type=int, default=0)
    parser.add_argument(
        "--governor-target-schedule",
        choices=("step", "boundary_ramp", "midpoint_trajectory"),
        default="midpoint_trajectory",
        help="How to apply conventional generator dispatch targets when enabled.",
    )
    parser.add_argument("--init-mode", choices=("dispatch", "first"), default="first")
    parser.add_argument("--wind-prefix", action="append", default=None)
    parser.add_argument("--solar-prefix", action="append", default=None)
    parser.add_argument("--apply-governor-targets", dest="apply_governor_targets", action="store_true")
    parser.add_argument("--no-apply-governor-targets", dest="apply_governor_targets", action="store_false")
    parser.add_argument(
        "--apply-dg-targets",
        dest="apply_dg_targets",
        action="store_true",
        help="Deprecated and ignored. DG/PVD1/ESD1 dispatch targets are not applied.",
    )
    parser.add_argument(
        "--no-apply-dg-targets",
        dest="apply_dg_targets",
        action="store_false",
        help="Deprecated compatibility flag; DG/PVD1/ESD1 dispatch targets are never applied.",
    )
    parser.add_argument("--allow-signature-mismatch", action="store_true",
                        help="Do not fail when the checkpoint signature differs from the current settings.")
    parser.add_argument("--no-save-checkpoint", dest="save_checkpoint", action="store_false")
    parser.set_defaults(apply_governor_targets=False, apply_dg_targets=False, save_checkpoint=True)
    return parser.parse_args()


def load_dispatch_record(args: argparse.Namespace, curve: pd.DataFrame) -> tuple[rdt.DispatchRecord, Path | None]:
    if args.dispatch_json is not None:
        return rdt.DispatchRecord.from_json(args.dispatch_json), args.dispatch_json

    dispatch_record = rdt.compute_dispatch(
        args.hour,
        args.dispatch,
        curve,
        args.opf_case,
        args.duration_seconds,
    )
    return dispatch_record, None


def build_signature(
    args: argparse.Namespace,
    *,
    dyn_case: Path,
    wind_prefixes: tuple[str, ...],
    solar_prefixes: tuple[str, ...],
) -> dict[str, object]:
    dispatch_interval = getattr(args, "duration_seconds", None)
    if dispatch_interval is None:
        dispatch_interval = getattr(args, "dispatch_interval")
    dispatch_interval = int(dispatch_interval)
    return hcp.build_param_signature(
        kp=args.kp,
        ki=args.ki,
        agc_interval=args.agc_interval,
        init_mode=args.init_mode,
        dispatch_interval=dispatch_interval,
        curve_file=args.curve_file,
        dyn_case=args.dyn_case,
        stable_dyn_case=dyn_case,
        wind_prefixes=wind_prefixes,
        solar_prefixes=solar_prefixes,
        extra={
            "runner": "run_dispatch_hotstart",
            "apply_governor_targets": bool(getattr(args, "apply_governor_targets", True)),
            "dispatch_target_scope": "governor_only",
            "governor_target_schedule": str(getattr(args, "governor_target_schedule", "midpoint_trajectory")),
            "dispatch_target_ramp_seconds": int(getattr(args, "dispatch_target_ramp_seconds", 0)),
        },
    )


def main() -> None:
    args = parse_args()
    rdt.andes.config_logger(stream_level=30)

    curve = rdt.load_curve(args.curve_file)
    dispatch_record, dispatch_json_source = load_dispatch_record(args, curve)
    next_dispatch_record = (
        rdt.DispatchRecord.from_json(args.next_dispatch_json)
        if args.next_dispatch_json is not None else None
    )
    if not dispatch_record.converged:
        raise RuntimeError(f"Dispatch {dispatch_record.label} did not converge")

    label = args.label or dispatch_record.label
    args.results_dir.mkdir(parents=True, exist_ok=True)

    dyn_case = rdt.adapt_dyn_case(args.dyn_case, args.stable_dyn_case)
    wind_prefixes = rdt.normalize_prefixes(args.wind_prefix, rdt.DEFAULT_WIND_PREFIXES)
    solar_prefixes = rdt.normalize_prefixes(args.solar_prefix, rdt.DEFAULT_SOLAR_PREFIXES)
    signature = build_signature(
        args,
        dyn_case=dyn_case,
        wind_prefixes=wind_prefixes,
        solar_prefixes=solar_prefixes,
    )
    signature_hash = hcp.param_hash(signature)
    signature_path = hcp.ensure_family_manifest(args.checkpoints_dir, signature)
    dispatch_target_transition = None

    if args.checkpoint_in is None:
        sa, ctx = prepare_system(
            dispatch_record=dispatch_record,
            curve=curve,
            dyn_case=dyn_case,
            dispatch_interval=args.duration_seconds,
            init_mode=args.init_mode,
            wind_prefixes=wind_prefixes,
            solar_prefixes=solar_prefixes,
        )
        ace_integral = 0.0
        ace_raw = 0.0
        source_checkpoint = ""
        source_manifest = None
    else:
        sa, stored_ctx, agc_state, source_manifest = hcp.load_checkpoint(args.checkpoint_in)
        if not args.allow_signature_mismatch:
            hcp.validate_signature(signature, source_manifest["param_signature"])
        ctx = hcp.build_runtime_context(sa=sa, curve=curve, stored_ctx=stored_ctx)
        ace_integral = float(agc_state["ace_integral"])
        ace_raw = float(agc_state["ace_raw"])
        source_checkpoint = str(args.checkpoint_in)

    if args.apply_governor_targets:
        schedule_mode = args.governor_target_schedule
        build_duration = args.duration_seconds if schedule_mode == "midpoint_trajectory" else None
        dispatch_target_transition = apply_second_dispatch_targets(
            sa,
            ctx["link"],  # type: ignore[arg-type]
            dispatch_record,
            apply_governor_targets=True,
            apply_dg_targets=False,
            duration_seconds=build_duration,
            schedule_mode=schedule_mode,
            next_dispatch_record=next_dispatch_record if schedule_mode == "midpoint_trajectory" else None,
        )
        if schedule_mode == "boundary_ramp":
            dispatch_target_transition["ramp_seconds"] = int(args.dispatch_target_ramp_seconds)
        else:
            dispatch_target_transition["ramp_seconds"] = 0
        activate_dispatch_target_transition(sa, dispatch_target_transition, step=0)

    bf = compute_bf(sa, dispatch_record)
    t, f_dev_hz, ace_integral_end, ace_raw_end = run_segment(
        sa=sa,
        ctx=ctx,
        start_offset=dispatch_offset(dispatch_record, args.duration_seconds),
        duration_seconds=args.duration_seconds,
        agc_interval=args.agc_interval,
        kp=args.kp,
        ki=args.ki,
        bf=bf,
        ace_integral=ace_integral,
        ace_raw=ace_raw,
        local_start=0.0,
        include_initial=True,
        dispatch_target_transition=dispatch_target_transition,
    )

    dispatch_json_path = rdt.write_dispatch_json(dispatch_record, args.results_dir, label=label)
    csv_path, png_path = rdt.save_outputs(t, f_dev_hz, dispatch_record, args.results_dir, label=label)

    summary = {
        "label": label,
        "dispatch_label": dispatch_record.label,
        "hour": dispatch_record.hour,
        "dispatch": dispatch_record.dispatch,
        "dispatch_json": str(dispatch_json_path),
        "dispatch_json_source": str(dispatch_json_source) if dispatch_json_source is not None else "",
        "next_dispatch_json": str(args.next_dispatch_json) if args.next_dispatch_json is not None else "",
        "freq_csv": str(csv_path),
        "freq_png": str(png_path),
        "checkpoint_in": source_checkpoint,
        "signature_hash": signature_hash,
        "kp": args.kp,
        "ki": args.ki,
        "agc_interval": args.agc_interval,
        "init_mode": args.init_mode,
        "apply_governor_targets": int(args.apply_governor_targets),
        "apply_dg_targets": 0,
        "governor_target_schedule": args.governor_target_schedule,
        "dispatch_target_ramp_seconds": int(args.dispatch_target_ramp_seconds),
        "resume_mode": "checkpoint" if args.checkpoint_in is not None else "cold",
        "start_dae_t": float(source_manifest["end_dae_t"]) if source_manifest is not None else 0.0,
        "end_dae_t": float(sa.dae.t),
        "ace_integral_start": float(ace_integral),
        "ace_raw_start": float(ace_raw),
        "ace_integral_end": float(ace_integral_end),
        "ace_raw_end": float(ace_raw_end),
    }
    summary.update(summarize_series(t, f_dev_hz))
    summary_csv = args.results_dir / f"{label}_summary.csv"
    pd.DataFrame([summary]).to_csv(summary_csv, index=False)

    checkpoint_saved = ""
    if args.save_checkpoint:
        checkpoint_out = args.checkpoint_out or hcp.checkpoint_dir(args.checkpoints_dir, signature, dispatch_record.label)
        manifest = {
            "format": "deadband_hotstart_v1",
            "dispatch_label": dispatch_record.label,
            "hour": dispatch_record.hour,
            "dispatch": dispatch_record.dispatch,
            "dispatch_json": str(dispatch_json_path),
            "checkpoint_dir": str(checkpoint_out),
            "checkpoint_in": source_checkpoint,
            "curve_file": str(args.curve_file.resolve()),
            "dyn_case": str(args.dyn_case.resolve()),
            "stable_dyn_case": str(dyn_case.resolve()),
            "wind_prefixes": list(wind_prefixes),
            "solar_prefixes": list(solar_prefixes),
            "duration_seconds": int(args.duration_seconds),
            "agc_interval": int(args.agc_interval),
            "end_dae_t": float(sa.dae.t),
            "param_signature": signature,
            "param_signature_path": str(signature_path),
            "param_hash": signature_hash,
        }
        hcp.save_checkpoint(
            checkpoint_dir=checkpoint_out,
            sa=sa,
            ctx=ctx,
            ace_integral=ace_integral_end,
            ace_raw=ace_raw_end,
            manifest=manifest,
        )
        checkpoint_saved = str(checkpoint_out)

    print(f"dispatch_json={dispatch_json_path}")
    print(f"freq_csv={csv_path}")
    print(f"freq_plot={png_path}")
    print(f"summary_csv={summary_csv}")
    if checkpoint_saved:
        print(f"checkpoint_dir={checkpoint_saved}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
