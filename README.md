# Penjaga

**Penjaga** (Malay for *guardian*) is a preventive AI companion for the home. It reads the house's meters (water flow and pressure, the electrical distribution board) and never the people. It finds problems before they cause damage, shows where they are inside the walls on a 3D model, and helps the owner act on them. It is built for a representative Sime Darby Property Elmina Green Six double-storey terrace (SDP University Hackathon 2026, PEOPLE track).

> Status: under active development. The product requirements live in [docs/PRD.md](docs/PRD.md).

## What is real and what is simulated

| Real | Simulated |
|---|---|
| Detection models, safety floor, agent, 3D, apps, management integration, evaluation | Sensor readings (calibrated on cited public data) |
| Tariffs, weather, electrical limits, water-use patterns (from cited sources, see [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)) | Fault events (injected through the operator Lab) |
| Field evidence collected by the team (if any) | Vendors and utility submissions (mock) |

In this prototype, processing runs in the cloud. In production, detection is designed to run on an in-home hub so that only alerts and summaries leave the home.

## Repository layout

| Path | What |
|---|---|
| `packages/contract` | **House Data Contract (M0):** JSON Schemas, the canonical house record, the stream catalogue, generated Python and TypeScript types, validators. See [docs/design/contract.md](docs/design/contract.md). |
| `docs/` | PRD, decisions, dependencies, data sources, design specs, model cards (an Obsidian vault rooted at the repo) |
| `scripts/check.py` | One-command quality gate for the Python workspace |

The simulator (M2), stream and house-time service (M4), detection (M5) and evaluation harness (M11) are added under `services/` and `ml/`. Their interfaces are specified in [docs/design/interfaces.md](docs/design/interfaces.md).

## Run locally

Requires [uv](https://docs.astral.sh/uv/). Node.js is only needed to regenerate the TypeScript types.

```bash
uv sync                                        # creates .venv with Python 3.12 and all dev tools
uv run penjaga-contract validate               # schemas, house record, stream catalogue, reg. 36 checks
uv run python scripts/check.py                 # lint, types, import rules, contract, tests
uv run python packages/contract/tools/generate.py   # regenerate types + streams.json after a contract change
```

## Documentation

- [docs/PRD.md](docs/PRD.md): what we build and why (source of truth)
- [docs/DECISIONS.md](docs/DECISIONS.md): decision log
- [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md): versions, licences, official docs
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md): every number's source
- [docs/design/](docs/design/): contract and interface specifications
