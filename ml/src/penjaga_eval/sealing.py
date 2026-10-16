"""The test split is sealed: its manifests are fixed by a recorded hash, and every test run is
logged (PRD §21: never tune on the test set).

``seal`` records the digest of a suite's test manifests in ``ml/sealed/<suite>.json``
(committed). ``check`` refuses a test run whose manifests differ from the sealed ones.
``log_test_run`` appends each test run, with the git commit, to ``ml/runs/test-log.jsonl``
(committed), so how often the test set was looked at stays visible.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from penjaga_contract.lab import EpisodeManifest
from penjaga_eval.suites import Suite, manifests, manifests_digest

ML_DIR = Path(__file__).resolve().parents[2]
SEALED_DIR = ML_DIR / "sealed"
TEST_LOG = ML_DIR / "runs" / "test-log.jsonl"


class SealError(RuntimeError):
    pass


def seal(suite: Suite, directory: Path = SEALED_DIR, *, reseal: bool = False) -> dict[str, Any]:
    items = manifests(suite, "test")
    record = {
        "suite": suite.name,
        "split": "test",
        "episodes": len(items),
        "generator": items[0].generator_version if items else None,
        "digest": manifests_digest(items),
    }
    path = directory / f"{suite.name}.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing["digest"] == record["digest"]:
            return dict(existing)
        if not reseal:
            raise SealError(
                f"{suite.name}: the test manifests changed since they were sealed; "
                "resealing replaces the test set (pass reseal=True deliberately)"
            )
    record["sealed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return record


def check(suite: Suite, items: list[EpisodeManifest], directory: Path = SEALED_DIR) -> None:
    path = directory / f"{suite.name}.json"
    if not path.exists():
        raise SealError(f"{suite.name}: the test split is not sealed (penjaga-eval seal)")
    sealed = json.loads(path.read_text(encoding="utf-8"))
    if sealed["digest"] != manifests_digest(items):
        raise SealError(f"{suite.name}: test manifests differ from the sealed ones")


def git_state(cwd: Path = ML_DIR) -> dict[str, Any]:
    def git(*args: str) -> str:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
        return out.stdout.strip()

    return {"commit": git("rev-parse", "HEAD") or None, "dirty": bool(git("status", "--porcelain"))}


def log_test_run(entry: dict[str, Any], path: Path = TEST_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
