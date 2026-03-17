#!/usr/bin/env python3
"""
Compare cold-start stitched dispatches against a hot-start second dispatch.

The hot-start workflow is:

1. Run the first dispatch interval with the regular deadband-demo TDS setup.
2. Save the terminal ANDES system snapshot and the external AGC integrator state.
3. Reload that snapshot and continue the second dispatch from the saved terminal
   dynamic state, while switching the AGC participation and governor basepoints
   to the second dispatch schedule.

This provides a practical "warm start" for the second dispatch without
re-initializing the dynamic model from a fresh power flow.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_dispatch_tds as rdt
import andes
from andes.utils.snapshot import load_ss, save_ss
import hotstart_checkpoint as hcp


def dispatch_offset(dispatch_record: rdt.DispatchRecord, dispatch_interval: int) -> int:
    return dispatch_record.hour * 3600 + dispatch_record.dispatch * dispatch_interval


def compute_bf(sa: andes.system.System, dispatch_record: rdt.DispatchRecord) -> np.ndarray:
    stg = sa.StaticGen.get_all_idxes()
    stg_on_uid = np.where(np.array(dispatch_record.pg) > 1e-4)[0]
    stg_on = np.array([1 if uid in stg_on_uid else 0 for uid in range(len(stg))], dtype=float)
    sn = sa.StaticGen.get(src="Sn", attr="v", idx=stg)
    denom = float((stg_on * sn).sum())
    if denom <= 0.0:
        raise RuntimeError("No online synchronous capacity found for AGC participation factors.")
    return stg_on * sn / denom


def prepare_system(
    dispatch_record: rdt.DispatchRecord,
    curve: pd.DataFrame,
    dyn_case: Path,
    dispatch_interval: int,
    init_mode: str,
    wind_prefixes: Iterable[str],
    solar_prefixes: Iterable[str],
) -> tuple[andes.system.System, dict[str, object]]:
    sa = andes.load(str(dyn_case), setup=False, no_output=True, default_config=True)
    sa.add("Output", dict(model="ACEc", varname="f"))
    sa.setup()

    link = rdt.build_andes_link(sa)
    pq_idx = sa.PQ.idx.v
    stg = sa.StaticGen.get_all_idxes()
    stg_w2t, stg_pv = rdt.pvd1_gen_subsets(sa, wind_prefixes, solar_prefixes)
    p0_w2t = sa.StaticGen.get(src="p0", attr="v", idx=stg_w2t)
    p0_pv = sa.StaticGen.get(src="p0", attr="v", idx=stg_pv)
    pvd1_w2t = sa.PVD1.find_idx(keys="gen", values=stg_w2t)
    pvd1_pv = sa.PVD1.find_idx(keys="gen", values=stg_pv)

    sap0 = sa.PQ.p0.v.copy()
    saq0 = sa.PQ.q0.v.copy()

    sa.StaticGen.set(src="p0", idx=dispatch_record.gen, attr="v", value=dispatch_record.pg)
    sa.Bus.set(src="v0", idx=dispatch_record.bus, attr="v", value=dispatch_record.vBus)
    sa.Bus.set(src="a0", idx=dispatch_record.bus, attr="v", value=dispatch_record.aBus)

    pv_bus = sa.PV.bus.v
    slack_bus = sa.Slack.bus.v
    v_pv = sa.Bus.get(src="v0", attr="v", idx=pv_bus)
    a_slack = sa.Bus.get(src="a0", attr="v", idx=slack_bus)
    sa.PV.set(src="v0", idx=sa.PV.idx.v, attr="v", value=v_pv)
    sa.Slack.set(src="a0", idx=sa.Slack.idx.v, attr="v", value=a_slack)

    sa.PQ.config.p2p = 1
    sa.PQ.config.q2q = 1
    sa.PQ.config.p2z = 0
    sa.PQ.config.q2z = 0
    sa.PQ.pq2z = 0
    sa.TDS.config.criteria = 0
    sa.TDS.config.no_tqdm = True

    init_load, init_wind, init_solar = rdt.resolve_initial_profile(
        curve=curve,
        dispatch_record=dispatch_record,
        duration_seconds=dispatch_interval,
        init_mode=init_mode,
    )
    sa.PQ.set(src="p0", idx=pq_idx, attr="v", value=init_load * sap0)
    sa.PQ.set(src="q0", idx=pq_idx, attr="v", value=init_load * saq0)
    sa.StaticGen.set(src="p0", idx=stg_w2t, attr="v", value=init_wind * p0_w2t)
    sa.StaticGen.set(src="p0", idx=stg_pv, attr="v", value=init_solar * p0_pv)

    sa.PFlow.run()
    if sa.exit_code != 0:
        raise RuntimeError(f"PFlow failed with exit_code={sa.exit_code}")

    sa.TDS.init()
    if sa.exit_code != 0:
        raise RuntimeError(f"TDS init failed with exit_code={sa.exit_code}")

    pext_max = 999 * np.ones(sa.DG.n)
    if hasattr(sa, "ESD1") and sa.ESD1.n:
        ess_uid = sa.DG.idx2uid(sa.ESD1.idx.v)
        pext_max[ess_uid] = 999

    ctx: dict[str, object] = {
        "curve": curve,
        "link": link,
        "pq_idx": pq_idx,
        "sap0": sap0,
        "saq0": saq0,
        "stg": stg,
        "stg_w2t": stg_w2t,
        "stg_pv": stg_pv,
        "p0_w2t": p0_w2t,
        "p0_pv": p0_pv,
        "pvd1_w2t": pvd1_w2t,
        "pvd1_pv": pvd1_pv,
        "pext_max": pext_max,
    }
    return sa, ctx


def apply_second_dispatch_targets(
    sa: andes.system.System,
    link: pd.DataFrame,
    dispatch_record: rdt.DispatchRecord,
    apply_governor_targets: bool,
    apply_dg_targets: bool,
    duration_seconds: int | None = None,
    schedule_mode: str = "boundary_ramp",
    next_dispatch_record: rdt.DispatchRecord | None = None,
) -> dict[str, object]:
    stg_idx = sa.StaticGen.get_all_idxes()
    pg_map = dict(zip(stg_idx, dispatch_record.pg))
    transition: dict[str, object] = {"ramp_seconds": 0}

    gov_rows = link.dropna(subset=["gov_idx"])
    if apply_governor_targets and not gov_rows.empty:
        gov_idx = gov_rows["gov_idx"].tolist()
        pref_values = np.array([pg_map[int(gen)] for gen in gov_rows["stg_idx"]], dtype=float)
        transition["gov_idx"] = gov_idx
        pref_start = sa.TurbineGov.get(src="pref0", attr="v", idx=gov_idx)
        transition["gov_pref_start"] = pref_start
        transition["gov_pref_target"] = pref_values
        transition["governor_target_schedule"] = schedule_mode

        if schedule_mode == "midpoint_trajectory":
            if duration_seconds is None:
                raise ValueError("duration_seconds is required for midpoint_trajectory")

            if next_dispatch_record is None:
                pref_end = pref_values.copy()
            else:
                next_pg_map = dict(zip(stg_idx, next_dispatch_record.pg))
                next_values = np.array([next_pg_map[int(gen)] for gen in gov_rows["stg_idx"]], dtype=float)
                pref_end = 0.5 * (pref_values + next_values)

            n_steps = int(duration_seconds)
            mid = max(1, n_steps // 2)
            last = max(1, n_steps - 1)
            pref_schedule = np.zeros((n_steps, len(gov_idx)), dtype=float)

            for step in range(n_steps):
                if step <= mid:
                    alpha = step / mid
                    pref_schedule[step] = pref_start + alpha * (pref_values - pref_start)
                else:
                    tail = max(1, last - mid)
                    alpha = (step - mid) / tail
                    pref_schedule[step] = pref_values + alpha * (pref_end - pref_values)

            transition["gov_pref_end"] = pref_end
            transition["gov_pref_schedule"] = pref_schedule

    # DG covers PVD1/ESD1 in this case. These units follow their curve / AGC path
    # and are no longer treated as dispatch-target devices at interval boundaries.
    _ = apply_dg_targets

    return transition


def activate_dispatch_target_transition(
    sa: andes.system.System,
    transition: dict[str, object] | None,
    step: int,
) -> None:
    if not transition:
        return

    ramp_seconds = int(transition.get("ramp_seconds", 0))
    if ramp_seconds <= 0:
        alpha = 1.0
    else:
        alpha = min(float(step) / float(ramp_seconds), 1.0)

    gov_idx = transition.get("gov_idx")
    gov_start = transition.get("gov_pref_start")
    gov_target = transition.get("gov_pref_target")
    gov_schedule = transition.get("gov_pref_schedule")
    if gov_idx is not None and gov_schedule is not None:
        schedule = np.asarray(gov_schedule, dtype=float)
        row = schedule[min(int(step), schedule.shape[0] - 1)]
        sa.TurbineGov.set(src="pref0", idx=gov_idx, attr="v", value=row)
    elif gov_idx is not None and gov_start is not None and gov_target is not None:
        gov_value = np.asarray(gov_start) + alpha * (np.asarray(gov_target) - np.asarray(gov_start))
        sa.TurbineGov.set(src="pref0", idx=gov_idx, attr="v", value=gov_value)


def run_segment(
    sa: andes.system.System,
    ctx: dict[str, object],
    start_offset: int,
    duration_seconds: int,
    agc_interval: int,
    kp: float,
    ki: float,
    bf: np.ndarray,
    ace_integral: float = 0.0,
    ace_raw: float = 0.0,
    local_start: float = 0.0,
    include_initial: bool = True,
    dispatch_target_transition: dict[str, object] | None = None,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    curve: pd.DataFrame = ctx["curve"]  # type: ignore[assignment]
    link: pd.DataFrame = ctx["link"]  # type: ignore[assignment]
    pq_idx = ctx["pq_idx"]
    sap0 = ctx["sap0"]
    saq0 = ctx["saq0"]
    p0_w2t = ctx["p0_w2t"]
    p0_pv = ctx["p0_pv"]
    pvd1_w2t = ctx["pvd1_w2t"]
    pvd1_pv = ctx["pvd1_pv"]
    pext_max = ctx["pext_max"]

    local_t = []
    freq = []
    if include_initial:
        local_t.append(float(local_start))
        freq.append(float((sa.ACEc.f.v[0] - 1.0) * sa.config.freq))

    current_tf = float(sa.dae.t)
    for step in range(1, duration_seconds):
        activate_dispatch_target_transition(sa, dispatch_target_transition, step)

        for col, has_col in (("agov", "has_gov"), ("adg", "has_dg"), ("arg", "has_rg")):
            link[col] = ace_raw * bf * link[has_col] * link["gammap"]

        if step % agc_interval == 0:
            agov_to_set = {gov: agov for gov, agov in zip(link["gov_idx"], link["agov"]) if pd.notna(gov)}
            if agov_to_set:
                gov_idx = list(agov_to_set.keys())
                paux0_raw = np.array(list(agov_to_set.values()))
                gov_syn = sa.TurbineGov.get(src="syn", attr="v", idx=gov_idx)
                gov_gen = sa.SynGen.get(src="gen", attr="v", idx=gov_syn)
                gov_pmax = sa.StaticGen.get(src="pmax", attr="v", idx=gov_gen)
                gov_pmin = sa.StaticGen.get(src="pmin", attr="v", idx=gov_gen)
                gov_pref0 = sa.TurbineGov.get(src="pref0", attr="v", idx=gov_idx)
                gov_up = np.maximum(0.0, gov_pmax - gov_pref0)
                gov_dn = np.minimum(0.0, gov_pmin - gov_pref0)
                paux0 = np.where(
                    paux0_raw >= 0.0,
                    np.minimum(paux0_raw, gov_up),
                    np.maximum(paux0_raw, gov_dn),
                )
                sa.TurbineGov.set(src="paux0", idx=gov_idx, attr="v", value=paux0)

            adg_to_set = {dg: adg for dg, adg in zip(link["dg_idx"], link["adg"]) if pd.notna(dg)}
            if adg_to_set:
                dg_idx = list(adg_to_set.keys())
                pext0_raw = np.array(list(adg_to_set.values()))
                dg_uids = sa.DG.idx2uid(dg_idx)
                pext0 = np.minimum(pext0_raw, pext_max[dg_uids])
                sa.DG.set(src="Pext0", idx=dg_idx, attr="v", value=pext0)

        kload = curve["Load"].iloc[start_offset + step]
        sa.PQ.set(src="Ppf", idx=sa.PQ.idx.v, attr="v", value=kload * sap0)
        sa.PQ.set(src="Qpf", idx=sa.PQ.idx.v, attr="v", value=kload * saq0)

        wind = curve["Wind"].iloc[start_offset + step]
        sa.PVD1.set(src="pref0", idx=pvd1_w2t, attr="v", value=wind * p0_w2t)

        solar = curve["PV"].iloc[start_offset + step]
        sa.PVD1.set(src="pref0", idx=pvd1_pv, attr="v", value=solar * p0_pv)

        current_tf += 1.0
        sa.TDS.config.tf = current_tf
        sa.TDS.run()
        if sa.exit_code != 0:
            raise RuntimeError(f"TDS failed at local step={step} with exit_code={sa.exit_code}")

        local_t.append(float(local_start + step))
        freq.append(float((sa.ACEc.f.v[0] - 1.0) * sa.config.freq))

        ace_sum = sa.ACEc.ace.v.sum()
        ace_raw = -(kp * ace_sum + ki * ace_integral)
        ace_integral = ace_integral + ace_sum

    return np.asarray(local_t), np.asarray(freq), float(ace_integral), float(ace_raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first-dispatch-json", type=Path, required=True)
    parser.add_argument("--second-dispatch-json", type=Path, required=True)
    parser.add_argument("--first-cold-csv", type=Path, required=True)
    parser.add_argument("--second-cold-csv", type=Path, required=True)
    parser.add_argument("--kp", type=float, default=0.03)
    parser.add_argument("--ki", type=float, default=0.01)
    parser.add_argument("--agc-interval", type=int, default=4)
    parser.add_argument("--dispatch-interval", type=int, default=900)
    parser.add_argument("--init-mode", choices=("dispatch", "first"), default="first")
    parser.add_argument("--resume-mode", choices=("memory", "snapshot"), default="memory")
    parser.add_argument("--apply-second-governor-targets", action="store_true")
    parser.add_argument(
        "--apply-second-dg-targets",
        action="store_true",
        help="Deprecated and ignored. DG/PVD1/ESD1 dispatch targets are not applied.",
    )
    parser.add_argument("--dispatch-target-ramp-seconds", type=int, default=0)
    parser.add_argument("--dyn-case", type=Path, default=rdt.DEFAULT_DYN_CASE)
    parser.add_argument("--stable-dyn-case", type=Path, default=rdt.DEFAULT_STABLE_DYN_CASE)
    parser.add_argument("--curve-file", type=Path, default=rdt.DEFAULT_CURVE_FILE)
    parser.add_argument("--results-dir", type=Path, default=rdt.RESULTS / "hotstart_compare")
    parser.add_argument("--label", type=str, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rdt.andes.config_logger(stream_level=30)

    first = rdt.DispatchRecord.from_json(args.first_dispatch_json)
    second = rdt.DispatchRecord.from_json(args.second_dispatch_json)
    label = args.label or f"{first.label}_{second.label}_hotstart"
    args.results_dir.mkdir(parents=True, exist_ok=True)

    curve = rdt.load_curve(args.curve_file)
    for record in (first, second):
        rdt.validate_curve_window(curve, record, args.dispatch_interval)

    dyn_case = rdt.adapt_dyn_case(args.dyn_case, args.stable_dyn_case)
    wind_prefixes = rdt.DEFAULT_WIND_PREFIXES
    solar_prefixes = rdt.DEFAULT_SOLAR_PREFIXES

    # First dispatch: regular cold start for hXdY.
    sa1, ctx1 = prepare_system(
        dispatch_record=first,
        curve=curve,
        dyn_case=dyn_case,
        dispatch_interval=args.dispatch_interval,
        init_mode=args.init_mode,
        wind_prefixes=wind_prefixes,
        solar_prefixes=solar_prefixes,
    )
    bf1 = compute_bf(sa1, first)
    t1, f1, ace_integral_end, ace_raw_end = run_segment(
        sa=sa1,
        ctx=ctx1,
        start_offset=dispatch_offset(first, args.dispatch_interval),
        duration_seconds=args.dispatch_interval,
        agc_interval=args.agc_interval,
        kp=args.kp,
        ki=args.ki,
        bf=bf1,
        ace_integral=0.0,
        ace_raw=0.0,
        local_start=0.0,
        include_initial=True,
    )

    snapshot_path = args.results_dir / f"{label}_snapshot.pkl"
    if args.resume_mode == "snapshot":
        # Save the dynamic terminal state and external AGC state.
        sa1._deadband_hotstart_meta = {  # type: ignore[attr-defined]
            "ace_integral": ace_integral_end,
            "ace_raw": ace_raw_end,
        }
        save_ss(snapshot_path, sa1)
        sa2 = load_ss(snapshot_path)
        hcp.rehydrate_loaded_snapshot(sa2)
        hot_meta = getattr(sa2, "_deadband_hotstart_meta", {})
        ace_integral_hot = float(hot_meta.get("ace_integral", 0.0))
        ace_raw_hot = float(hot_meta.get("ace_raw", 0.0))
    else:
        sa2 = sa1
        ace_integral_hot = ace_integral_end
        ace_raw_hot = ace_raw_end

    ctx2 = ctx1.copy()
    ctx2["link"] = rdt.build_andes_link(sa2)
    bf2 = compute_bf(sa2, second)
    transition = apply_second_dispatch_targets(
        sa2,
        ctx2["link"],  # type: ignore[arg-type]
        second,
        apply_governor_targets=args.apply_second_governor_targets,
        apply_dg_targets=args.apply_second_dg_targets,
    )
    transition["ramp_seconds"] = int(args.dispatch_target_ramp_seconds)
    if int(args.dispatch_target_ramp_seconds) <= 0:
        activate_dispatch_target_transition(sa2, transition, step=0)

    t2_hot, f2_hot, _, _ = run_segment(
        sa=sa2,
        ctx=ctx2,
        start_offset=dispatch_offset(second, args.dispatch_interval),
        duration_seconds=args.dispatch_interval,
        agc_interval=args.agc_interval,
        kp=args.kp,
        ki=args.ki,
        bf=bf2,
        ace_integral=ace_integral_hot,
        ace_raw=ace_raw_hot,
        local_start=float(args.dispatch_interval),
        include_initial=True,
        dispatch_target_transition=transition,
    )

    hot_df = pd.DataFrame({"time_s": np.concatenate([t1, t2_hot]), "freq_dev_hz": np.concatenate([f1, f2_hot])})
    hot_csv = args.results_dir / f"{label}_hotstart_frequency.csv"
    hot_df.to_csv(hot_csv, index=False)

    # Cold stitched traces from the existing per-dispatch runs for comparison.
    cold1 = pd.read_csv(args.first_cold_csv)
    cold2 = pd.read_csv(args.second_cold_csv)
    cold_x = np.concatenate([
        cold1["time_s"].to_numpy(dtype=float),
        cold2["time_s"].to_numpy(dtype=float) + args.dispatch_interval,
    ])
    cold_y = np.concatenate([
        cold1["freq_dev_hz"].to_numpy(dtype=float),
        cold2["freq_dev_hz"].to_numpy(dtype=float),
    ])

    jump_cold = float(cold2["freq_dev_hz"].iloc[0] - cold1["freq_dev_hz"].iloc[-1])
    jump_hot = float(f2_hot[0] - f1[-1])
    step_hot = float(f2_hot[1] - f2_hot[0]) if len(f2_hot) > 1 else float("nan")

    summary = pd.DataFrame([{
        "label": label,
        "cold_end_first_hz": float(cold1["freq_dev_hz"].iloc[-1]),
        "cold_start_second_hz": float(cold2["freq_dev_hz"].iloc[0]),
        "cold_jump_hz": jump_cold,
        "hot_end_first_hz": float(f1[-1]),
        "hot_start_second_hz": float(f2_hot[0]),
        "hot_jump_hz": jump_hot,
        "hot_step_0_to_1_hz": step_hot,
        "hot_min_hz": float(hot_df["freq_dev_hz"].min()),
        "hot_max_hz": float(hot_df["freq_dev_hz"].max()),
    }])
    summary_csv = args.results_dir / f"{label}_hotstart_summary.csv"
    summary.to_csv(summary_csv, index=False)

    fig, axes = plt.subplots(2, 1, figsize=(15.5, 10.2), sharex=False)
    axes[0].plot(cold_x, cold_y, color="#b24c2a", linewidth=1.25, label="cold stitched")
    axes[0].plot(hot_df["time_s"], hot_df["freq_dev_hz"], color="#0f5c78", linewidth=1.4, label="hot-start second dispatch")
    axes[0].axvline(args.dispatch_interval, color="#666666", linestyle="--", linewidth=0.9)
    axes[0].axhline(0.0, color="#999999", linestyle=":", linewidth=0.8)
    axes[0].set_title(f"{first.label} -> {second.label}: cold stitched vs hot-start second dispatch")
    axes[0].set_ylabel("Frequency deviation [Hz]")
    axes[0].grid(True, alpha=0.22)
    axes[0].legend(loc="upper right")

    axes[1].plot(cold_x, cold_y, color="#b24c2a", linewidth=1.35, label="cold stitched")
    axes[1].plot(hot_df["time_s"], hot_df["freq_dev_hz"], color="#0f5c78", linewidth=1.45, label="hot-start")
    axes[1].axvline(args.dispatch_interval, color="#666666", linestyle="--", linewidth=0.9)
    axes[1].axhline(0.0, color="#999999", linestyle=":", linewidth=0.8)
    axes[1].set_xlim(args.dispatch_interval - 60, args.dispatch_interval + 120)
    axes[1].set_title("Zoom around the dispatch boundary")
    axes[1].set_xlabel("Combined time [s]")
    axes[1].set_ylabel("Frequency deviation [Hz]")
    axes[1].grid(True, alpha=0.22)
    axes[1].legend(loc="upper right")
    axes[1].text(
        0.985,
        0.05,
        f"cold jump = {jump_cold:+.4f} Hz\nhot jump = {jump_hot:+.4f} Hz\nhot first step = {step_hot:+.4f} Hz",
        transform=axes[1].transAxes,
        ha="right",
        va="bottom",
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor="#cccccc", alpha=0.92),
    )
    fig.tight_layout()
    plot_path = args.results_dir / f"{label}_hotstart_vs_cold.png"
    fig.savefig(plot_path, dpi=220)
    plt.close(fig)

    manifest = {
        "first_dispatch_json": str(args.first_dispatch_json),
        "second_dispatch_json": str(args.second_dispatch_json),
        "kp": args.kp,
        "ki": args.ki,
        "agc_interval": args.agc_interval,
        "dispatch_interval": args.dispatch_interval,
        "init_mode": args.init_mode,
        "resume_mode": args.resume_mode,
        "apply_second_governor_targets": args.apply_second_governor_targets,
        "apply_second_dg_targets": args.apply_second_dg_targets,
        "snapshot_path": str(snapshot_path if args.resume_mode == "snapshot" else ""),
        "hot_csv": str(hot_csv),
        "summary_csv": str(summary_csv),
        "plot_path": str(plot_path),
    }
    (args.results_dir / f"{label}_hotstart_manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"hot_csv={hot_csv}")
    print(f"summary_csv={summary_csv}")
    print(f"plot={plot_path}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
