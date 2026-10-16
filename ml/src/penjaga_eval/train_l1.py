"""Train the L1 leak model on the train split only (D-008, rung 2).

Feature rows come from replaying each episode through ``L1LeakModel`` in collection mode, so
training sees exactly what live detection computes. Each night is labelled from the episode's
truth: the water fault active during the night window (W1-W5), else ``none``.

Model: scikit-learn HistGradientBoostingClassifier (balanced class weights; missing features
are native NaN), calibrated with temperature scaling (CalibratedClassifierCV, method
"temperature", multiclass) over folds grouped by episode, so nights of one house never sit on
both sides of a fold. The decision threshold is then chosen on the validation split by
``penjaga-eval tune``.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold

from penjaga_detect.detector import HouseContext
from penjaga_detect.l1_features import FEATURES, as_vector
from penjaga_detect.l1_model import L1LeakModel, L1Params
from penjaga_eval import episodes
from penjaga_eval.replay import replay
from penjaga_eval.scoring import load_truth
from penjaga_eval.suites import Suite, manifests
from penjaga_sim import GENERATOR_VERSION

ML_DIR = Path(__file__).resolve().parents[2]
ARTIFACT = Path("ml/artifacts/l1_leak_model.pkl")
MODEL_CARD_DATA = ML_DIR / "models" / "l1_leak_model.json"
NIGHT_US = 4 * 3600 * 1_000_000
VERSION = "1.0.0"


def night_rows(path: Path, house: HouseContext) -> tuple[list[npt.NDArray[np.float64]], list[str]]:
    """Feature vectors and labels for every night of one episode."""
    detector = L1LeakModel(house, L1Params())
    replay(path, [detector])
    faults, _, _ = load_truth(path)
    water = [f for f in faults if f.layer == "L1"]
    xs, ys = [], []
    for night_end, row in detector.rows:
        night_start = night_end - NIGHT_US
        active = [f for f in water if f.start_us < night_end and f.end_us > night_start]
        label = max(active, key=lambda f: f.start_us).fault_type if active else "none"
        xs.append(as_vector(row))
        ys.append(label)
    return xs, ys


def dataset(
    suite: Suite, split: str, root: Path, workers: int | None = None
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.str_], npt.NDArray[np.int64]]:
    paths = episodes.ensure(manifests(suite, split), root, workers, suite.streams)
    house = HouseContext.canonical()
    xs: list[npt.NDArray[np.float64]] = []
    ys: list[str] = []
    groups: list[int] = []
    for i, path in enumerate(paths):
        x, y = night_rows(path, house)
        xs += x
        ys += y
        groups += [i] * len(y)
    return np.vstack(xs), np.array(ys), np.array(groups, dtype=np.int64)


def train(
    suite: Suite,
    *,
    root: Path = episodes.DEFAULT_ROOT,
    artifact: Path = ARTIFACT,
    card_data: Path = MODEL_CARD_DATA,
    seed: int = 0,
    workers: int | None = None,
    folds: int = 5,
) -> dict[str, Any]:
    x, y, groups = dataset(suite, "train", root, workers)
    base = HistGradientBoostingClassifier(
        learning_rate=0.05, max_iter=300, class_weight="balanced", random_state=seed
    )
    n_folds = min(folds, len(np.unique(groups)))  # small suites have few episodes
    splits = list(GroupKFold(n_splits=n_folds).split(x, y, groups))
    model = CalibratedClassifierCV(base, method="temperature", cv=splits)
    model.fit(x, y)
    payload = {
        "estimator": model,
        "classes": [str(c) for c in model.classes_],
        "features": list(FEATURES),
        "version": VERSION,
    }
    artifact.parent.mkdir(parents=True, exist_ok=True)
    blob = pickle.dumps(payload)
    artifact.write_bytes(blob)
    record = {
        "model": "l1_leak_model",
        "version": VERSION,
        "artifact": artifact.as_posix(),
        "artifact_sha256": hashlib.sha256(blob).hexdigest(),
        "trained_on": {
            "suite": suite.name,
            "split": "train",
            "generator": GENERATOR_VERSION,
            "episodes": int(groups.max()) + 1,
            "nights": len(y),
            "labels": dict(sorted(Counter(y.tolist()).items())),
        },
        "estimator": {
            "kind": "HistGradientBoostingClassifier + CalibratedClassifierCV(temperature)",
            "learning_rate": 0.05,
            "max_iter": 300,
            "class_weight": "balanced",
            "calibration_folds": f"GroupKFold({n_folds}) by episode",
            "seed": seed,
            "scikit_learn": sklearn.__version__,
        },
        "features": list(FEATURES),
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    card_data.parent.mkdir(parents=True, exist_ok=True)
    card_data.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return record
