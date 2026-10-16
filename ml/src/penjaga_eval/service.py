"""The evaluation service behind ``/eval/v1``: the Lab starts M11 runs and reads their results.

**Runs.** A run executes ``penjaga-eval run`` in a subprocess, one at a time in request order.
The API stays responsive, a crash is contained, and every run goes through the same code path
as the CLI.
- Test-split runs are refused unless the suite is sealed for the current generator.
- Like CLI test runs, they are logged with the git commit.

**State.** Each run's state (``run.json``), full report (``report.json``, ``report.md``) and
log (``run.log``) live in its own directory under ``runs_dir``. After a restart:
- queued runs are queued again;
- a run that was running is marked failed (its process died with the service).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from penjaga_detect import registry
from penjaga_detect.deployment import Deployment
from penjaga_eval import episodes, sealing
from penjaga_eval.suites import SPLITS, SUITES_DIR, Suite, SuiteError, load_suite, manifests
from penjaga_sim import GENERATOR_VERSION

log = logging.getLogger("penjaga_eval.service")

REPO = Path(__file__).resolve().parents[3]
LOG_TAIL = 2000


class EvalRequestError(ValueError):
    """The run cannot be started as asked (422)."""


class UnknownRunError(LookupError):
    """No such run (404)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def detector_layer(name: str) -> str:
    """Layer of a registered detector, from the naming convention ``l<N>_...`` (tested)."""
    return name.split("_", 1)[0].upper()


@dataclass(frozen=True)
class EvalConfig:
    runs_dir: Path
    suites_dir: Path = SUITES_DIR
    episodes_root: Path = episodes.DEFAULT_ROOT
    sealed_dir: Path = sealing.SEALED_DIR
    test_log: Path = sealing.TEST_LOG
    workers: int | None = None
    python: str = sys.executable
    deployment: Path | None = None  # deployment.toml (None: penjaga_detect's default)


class EvalService:
    def __init__(self, config: EvalConfig) -> None:
        self.config = config
        self.config.runs_dir.mkdir(parents=True, exist_ok=True)
        deployment = (
            Deployment.load() if config.deployment is None else Deployment.load(config.deployment)
        )
        self.deployed = deployment.detectors
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._suites: dict[str, dict[str, Any]] = {}
        self.running: str | None = None
        self._recover()

    # ------------------------------------------------------------------ suites

    def _suite(self, name: str) -> Suite:
        try:
            return load_suite(name, self.config.suites_dir)
        except SuiteError as exc:
            raise EvalRequestError(str(exc)) from exc

    def suite_names(self) -> list[str]:
        return sorted(p.stem for p in self.config.suites_dir.glob("*.toml"))

    def suite(self, name: str) -> dict[str, Any]:
        if name not in self._suites:
            suite = self._suite(name)
            counts = {split: len(manifests(suite, split)) for split in SPLITS}
            self._suites[name] = {
                "name": name,
                "layers": list(suite.layers),
                "days": suite.days,
                "episodes": counts,
                "generator": GENERATOR_VERSION,
                "detectors": sorted(
                    n for n in registry.REGISTRY if detector_layer(n) in suite.layers
                ),
            }
        doc = dict(self._suites[name])
        doc["sealed"] = self._sealed(self._suite(name))  # may change while running
        return doc

    def _sealed(self, suite: Suite) -> bool:
        try:
            sealing.check(suite, manifests(suite, "test"), self.config.sealed_dir)
        except sealing.SealError:
            return False
        return True

    # ------------------------------------------------------------------ runs

    def _dir(self, run_id: str) -> Path:
        if not run_id.startswith("evr_") or not run_id[4:].isalnum():
            raise UnknownRunError(f"no run {run_id!r}")
        return self.config.runs_dir / run_id

    def _save(self, doc: dict[str, Any]) -> None:
        path = self._dir(doc["run_id"]) / "run.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
        tmp.replace(path)

    def get(self, run_id: str) -> dict[str, Any]:
        path = self._dir(run_id) / "run.json"
        if not path.exists():
            raise UnknownRunError(f"no run {run_id!r}")
        return dict(json.loads(path.read_text(encoding="utf-8")))

    def runs(self) -> list[dict[str, Any]]:
        docs = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in self.config.runs_dir.glob("evr_*/run.json")
        ]
        return sorted(docs, key=lambda d: (d["created_at"], d["run_id"]), reverse=True)

    def report_path(self, run_id: str, name: str) -> Path:
        doc = self.get(run_id)
        if doc["status"] != "complete":
            raise UnknownRunError(f"run {run_id} has no report (status {doc['status']})")
        return self._dir(run_id) / name

    def create(
        self,
        suite_name: str,
        split: str,
        detectors: Sequence[str] | None,
        limit: int | None,
    ) -> dict[str, Any]:
        suite = self._suite(suite_name)
        allowed = {n for n in registry.REGISTRY if detector_layer(n) in suite.layers}
        if detectors is None:
            chosen = [n for n in self.deployed if n in allowed]
            if not chosen:
                raise EvalRequestError(f"no deployed detector covers {list(suite.layers)}")
        else:
            chosen = list(detectors)
            unknown = sorted(set(chosen) - allowed)
            if unknown:
                raise EvalRequestError(f"{unknown} cannot be evaluated on {suite_name}")
        if split == "test":
            if limit is not None:
                raise EvalRequestError("a test run uses the whole sealed split")
            if not self._sealed(suite):
                raise EvalRequestError(f"{suite_name}: the test split is not sealed")
        elif split != "validation":
            raise EvalRequestError("split must be validation or test")
        doc: dict[str, Any] = {
            "run_id": f"evr_{secrets.token_hex(6)}",
            "suite": suite_name,
            "split": split,
            "detectors": chosen,
            "limit": limit,
            "status": "queued",
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "generator": GENERATOR_VERSION,
            "episodes": None,
            "git_commit": None,
            "results": None,
        }
        self._save(doc)
        self._queue.put_nowait(doc["run_id"])
        return doc

    def _recover(self) -> None:
        for doc in sorted(self.runs(), key=lambda d: d["created_at"]):
            if doc["status"] == "running":
                doc.update(status="failed", finished_at=_now(),
                           error="interrupted: the evaluation service restarted")  # fmt: skip
                self._save(doc)
            elif doc["status"] == "queued":
                self._queue.put_nowait(doc["run_id"])

    # ------------------------------------------------------------------ worker

    async def serve(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                run_id = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except TimeoutError:
                continue
            try:
                await self._execute(run_id, stop)
            except Exception as exc:  # a broken run must not stop the queue
                log.exception("run %s failed", run_id)
                doc = self.get(run_id)
                doc.update(status="failed", finished_at=_now(), error=str(exc)[:LOG_TAIL])
                self._save(doc)
            finally:
                self.running = None

    def command(self, doc: dict[str, Any]) -> list[str]:
        c = self.config
        out = self._dir(doc["run_id"])
        cmd = [c.python, "-m", "penjaga_eval.cli",
               "--suites-dir", str(c.suites_dir), "--episodes-root", str(c.episodes_root),
               "--sealed-dir", str(c.sealed_dir), "--test-log", str(c.test_log),
               "--runs-dir", str(out)]  # fmt: skip
        if c.workers is not None:
            cmd += ["--workers", str(c.workers)]
        cmd += ["run", "--suite", doc["suite"], "--split", doc["split"]]
        for name in doc["detectors"]:
            cmd += ["--detector", name]
        if doc["limit"] is not None:
            cmd += ["--limit", str(doc["limit"])]
        return [*cmd, "--markdown", str(out / "report.md")]

    async def _execute(self, run_id: str, stop: asyncio.Event) -> None:
        doc = self.get(run_id)
        if doc["status"] != "queued":
            return
        self.running = run_id
        doc.update(status="running", started_at=_now())
        self._save(doc)
        out = self._dir(run_id)
        with (out / "run.log").open("wb") as log_file:
            proc = await asyncio.create_subprocess_exec(
                *self.command(doc),
                cwd=REPO,
                stdout=asyncio.subprocess.PIPE,
                stderr=log_file,
                env={**os.environ, "PYTHONUTF8": "1"},
            )
            waiter = asyncio.create_task(proc.communicate())
            stopper = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if waiter not in done:  # the service is stopping: end the run, mark it failed
                proc.kill()
                with contextlib.suppress(Exception):
                    await waiter
                doc.update(status="failed", finished_at=_now(), error="stopped with the service")
                self._save(doc)
                return
            stopper.cancel()
            stdout, _ = waiter.result()
        if proc.returncode != 0:
            tail = (out / "run.log").read_text(encoding="utf-8", errors="replace")[-LOG_TAIL:]
            doc.update(status="failed", finished_at=_now(), error=tail or f"exit {proc.returncode}")
            self._save(doc)
            return
        summary = json.loads(stdout.decode("utf-8"))
        report = json.loads(Path(summary["report"]).read_text(encoding="utf-8"))
        (out / "report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        doc.update(
            status="complete",
            finished_at=_now(),
            episodes=report["episodes"],
            git_commit=report["git"]["commit"],
            results=results(report),
        )
        self._save(doc)


def results(report: dict[str, Any]) -> dict[str, Any]:
    """The headline numbers of a report, per detector and layer (EvalRun.results)."""
    out: dict[str, Any] = {}
    for name, d in report["detectors"].items():
        layers = {}
        for layer, m in d["metrics"].items():
            layers[layer] = {
                "recall": m["recall"],
                "recall_located": m["recall_located"],
                "precision": m["precision"],
                "false_alarms_per_house_month": m["false_alarms_per_house_month"],
                "faults": m["faults"],
                "detected": m["detected"],
                "by_fault_type": {
                    ft: {
                        "n": t["n"],
                        "recall": t["recall"],
                        "ttd_hours_median": t["ttd_hours_median"],
                    }
                    for ft, t in m["by_fault_type"].items()
                },
            }
        out[name] = {"version": d["version"], "layers": layers}
    return out
