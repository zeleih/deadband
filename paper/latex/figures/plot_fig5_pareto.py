"""Fig. 5: Full-day trade-off among the top-6 candidates.

Plots six Stage-1-selected candidates in the
(edge_mass_36, share(|Df|>0.05 Hz)) plane. The baseline point is added
from paper baseline statistics, and candidate labels use the final full-day
ranking. Output: fig5_pareto.pdf/.png
"""
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from fig_style import COLORS, apply_style, polish_axes, save_figure

HERE = Path(__file__).parent
ROOT = HERE.parent.parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--summary-csv", type=Path,
        default=ROOT / "results"
                / "phase1_full_day_tip100_alpha098_disable_pvd_agc_disable_esd_agc_kp0p1_ki0p002"
                / "phase1_full_day_ranked.csv",
    )
    parser.add_argument("--out", type=Path, default=HERE / "fig5_pareto.pdf")
    parser.add_argument("--baseline-edge-mass", type=float, default=0.1690)
    parser.add_argument("--baseline-share-gt-0p05", type=float, default=0.0287)
    args = parser.parse_args()

    df = pd.read_csv(args.summary_csv).sort_values("rank").reset_index(drop=True)
    if df.empty:
        raise RuntimeError("No candidates in full-day ranked CSV")

    apply_style()
    fig, ax = plt.subplots(figsize=(3.5, 2.6))

    x_base = 100 * args.baseline_edge_mass
    y_base = 100 * args.baseline_share_gt_0p05
    x = 100 * df["edge_mass_36"].values
    y = 100 * df["share_abs_gt_0p05"].values
    ranks = df["rank"].astype(int).values

    # baseline marker (anchor of comparison)
    ax.scatter(x_base, y_base, marker="X", s=80, color=COLORS["red"],
               edgecolors="white", linewidths=0.6, zorder=6, label="baseline")
    ax.annotate("baseline", (x_base, y_base), xytext=(-7, -2),
                textcoords="offset points", ha="right", va="center",
                fontsize=6.8, color=COLORS["red"])

    # candidate points
    best_mask = ranks == 1
    ax.scatter(x[~best_mask], y[~best_mask], s=46, color=COLORS["blue"],
               edgecolors="white", linewidths=0.55, zorder=5,
               label="top candidates")
    ax.scatter(x[best_mask], y[best_mask], s=110, marker="*",
               color=COLORS["amber"], edgecolors=COLORS["ink"],
               linewidths=0.45, zorder=7, label="selected best (#1)")

    # Short, non-crossing leaders: right-side labels for points with free space
    # to the right, left-side labels otherwise (#5/#6 nearly coincide).
    label_offsets = {
        1: (9, -6), 2: (9, 1), 3: (9, 1),
        4: (-9, 0), 5: (9, -2), 6: (-9, 3),
    }
    for xi, yi, r in zip(x, y, ranks):
        dx, dy = label_offsets.get(int(r), (8, 4))
        ax.annotate(f"#{r}", xy=(xi, yi), xytext=(dx, dy),
                    textcoords="offset points", fontsize=6.6,
                    color=COLORS["ink"],
                    ha="left" if dx >= 0 else "right",
                    va="center",
                    arrowprops=dict(arrowstyle="-", color=COLORS["muted"],
                                    linewidth=0.45, shrinkA=1.5, shrinkB=2.5,
                                    alpha=0.80))

    ax.set_xlabel(r"$\mathrm{EM}_{36}$ (% of samples)")
    ax.set_ylabel(r"$\mathrm{share}(|\Delta f|\!>\!0.05\,\mathrm{Hz})$ (%)")
    xpad = 0.6
    ypad = 0.18
    ax.set_xlim(min(x.min(), x_base) - xpad, max(x.max(), x_base) + xpad)
    ax.set_ylim(min(y.min(), y_base) - ypad, max(y.max(), y_base) + ypad + 0.15)

    polish_axes(ax)
    leg = ax.legend(loc="upper left", frameon=False, ncol=1,
                    handletextpad=0.4, labelspacing=0.25)
    for txt in leg.get_texts():
        txt.set_fontsize(6.6)
    save_figure(fig, args.out)


if __name__ == "__main__":
    main()
