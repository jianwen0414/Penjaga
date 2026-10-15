"""Regenerate (or check) every derived artifact of the House Data Contract.

Derived artifacts, all committed and never edited by hand:
  * Python types:     src/penjaga_contract/generated/{core,lab}/*_schema.py (datamodel-codegen)
  * TypeScript types: ts/generated/{core,lab}/*.d.ts (json-schema-to-typescript via npx)
  * Stream catalogue: src/penjaga_contract/data/streams.json (penjaga_contract.catalogue)

Usage (from the repo root):
  uv run python packages/contract/tools/generate.py            # write everything
  uv run python packages/contract/tools/generate.py --check    # exit 1 if anything drifted
  add --skip-ts when Node.js/npx is unavailable (TypeScript is then neither written nor checked)
"""

from __future__ import annotations

import argparse
import difflib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from penjaga_contract.catalogue import build_catalogue
from penjaga_contract.resources import dump_json, load_house_document

CONTRACT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = CONTRACT_DIR / "src" / "penjaga_contract"
SCHEMAS_DIR = SRC_DIR / "schemas"
PY_OUT_DIR = SRC_DIR / "generated"
TS_OUT_DIR = CONTRACT_DIR / "ts" / "generated"
STREAMS_PATH = SRC_DIR / "data" / "streams.json"

# Pinned so output is byte-identical across machines (see docs/DEPENDENCIES.md).
JSON2TS = "json-schema-to-typescript@16.0.0"

DATAMODEL_CODEGEN_ARGS = [
    "--input-file-type",
    "jsonschema",
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--base-class",
    "penjaga_contract.base.ContractModel",
    "--target-python-version",
    "3.12",
    "--use-standard-collections",
    "--use-union-operator",
    "--use-annotated",
    "--field-constraints",
    "--use-schema-description",
    "--use-field-description",
    "--use-title-as-name",
    "--use-double-quotes",
    "--disable-timestamp",
    "--extra-fields",
    "forbid",
    "--output-datetime-class",
    "AwareDatetime",
    "--no-treat-dot-as-module",
    "--keep-model-order",
    "--collapse-root-models",
    "--use-type-alias",
    "--formatters",
    "ruff-format",
    "ruff-check",
]

PY_PACKAGE_DOCSTRING = (
    '"""GENERATED from penjaga_contract/schemas by packages/contract/tools/generate.py.\n\n'
    "Do not edit by hand: change the JSON Schema and regenerate.\n"
    '"""\n'
)

TS_HEADER = (
    "// GENERATED from packages/contract/src/penjaga_contract/schemas by\n"
    "// packages/contract/tools/generate.py. Do not edit by hand.\n"
)
TS_BANNER = (
    "/* GENERATED from packages/contract/src/penjaga_contract/schemas by "
    "packages/contract/tools/generate.py. Do not edit by hand. */"
)


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n")


def _read_tree(root: Path, pattern: str) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): _normalise(p.read_text(encoding="utf-8"))
        for p in sorted(root.rglob(pattern))
        if p.is_file() and "__pycache__" not in p.parts and ".ruff_cache" not in p.parts
    }


def _write_tree(root: Path, files: dict[str, str]) -> None:
    if root.exists():
        shutil.rmtree(root)
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


def generate_python() -> dict[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "generated"
        cmd = [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(SCHEMAS_DIR),
            "--output",
            str(out),
            *DATAMODEL_CODEGEN_ARGS,
        ]
        subprocess.run(cmd, check=True, cwd=CONTRACT_DIR)
        files = _read_tree(out, "*.py")
    files["__init__.py"] = PY_PACKAGE_DOCSTRING
    for ns in ("core", "lab"):
        key = f"{ns}/__init__.py"
        if key not in files:
            raise RuntimeError(f"datamodel-codegen produced no {key}")
        files[key] = PY_PACKAGE_DOCSTRING
    return files


def _npx() -> str | None:
    return shutil.which("npx") or shutil.which("npx.cmd")


def generate_typescript() -> dict[str, str]:
    npx = _npx()
    if npx is None:
        raise RuntimeError("npx not found: install Node.js or pass --skip-ts")
    files: dict[str, str] = {}
    index_lines = [TS_HEADER]
    with tempfile.TemporaryDirectory() as tmp:
        for schema in sorted(SCHEMAS_DIR.glob("*/*.schema.json")):
            ns = schema.parent.name
            stem = schema.name.removesuffix(".schema.json")
            out = Path(tmp) / f"{ns}_{stem}.d.ts"
            cmd = [
                npx,
                "--yes",
                JSON2TS,
                "--input",
                str(schema),
                "--output",
                str(out),
                "--cwd",
                str(schema.parent),
                "--unreachableDefinitions",
                "--maxItems",
                "-1",
                "--bannerComment",
                TS_BANNER,
            ]
            subprocess.run(cmd, check=True, cwd=CONTRACT_DIR)
            files[f"{ns}/{stem}.d.ts"] = _normalise(out.read_text(encoding="utf-8"))
            index_lines.append(f'export type * as {ns}_{stem} from "./{ns}/{stem}";\n')
    files["index.d.ts"] = "".join(index_lines)
    return files


def generate_streams() -> dict[str, str]:
    return {"streams.json": dump_json(build_catalogue(load_house_document()))}


def _diff(label: str, expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    problems = []
    for rel in sorted(set(expected) | set(actual)):
        if rel not in actual:
            problems.append(f"{label}: missing {rel}")
        elif rel not in expected:
            problems.append(f"{label}: unexpected {rel}")
        elif expected[rel] != actual[rel]:
            diff = difflib.unified_diff(
                actual[rel].splitlines(),
                expected[rel].splitlines(),
                f"committed/{rel}",
                f"regenerated/{rel}",
                lineterm="",
                n=1,
            )
            problems.append(f"{label}: {rel} is stale\n" + "\n".join(list(diff)[:40]))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--check", action="store_true", help="verify committed artifacts; write nothing"
    )
    parser.add_argument(
        "--skip-ts", action="store_true", help="do not generate or check TypeScript"
    )
    args = parser.parse_args(argv)

    targets: list[tuple[str, Path, str, dict[str, str]]] = [
        ("python", PY_OUT_DIR, "*.py", generate_python()),
        ("streams", STREAMS_PATH.parent, "streams.json", generate_streams()),
    ]
    if not args.skip_ts:
        targets.append(("typescript", TS_OUT_DIR, "*.ts", generate_typescript()))

    if args.check:
        problems: list[str] = []
        for label, root, pattern, expected in targets:
            problems += _diff(label, expected, _read_tree(root, pattern))
        for p in problems:
            print(p, file=sys.stderr)
        print("contract artifacts up to date" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    for label, root, _pattern, files in targets:
        if label == "streams":
            STREAMS_PATH.write_text(files["streams.json"], encoding="utf-8", newline="\n")
        else:
            _write_tree(root, files)
        print(f"wrote {label}: {len(files)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
