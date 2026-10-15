"""Run every quality gate for the Python workspace with one command.

    uv run python scripts/check.py            # all gates
    uv run python scripts/check.py --fast     # skip TypeScript regeneration check

Gates: ruff lint, ruff format check, mypy (strict on sources, relaxed on tests), import-linter
rules (detection never imports ground truth), contract validation, and the test suite.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

SOURCE_DIRS = ["packages/contract/src", "packages/contract/tools", "scripts"]
TEST_DIRS = ["tests", "packages/contract/tests"]


def gates(fast: bool) -> list[tuple[str, list[str]]]:
    pytest_args = [PY, "-m", "pytest", "-q"]
    if fast:
        pytest_args += ["-k", "not typescript"]
    return [
        ("ruff lint", [PY, "-m", "ruff", "check", "."]),
        ("ruff format", [PY, "-m", "ruff", "format", "--check", "."]),
        ("mypy sources (strict)", [PY, "-m", "mypy", *SOURCE_DIRS]),
        (
            "mypy tests",
            [PY, "-m", "mypy", *TEST_DIRS, "--allow-untyped-defs", "--allow-incomplete-defs"],
        ),
        (
            "import rules",
            [PY, "-c", "from importlinter.cli import lint_imports_command; lint_imports_command()"],
        ),
        ("contract", [PY, "-m", "penjaga_contract.cli", "validate"]),
        ("tests", pytest_args),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--fast", action="store_true", help="skip the TypeScript drift check")
    args = parser.parse_args()
    env = {**os.environ, "PYTHONUTF8": "1"}
    failed: list[str] = []
    for name, cmd in gates(args.fast):
        start = time.perf_counter()
        result = subprocess.run(cmd, cwd=ROOT, env=env, check=False)
        status = "ok" if result.returncode == 0 else "FAILED"
        print(f"==> {name}: {status} ({time.perf_counter() - start:.1f}s)", flush=True)
        if result.returncode != 0:
            failed.append(name)
    print("all gates passed" if not failed else f"failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
