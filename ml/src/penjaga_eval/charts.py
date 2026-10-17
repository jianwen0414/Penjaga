"""Charts for the pitch deck (PRD §21), drawn from saved evaluation reports.

Each chart is a PNG (for slides) and an SVG, plus a CSV with the numbers it shows (the table
view: the chart is never the only way to read them). Charts are drawn only from reports on
sealed test splits, the newest of each kind, so a chart can always be traced to a logged run.

Design rules (the project's data-viz method): one axis per chart (two measures are two
panels), categorical colours assigned in a fixed order and validated for colour-vision
deficiency, values written next to the marks, recessive grid and axes, no colour-only meaning.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# reference palette, light mode (validated: adjacent CVD dE >= 9.1, normal-vision >= 22.9)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
LAYER_COLOUR = {"L1": SERIES[0], "L2": SERIES[1], "L3": SERIES[2], "L5": SERIES[3]}
LAYER_NAME = {
    "L1": "L1 water leaks",
    "L2": "L2 electrical safety",
    "L3": "L3 appliance health",
    "L5": "L5 supply",
}
# fault type -> (layer, the deployed detector that answers for it)
DEPLOYED = {
    "W1": ("L1", "l1_mnf_baseline"),
    "W2": ("L1", "l1_mnf_baseline"),
    "W3": ("L1", "l1_mnf_baseline"),
    "W4": ("L1", "l1_mnf_baseline"),
    "W5": ("L1", "l1_burst_rule"),
    "E1": ("L2", "l2_safety_floor"),
    "E2": ("L2", "l2_leakage_baseline"),
    "E3": ("L2", "l2_safety_floor"),
    "A1": ("L3", "l3_health_baseline"),
    "A2": ("L3", "l3_health_baseline"),
    "A3": ("L3", "l3_health_baseline"),
    "A4": ("L3", "l3_health_baseline"),
    "U1": ("L5", "l5_supply"),
    "U2": ("L5", "l5_supply"),
    "U3": ("L5", "l5_supply"),
}
FAULT_NAME = {
    "W1": "Dripping tap",
    "W2": "Running cistern",
    "W3": "Tank float valve",
    "W4": "Concealed pipe leak",
    "W5": "Burst pipe",
    "E1": "Circuit overload",
    "E2": "Rising earth leakage",
    "E3": "Repeated trips",
    "A1": "Aircon filter clogging",
    "A2": "Aircon low refrigerant",
    "A3": "Fridge door seal",
    "A4": "Fridge compressor wear",
    "U1": "Scheduled water cut",
    "U2": "Unannounced water cut",
    "U3": "Power outage",
}
METER_START_LPH = 1.4  # DS-028


def latest_reports(directory: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    """The newest report per (suite, kind, split); detection reports have kind "detection"."""
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in sorted(directory.glob("*/report.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        key = (report.get("suite", ""), report.get("kind", "detection"), report.get("split", ""))
        if key not in out or report.get("created", "") > out[key].get("created", ""):
            out[key] = report
    return out


def _style(ax: Any) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _figure(width: float, height: float, ncols: int = 1) -> tuple[Any, Any]:
    fig, axes = plt.subplots(1, ncols, figsize=(width, height), facecolor=SURFACE)
    for ax in axes if ncols > 1 else [axes]:
        _style(ax)
    return fig, axes


def _title(fig: Any, title: str, subtitle: str) -> None:
    fig.text(0.01, 0.98, title, ha="left", va="top", fontsize=13, color=INK, weight="bold")
    fig.text(0.01, 0.925, subtitle, ha="left", va="top", fontsize=9, color=INK_2)


def _save(fig: Any, out: Path, name: str, rows: Sequence[Mapping[str, Any]]) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / f"{name}.png", out / f"{name}.svg", out / f"{name}.csv"]
    fig.savefig(paths[0], dpi=200, facecolor=SURFACE)
    fig.savefig(paths[1], facecolor=SURFACE)
    plt.close(fig)
    with paths[2].open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return paths


def recall_by_fault(
    reports: Mapping[tuple[str, str, str], dict[str, Any]], out: Path
) -> list[Path]:
    """Recall of the deployed system per fault type, with 95% intervals, by layer."""
    rows: list[dict[str, Any]] = []
    for suite in ("l1_v0", "l2_v0", "l3_v0", "l5_v0"):
        report = reports.get((suite, "detection", "test"))
        if report is None:
            continue
        for fault, (layer, detector) in DEPLOYED.items():
            d = report["detectors"].get(detector)
            t = (
                None
                if d is None
                else d["metrics"].get(layer, {}).get("by_fault_type", {}).get(fault)
            )
            if t is None or t["recall"] is None:
                continue
            ci = t["recall_ci95"] or [t["recall"], t["recall"]]
            rows.append(
                {
                    "fault": fault,
                    "name": FAULT_NAME[fault],
                    "layer": layer,
                    "detector": detector,
                    "n": t["n"],
                    "recall": t["recall"],
                    "ci_low": ci[0],
                    "ci_high": ci[1],
                    "ttd_median_h": t["ttd_hours_median"],
                    "generator": report["generator"],
                }
            )
    if not rows:
        return []
    fig, ax = _figure(8.5, 0.36 * len(rows) + 1.6)
    y = list(range(len(rows)))[::-1]
    for yi, r in zip(y, rows, strict=True):
        ax.barh(yi, 100 * r["recall"], height=0.6, color=LAYER_COLOUR[r["layer"]])
        ax.plot([100 * r["ci_low"], 100 * r["ci_high"]], [yi, yi], color=INK_2, linewidth=1)
        ax.text(103, yi, f"{100 * r['recall']:.0f}%", va="center", fontsize=9, color=INK)
    ax.set_yticks(y, [f"{r['name']} ({r['fault']})" for r in rows], color=INK, fontsize=9)
    ax.set_xlim(0, 115)
    ax.set_xlabel("Faults found on the sealed test split (%)", color=INK_2, fontsize=9)
    handles = [Rectangle((0, 0), 1, 1, color=c) for c in LAYER_COLOUR.values()]
    ax.legend(
        handles,
        list(LAYER_NAME.values()),
        loc="lower left",
        bbox_to_anchor=(0, 1.0),
        ncol=4,
        frameon=False,
        fontsize=8,
        labelcolor=INK_2,
    )
    _title(
        fig,
        "What the deployed system finds",
        "Recall per fault type, 95% bootstrap interval as a line; "
        f"simulated houses ({rows[0]['generator']})",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.86))
    return _save(fig, out, "recall_by_fault", rows)


def l1_detectors(reports: Mapping[tuple[str, str, str], dict[str, Any]], out: Path) -> list[Path]:
    """Baseline against model, on normal houses and on houses whose tank refills at night."""
    rows = []
    for suite, label in (
        ("l1_v0", "Normal pressure"),
        ("l1_lowp_v0", "Low pressure (night refill)"),
    ):
        report = reports.get((suite, "detection", "test"))
        if report is None:
            continue
        for detector, name in (
            ("l1_mnf_baseline", "Night-flow baseline"),
            ("l1_leak_model", "Leak model"),
        ):
            d = report["detectors"].get(detector)
            if d is None:
                continue
            m = d["metrics"]["L1"]
            rows.append(
                {
                    "suite": suite,
                    "houses": label,
                    "detector": name,
                    "recall": m["recall"],
                    "false_alarms_per_house_month": m["false_alarms_per_house_month"],
                    "params": json.dumps(d["params"], sort_keys=True),
                }
            )
    if not rows:
        return []
    fig, (left, right) = _figure(9, 3.6, ncols=2)
    groups = list(dict.fromkeys(r["houses"] for r in rows))
    detectors = list(dict.fromkeys(r["detector"] for r in rows))
    width = 0.36
    for k, det in enumerate(detectors):
        for g, houses in enumerate(groups):
            r = next((x for x in rows if x["houses"] == houses and x["detector"] == det), None)
            if r is None:
                continue
            x = g + (k - (len(detectors) - 1) / 2) * (width + 0.04)
            rec = 100 * (r["recall"] or 0)
            fa = r["false_alarms_per_house_month"] or 0.0
            left.bar(x, rec, width, color=SERIES[k], label=det if g == 0 else None)
            left.text(x, rec + 2, f"{rec:.0f}%", ha="center", fontsize=9, color=INK)
            right.bar(x, fa, width, color=SERIES[k])
            right.text(x, fa + 0.02, f"{fa:.2f}", ha="center", fontsize=9, color=INK)
    for ax, label in ((left, "Leaks found (%)"), (right, "False alarms per house-month")):
        ax.set_xticks(range(len(groups)), groups, color=INK, fontsize=9)
        ax.set_ylabel(label, color=INK_2, fontsize=9)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.grid(axis="x", visible=False)
    left.set_ylim(0, 112)
    right.set_ylim(0, max(0.6, 1.25 * max(r["false_alarms_per_house_month"] or 0 for r in rows)))
    left.legend(
        frameon=False,
        fontsize=8,
        loc="lower left",
        bbox_to_anchor=(0, 1.0),
        ncol=2,
        labelcolor=INK_2,
    )
    _title(
        fig,
        "L1 leaks: the night-flow baseline against the leak model",
        "Deployed settings on the sealed test splits; low-pressure houses refill their roof "
        "tank at night (DS-033 to DS-035)",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    return _save(fig, out, "l1_baseline_vs_model", rows)


def drip_recall(reports: Mapping[tuple[str, str, str], dict[str, Any]], out: Path) -> list[Path]:
    """Where L1 fails: recall of dripping taps by drip size, against the meter's starting flow."""
    report = reports.get(("l1_v0", "detection", "test"))
    if report is None:
        return []
    rows = []
    for detector, name in (
        ("l1_mnf_baseline", "Night-flow baseline"),
        ("l1_leak_model", "Leak model"),
    ):
        d = report["detectors"].get(detector)
        if d is None:
            continue
        for b in d["metrics"]["L1"]["by_fault_type"].get("W1", {}).get("recall_by_magnitude", []):
            lo, hi = b["magnitude"]
            rows.append(
                {
                    "detector": name,
                    "flow_lph_low": 60 * lo,
                    "flow_lph_high": 60 * hi,
                    "n": b["n"],
                    "recall": b["recall"],
                }
            )
    if not rows:
        return []
    fig, ax = _figure(7.5, 3.8)
    names = list(dict.fromkeys(r["detector"] for r in rows))
    bins = list(dict.fromkeys((r["flow_lph_low"], r["flow_lph_high"]) for r in rows))
    width = 0.36
    for k, name in enumerate(names):
        for g, (lo, _hi) in enumerate(bins):
            r = next(x for x in rows if x["detector"] == name and x["flow_lph_low"] == lo)
            v = 100 * (r["recall"] or 0)
            x = g + (k - (len(names) - 1) / 2) * (width + 0.04)
            ax.bar(x, v, width, color=SERIES[k], label=name if g == 0 else None)
            ax.text(x, v + 2, f"{v:.0f}%", ha="center", fontsize=9, color=INK)
    labels = [f"{lo:.1f}-{hi:.1f} L/h" for lo, hi in bins]
    ax.set_xticks(range(len(bins)), labels, color=INK, fontsize=9)
    ax.set_ylim(0, 112)
    ax.set_xlabel("Drip size, by third of the test drips", color=INK_2, fontsize=9)
    ax.set_ylabel("Drips found (%)", color=INK_2, fontsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.grid(axis="x", visible=False)
    ax.legend(
        frameon=False,
        fontsize=8,
        loc="lower left",
        bbox_to_anchor=(0, 1.0),
        ncol=2,
        labelcolor=INK_2,
    )
    _title(
        fig,
        "Where L1 fails: the smallest drips",
        f"Dripping-tap recall on the sealed l1_v0 test split; the meter starts registering at "
        f"{METER_START_LPH} L/h (DS-028)",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    return _save(fig, out, "l1_drip_recall", rows)


def reliability(reports: Mapping[tuple[str, str, str], dict[str, Any]], out: Path) -> list[Path]:
    """Reliability diagram of the leak model's P(leak) on the sealed test splits."""
    rows = []
    for k, suite in enumerate(("l1_v0", "l1_lowp_v0")):
        report = reports.get((suite, "calibration", "test"))
        if report is None:
            continue
        for b in report["metrics"]["reliability_equal_mass"]:
            rows.append({"suite": suite, "series": k, "ece": report["metrics"]["ece"], **b})
    if not rows:
        return []
    fig, ax = _figure(5.6, 5.0)
    ax.plot([0, 1], [0, 1], color=INK_2, linewidth=1, linestyle="--")
    for suite in dict.fromkeys(r["suite"] for r in rows):
        pts = [r for r in rows if r["suite"] == suite]
        k = pts[0]["series"]
        ax.plot(
            [r["mean_predicted"] for r in pts],
            [r["observed"] for r in pts],
            color=SERIES[k],
            linewidth=2,
            marker="o",
            markersize=7,
            markeredgecolor=SURFACE,
            markeredgewidth=2,
            label=f"{suite} (ECE {pts[0]['ece']:.3f})",
        )
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("Predicted P(leak) (15 equal-count bins of nights)", color=INK_2, fontsize=9)
    ax.set_ylabel("Share of nights with a leak", color=INK_2, fontsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.legend(frameon=False, fontsize=8, loc="upper left", labelcolor=INK_2)
    _title(
        fig,
        "Is the leak model's probability honest?",
        "Reliability on the sealed test splits; dashed: perfectly calibrated",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    return _save(fig, out, "l1_reliability", rows)


def localisation(reports: Mapping[tuple[str, str, str], dict[str, Any]], out: Path) -> list[Path]:
    """Top-1 and top-3 location accuracy before and after guided checks."""
    report = reports.get(("l1_v0", "localisation", "test"))
    if report is None:
        return []
    names = {
        "l1_mnf_baseline": "Night-flow baseline",
        "l1_leak_model": "Leak model",
        "l1_burst_rule": "Burst rule",
    }
    rows = []
    for detector, d in report["detectors"].items():
        g = d["all"]
        rows.append(
            {
                "detector": names.get(detector, detector),
                "n": g["n"],
                "top1_before": g["top1_before"],
                "top1_after": g["top1_after"],
                "top3_before": g["top3_before"],
                "top3_after": g["top3_after"],
                "checks_mean": g["checks_mean"],
            }
        )
    fig, axes = _figure(9, 3.6, ncols=2)
    width = 0.36
    for ax, key, label in (
        (axes[0], "top1", "Faulty element ranked first (%)"),
        (axes[1], "top3", "Faulty element in the top three (%)"),
    ):
        for g, r in enumerate(rows):
            for k, when in enumerate(("before", "after")):
                v = 100 * (r[f"{key}_{when}"] or 0)
                x = g + (k - 0.5) * (width + 0.04)
                ax.bar(x, v, width, color=SERIES[k], label=f"{when} checks" if g == 0 else None)
                ax.text(x, v + 2, f"{v:.0f}%", ha="center", fontsize=9, color=INK)
        ax.set_xticks(
            range(len(rows)),
            [f"{r['detector']}\n(n={r['n']})" for r in rows],
            color=INK,
            fontsize=9,
        )
        ax.set_ylim(0, 112)
        ax.set_ylabel(label, color=INK_2, fontsize=9)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.grid(axis="x", visible=False)
    axes[0].legend(
        frameon=False,
        fontsize=8,
        loc="lower left",
        bbox_to_anchor=(0, 1.0),
        ncol=2,
        labelcolor=INK_2,
    )
    _title(
        fig,
        "What guided checks add to locating a leak",
        "Detected water faults on the sealed l1_v0 test split; checks simulated in each house",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    return _save(fig, out, "l1_localisation_checks", rows)


def tank_outlook(reports: Mapping[tuple[str, str, str], dict[str, Any]], out: Path) -> list[Path]:
    """The roof-tank outlook's implied draw rate against the true one, per water cut."""
    report = reports.get(("l5_tank_v0", "tank_outlook", "test"))
    if report is None:
        return []
    cases = [c for c in report["cases"] if c["rate"] is not None and c["true_rate"] is not None]
    if not cases:
        return []
    rows = [
        {
            "episode": c["episode"],
            "fault_type": c["fault_type"],
            "predicted_l_per_h": c["rate"],
            "true_l_per_h": c["true_rate"],
            "litres": c["litres"],
            "true_litres": c["true_litres"],
        }
        for c in cases
    ]
    fig, ax = _figure(5.6, 5.0)
    top = 1.1 * max(max(r["predicted_l_per_h"], r["true_l_per_h"]) for r in rows)
    ax.plot([0, top], [0, top], color=INK_2, linewidth=1, linestyle="--")
    for k, ft in enumerate(("U1", "U2")):
        pts = [r for r in rows if r["fault_type"] == ft]
        ax.scatter(
            [r["true_l_per_h"] for r in pts],
            [r["predicted_l_per_h"] for r in pts],
            s=40,
            color=SERIES[k],
            edgecolors=SURFACE,
            linewidths=1.5,
            label=FAULT_NAME[ft],
        )
    m = report["metrics"]
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    ax.set_xlabel("True tank draw during the cut (L/h)", color=INK_2, fontsize=9)
    ax.set_ylabel("Draw the outlook assumed (L/h)", color=INK_2, fontsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.legend(frameon=False, fontsize=8, loc="upper left", labelcolor=INK_2)
    _title(
        fig,
        "How long will the roof tank last?",
        f"Method {report['params']['outlook_method']}: draw typically off by "
        f"x{m['rate_factor']:.2f}; tank litres off by {m['litres_mae']:.0f} L on average",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    return _save(fig, out, "l5_tank_outlook", rows)


def make_charts(reports_dir: Path, out: Path) -> list[Path]:
    reports = latest_reports(reports_dir)
    paths: list[Path] = []
    for chart in (
        recall_by_fault,
        l1_detectors,
        drip_recall,
        reliability,
        localisation,
        tank_outlook,
    ):
        paths += chart(reports, out)
    return paths
