"""``penjaga-api``: the Core API.

    penjaga-api serve --dev-tokens var/dev-tokens.json    # local, next to the data/ML dev stack

Start the stream service, simulator and detection first (``scripts/dev_stack.py`` starts all of
them and this API). Tokens:
- **outbound** (to detection and the stream service): the app role token, from ``PENJAGA_TOKEN_APP``
  or the stream service's dev tokens file;
- **callers**: ``PENJAGA_TOKEN_HOMEOWNER``, ``PENJAGA_TOKEN_MANAGEMENT`` and
  ``PENJAGA_TOKEN_OPERATOR``. In development the first two come from ``var/api-tokens.json``
  (created with random tokens on first use, git-ignored), and the operator token from the dev
  tokens file.

The Core API holds no simulator token and never sees ground truth.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sys
from pathlib import Path

import uvicorn

from penjaga_api.agent import evaluate
from penjaga_api.agent.config import DEFAULT_FALLBACKS, DEFAULT_MODEL, AgentConfig
from penjaga_api.agent.openrouter import OpenRouterClient
from penjaga_api.api import create_app
from penjaga_api.service import ApiService, Role
from penjaga_api.store import ApiStore
from penjaga_api.upstream import HttpUpstream

REPO = Path(__file__).resolve().parents[4]
log = logging.getLogger("penjaga_api")


def _read(path: Path) -> dict[str, str]:
    data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    return data


def dev_api_tokens(path: Path) -> dict[str, str]:
    """Homeowner and management tokens for local runs; created once, then reused."""
    if path.exists():
        return _read(path)
    tokens = {role: secrets.token_urlsafe(24) for role in ("homeowner", "management")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2) + "\n", encoding="utf-8", newline="\n")
    return tokens


def resolve_tokens(dev_tokens: Path | None, api_tokens: Path) -> tuple[str | None, dict[Role, str]]:
    """(outbound app token, caller tokens by role) from the environment or dev files."""
    env = {r: os.environ.get(f"PENJAGA_TOKEN_{r.upper()}") for r in ("app", *Role)}
    stream_dev = _read(dev_tokens) if dev_tokens is not None else {}
    api_dev = dev_api_tokens(api_tokens) if dev_tokens is not None else {}
    outbound = env["app"] or stream_dev.get("app")
    callers: dict[Role, str] = {}
    for role in Role:
        value = (
            env[role.value]
            or api_dev.get(role.value)
            or (stream_dev.get("operator") if role is Role.operator else None)
        )
        if value:
            callers[role] = value
    return outbound, callers


def load_env_file(path: Path) -> None:
    """``KEY=value`` lines from ``path`` into the environment, without overriding what is set.
    Values are never printed."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _agent() -> OpenRouterClient | None:
    config = AgentConfig.from_env()
    if not config.enabled:
        log.info("AI explanations and chat: off (no OPENROUTER_API_KEY); templates only")
        return None
    log.info("AI explanations and chat: on, models %s", ", ".join(config.models))
    return OpenRouterClient(config)


def _cmd_serve(args: argparse.Namespace) -> int:
    outbound, callers = resolve_tokens(args.dev_tokens, args.api_tokens)
    if outbound is None or not callers:
        print(
            "need the app token (PENJAGA_TOKEN_APP) and at least one caller token "
            "(PENJAGA_TOKEN_HOMEOWNER / _MANAGEMENT / _OPERATOR), or pass --dev-tokens",
            file=sys.stderr,
        )
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    upstream = HttpUpstream(stream_url=args.stream_url, detect_url=args.detect_url, token=outbound)
    store = ApiStore(args.data_dir / "api.sqlite")
    load_env_file(args.env_file)
    service = ApiService(store, upstream, llm=_agent())
    try:
        uvicorn.run(create_app(service, tokens=callers), host=args.host, port=args.port)
    finally:
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="penjaga-api", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sv = sub.add_parser("serve", help="run the Core API")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8705)
    sv.add_argument("--stream-url", default="http://127.0.0.1:8701")
    sv.add_argument("--detect-url", default="http://127.0.0.1:8703")
    sv.add_argument("--data-dir", type=Path, default=REPO / "var" / "api")
    sv.add_argument("--dev-tokens", type=Path, help="tokens file from `penjaga-stream serve --dev`")
    sv.add_argument("--api-tokens", type=Path, default=REPO / "var" / "api-tokens.json")
    sv.add_argument("--env-file", type=Path, default=REPO / ".env", help="OPENROUTER_API_KEY etc.")
    ev = sub.add_parser("eval-agent", help="score candidate models live (uses the OpenRouter key)")
    ev.add_argument("--models", nargs="+", default=[DEFAULT_MODEL, *DEFAULT_FALLBACKS])
    ev.add_argument("--out", type=Path, default=REPO / "var" / "agent-eval.md")
    ev.add_argument("--env-file", type=Path, default=REPO / ".env")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return _cmd_serve(args)
    if args.command == "eval-agent":
        load_env_file(args.env_file)
        return evaluate.run(args.models, args.out, REPO / "var" / "agent-eval")
    return 2  # pragma: no cover (argparse enforces the subcommand)


if __name__ == "__main__":
    sys.exit(main())
