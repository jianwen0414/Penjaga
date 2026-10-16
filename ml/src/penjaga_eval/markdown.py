"""A readable summary of an evaluation report (the source of docs/EVALUATION.md sections)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def _ci(ci: list[float] | tuple[float, float] | None) -> str:
    return "" if not ci else f" ({100 * ci[0]:.0f}-{100 * ci[1]:.0f}%)"


def _num(x: float | None, fmt: str = ".1f") -> str:
    return "-" if x is None else format(x, fmt)


def render(report: Mapping[str, Any]) -> str:
    lines = [
        f"## {report['suite']} · {report['split']} split",
        "",
        f"{report['episodes']} episodes, generator `{report['generator']}`, "
        f"commit `{(report['git']['commit'] or 'unknown')[:10]}`"
        f"{' (uncommitted changes)' if report['git']['dirty'] else ''}, {report['created']}.",
        "",
    ]
    for name, d in report["detectors"].items():
        params = ", ".join(f"{k}={v}" for k, v in d["params"].items())
        lines += [f"### `{name}` ({d['kind']}, v{d['version']})", "", f"Parameters: {params}.", ""]
        for layer, m in d["metrics"].items():
            lines += [
                f"**{layer}:** recall {_pct(m['recall'])} ({m['detected']}/{m['faults']} faults), "
                f"precision {_pct(m['precision'])}{_ci(m['precision_ci95'])}, "
                f"{_num(m['false_alarms_per_house_month'], '.2f')} false alarms per house-month "
                f"over {m['house_months']} house-months; {m['duplicate_findings']} extra findings "
                f"during real faults ({_num(m['findings_per_detected_fault'], '.2f')} findings "
                "per detected fault).",
                "",
                "| Fault | n | Recall (95% CI) | TTD median / p90 (h) | Specific type "
                "| Top-1 / top-3 | Room |",
                "|---|---|---|---|---|---|---|",
            ]
            for fault_type, t in m["by_fault_type"].items():
                lines.append(
                    f"| {fault_type} | {t['n']} | {_pct(t['recall'])}{_ci(t['recall_ci95'])} "
                    f"| {_num(t['ttd_hours_median'])} / {_num(t['ttd_hours_p90'])} "
                    f"| {_pct(t['specific_type_rate'])} "
                    f"| {_pct(t['localisation_top1'])} / {_pct(t['localisation_top3'])} "
                    f"| {_pct(t['room_top1'])} |"
                )
            causes = ", ".join(f"{k}: {v}" for k, v in sorted(m["false_alarms_by_cause"].items()))
            lines += ["", f"False alarms by context: {causes or 'none'}.", ""]
            weak = [
                f"{fault_type} {b['magnitude'][0]}-{b['magnitude'][1]}: {_pct(b['recall'])}"
                for fault_type, t in m["by_fault_type"].items()
                for b in t["recall_by_magnitude"]
            ]
            if weak:
                lines += ["Recall by magnitude tercile: " + "; ".join(weak) + ".", ""]
    return "\n".join(lines)
