"""``penjaga-contract`` command line: validate the canonical contract or a document."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from penjaga_contract.resources import (
    load_house_document,
    load_streams_document,
    read_json,
    schema_paths,
)
from penjaga_contract.validate import (
    Report,
    load_context,
    validate_catalogue_document,
    validate_event_document,
    validate_finding_document,
    validate_house_document,
    validate_reading_batch_document,
)

DOCUMENT_VALIDATORS = {
    "finding": validate_finding_document,
    "reading_batch": validate_reading_batch_document,
    "event": validate_event_document,
}


def _print(report: Report, label: str) -> None:
    for issue in report.issues:
        print(f"{label}: {issue}")


def validate_all() -> int:
    """Meta-validate every schema, then validate house.json and streams.json."""
    failed = False
    for path in schema_paths():
        try:
            Draft202012Validator.check_schema(read_json(path))
        except Exception as exc:  # report every bad schema, not just the first
            failed = True
            print(f"{path.name}: invalid JSON Schema: {exc}")
    house_doc = load_house_document()
    house = validate_house_document(house_doc)
    _print(house, "house.json")
    streams = validate_catalogue_document(load_streams_document(), house_doc)
    _print(streams, "streams.json")
    failed = failed or not house.ok or not streams.ok
    warnings = len(house.warnings) + len(streams.warnings)
    print("contract INVALID" if failed else f"contract valid ({warnings} warning(s))")
    return 1 if failed else 0


def validate_document(kind: str, path: Path) -> int:
    doc = json.loads(path.read_text(encoding="utf-8"))
    report = DOCUMENT_VALIDATORS[kind](doc, load_context())
    _print(report, path.name)
    print(f"{path.name}: {'valid' if report.ok else 'INVALID'}")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="penjaga-contract", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate schemas, house.json and streams.json")
    doc = sub.add_parser("check", help="validate one runtime document against the contract")
    doc.add_argument("kind", choices=sorted(DOCUMENT_VALIDATORS))
    doc.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    if args.command == "validate":
        return validate_all()
    return validate_document(args.kind, args.path)


if __name__ == "__main__":
    sys.exit(main())
