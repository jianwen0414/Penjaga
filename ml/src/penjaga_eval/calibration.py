"""Is the leak model's probability a probability? Reliability and calibration error (PRD §21).

The L1 leak model reports ``confidence_kind: calibrated_probability``. This checks that claim
night by night: for every night of a split, the model's P(leak) = 1 - P(none) against whether
a water fault was active during the night window (the label training uses, from the truth).

Reported, with 95% bootstrap intervals over episodes (nights of one house are not independent):
- **ECE**, the expected calibration error: the count-weighted mean gap between predicted
  probability and observed frequency, over 15 equal-width bins (Guo et al. 2017) and over 15
  equal-mass bins (less sensitive to empty bins);
- the **reliability table** behind the diagram (per bin: mean prediction, observed rate, nights);
- the **Brier score**, and the Brier skill score against always predicting the split's base rate
  (the simple baseline: 0 means no better than the base rate, 1 is perfect);
- **log loss**;
- **top-label ECE** over the six classes {none, W1..W5}: confidence is the highest class
  probability, correct means that class is the night's label.

A night whose leak sits below the meter's starting flow still counts as a leak: the model is
judged on what the household has, not on what the meter can see.
"""

from __future__ import annotations

import pickle
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from penjaga_eval import episodes, sealing
from penjaga_eval.suites import Suite, manifests
from penjaga_eval.train_l1 import dataset
from penjaga_sim import GENERATOR_VERSION

Floats = npt.NDArray[np.float64]
BINS = 15
EPS = 1e-12


def reliability(
    p: Floats, y: Floats, bins: int = BINS, *, equal_mass: bool = False
) -> list[dict[str, Any]]:
    """Per-bin mean prediction, observed rate and count (empty bins left out)."""
    if p.size == 0:
        return []
    if equal_mass:
        order = np.argsort(p, kind="stable")
        groups = np.array_split(order, min(bins, p.size))
    else:
        index = np.minimum((p * bins).astype(np.int64), bins - 1)
        groups = [np.flatnonzero(index == b) for b in range(bins)]
    out = []
    for g in groups:
        if g.size:
            out.append(
                {
                    "p_low": float(p[g].min()),
                    "p_high": float(p[g].max()),
                    "mean_predicted": float(p[g].mean()),
                    "observed": float(y[g].mean()),
                    "n": int(g.size),
                }
            )
    return out


def ece(p: Floats, y: Floats, bins: int = BINS, *, equal_mass: bool = False) -> float:
    table = reliability(p, y, bins, equal_mass=equal_mass)
    n = sum(int(b["n"]) for b in table)
    gaps = sum(int(b["n"]) * abs(float(b["mean_predicted"]) - float(b["observed"])) for b in table)
    return gaps / max(n, 1)


def brier(p: Floats, y: Floats) -> float:
    return float(np.mean((p - y) ** 2))


def brier_skill(p: Floats, y: Floats) -> float | None:
    base = float(y.mean())
    reference = float(np.mean((base - y) ** 2))
    return None if reference <= 0 else 1.0 - brier(p, y) / reference


def log_loss(p: Floats, y: Floats) -> float:
    q = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def _bootstrap(
    groups: npt.NDArray[np.int64], stat: Callable[[npt.NDArray[np.int64]], float | None],
    reps: int = 1000, seed: int = 0,
) -> list[float] | None:  # fmt: skip
    """95% interval of ``stat`` over resampled episodes (``stat`` takes row indices)."""
    ids = np.unique(groups)
    rows = {g: np.flatnonzero(groups == g) for g in ids}
    r = np.random.default_rng(seed)
    values = []
    for _ in range(reps):
        picked = np.concatenate([rows[g] for g in r.choice(ids, ids.size, replace=True)])
        v = stat(picked)
        if v is not None:
            values.append(v)
    if not values:
        return None
    lo, hi = np.percentile(values, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def metrics(
    proba: Floats, classes: Sequence[str], labels: Sequence[str], groups: npt.NDArray[np.int64]
) -> dict[str, Any]:
    """Calibration of P(leak) and of the top label, for nights with known features."""
    classes = list(classes)
    none = classes.index("none")
    p_leak = 1.0 - proba[:, none]
    y_leak = np.array([lab != "none" for lab in labels], dtype=np.float64)
    top = proba.argmax(axis=1)
    confidence = proba[np.arange(len(top)), top]
    correct = np.array(
        [classes[t] == lab for t, lab in zip(top, labels, strict=True)], dtype=np.float64
    )

    def on(
        rows: npt.NDArray[np.int64], f: Callable[[Floats, Floats], float | None]
    ) -> float | None:
        return f(p_leak[rows], y_leak[rows])

    return {
        "nights": int(p_leak.size),
        "episodes": int(np.unique(groups).size),
        "leak_nights": int(y_leak.sum()),
        "base_rate": float(y_leak.mean()),
        "ece": ece(p_leak, y_leak),
        "ece_ci95": _bootstrap(groups, lambda r: on(r, ece)),
        "ece_equal_mass": ece(p_leak, y_leak, equal_mass=True),
        "brier": brier(p_leak, y_leak),
        "brier_skill": brier_skill(p_leak, y_leak),
        "brier_skill_ci95": _bootstrap(groups, lambda r: on(r, brier_skill)),
        "log_loss": log_loss(p_leak, y_leak),
        "reliability": reliability(p_leak, y_leak),
        "reliability_equal_mass": reliability(p_leak, y_leak, equal_mass=True),
        "top_label_ece": ece(confidence, correct),
        "top_label_accuracy": float(correct.mean()),
        "top_label_reliability": reliability(confidence, correct),
    }


def predict(artifact: Path, x: Floats) -> tuple[Floats, list[str]]:
    with artifact.open("rb") as fh:
        model: dict[str, Any] = pickle.load(fh)  # project artifact, never user input
    proba = np.asarray(model["estimator"].predict_proba(x), dtype=np.float64)
    return proba, [str(c) for c in model["classes"]]


def calibrate(
    suite: Suite,
    split: str,
    artifact: Path,
    *,
    episodes_root: Path = episodes.DEFAULT_ROOT,
    workers: int | None = None,
    sealed_dir: Path = sealing.SEALED_DIR,
    test_log: Path = sealing.TEST_LOG,
) -> dict[str, Any]:
    if split == "train":
        raise ValueError("calibration is judged on held-out nights (validation or test)")
    if split == "test":
        sealing.check(suite, manifests(suite, "test"), sealed_dir)
    x, labels, groups = dataset(suite, split, episodes_root, workers)
    known = np.isfinite(x[:, 0])  # the model skips nights without data ("mnf" missing)
    proba, classes = predict(artifact, x[known])
    report: dict[str, Any] = {
        "kind": "calibration",
        "suite": suite.name,
        "split": split,
        "generator": GENERATOR_VERSION,
        "artifact": artifact.as_posix(),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": sealing.git_state(),
        "nights_without_data": int((~known).sum()),
        "metrics": metrics(proba, classes, labels[known].tolist(), groups[known]),
    }
    if split == "test":
        m = report["metrics"]
        sealing.log_test_run(
            {
                "created": report["created"],
                "git": report["git"],
                "suite": suite.name,
                "generator": GENERATOR_VERSION,
                "calibration": {
                    "artifact": report["artifact"],
                    "headline": {
                        k: m[k] for k in ("ece", "ece_equal_mass", "brier_skill", "top_label_ece")
                    },
                },
            },
            test_log,
        )
    return report


def render(report: dict[str, Any]) -> str:
    m = report["metrics"]

    def ci(v: list[float] | None) -> str:
        return "" if not v else f" ({v[0]:.3f}-{v[1]:.3f})"

    lines = [
        f"### Calibration · `{report['suite']}` {report['split']} split",
        "",
        f"{m['nights']} nights from {m['episodes']} episodes ({m['leak_nights']} with a leak, "
        f"base rate {m['base_rate']:.2f}); model `{report['artifact']}`, generator "
        f"`{report['generator']}`, commit `{(report['git']['commit'] or 'unknown')[:10]}`.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| ECE, P(leak), 15 equal-width bins | {m['ece']:.3f}{ci(m['ece_ci95'])} |",
        f"| ECE, P(leak), 15 equal-mass bins | {m['ece_equal_mass']:.3f} |",
        f"| Brier score | {m['brier']:.3f} |",
        f"| Brier skill vs base rate | {m['brier_skill']:.3f}{ci(m['brier_skill_ci95'])} |",
        f"| Log loss | {m['log_loss']:.3f} |",
        f"| Top-label ECE (6 classes) | {m['top_label_ece']:.3f} |",
        f"| Top-label accuracy | {m['top_label_accuracy']:.3f} |",
        "",
        "| P(leak) bin | Mean predicted | Observed | Nights |",
        "|---|---|---|---|",
    ]
    for b in m["reliability"]:
        lines.append(
            f"| {b['p_low']:.2f}-{b['p_high']:.2f} | {b['mean_predicted']:.3f} "
            f"| {b['observed']:.3f} | {b['n']} |"
        )
    return "\n".join(lines)
