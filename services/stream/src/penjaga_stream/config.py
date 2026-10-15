"""Configuration and role-based access for the stream service.

Roles (PRD §24 role-based access):
  operator  Lab: clock control, resets, raw data, live view
  device    producers: the simulator now, a device gateway later; ingest only
  detector  detection service: lossless subscription, raw and summary queries
  app       Core API / agent / homeowner app: summaries, events, live view

Each role has a bearer token taken from the environment (PENJAGA_TOKEN_<ROLE>). No token is
ever stored in the repository; ``penjaga-stream serve --dev`` generates random ones for local
runs and writes them to var/dev-tokens.json (git-ignored).
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Role(StrEnum):
    operator = "operator"
    device = "device"
    detector = "detector"
    app = "app"


def tokens_from_env(env: Mapping[str, str] | None = None) -> dict[Role, str]:
    env = os.environ if env is None else env
    tokens = {}
    for role in Role:
        value = env.get(f"PENJAGA_TOKEN_{role.value.upper()}")
        if value:
            if len(value) < 16:
                raise ValueError(
                    f"PENJAGA_TOKEN_{role.value.upper()} must be at least 16 characters"
                )
            tokens[role] = value
    return tokens


def generate_dev_tokens(path: Path) -> dict[Role, str]:
    """Random tokens for local development, written to ``path`` for the other dev processes."""
    tokens = {role: secrets.token_urlsafe(24) for role in Role}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({r.value: t for r, t in tokens.items()}, indent=2), encoding="utf-8")
    return tokens


def load_dev_tokens(path: Path) -> dict[Role, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {Role(k): v for k, v in data.items()}


@dataclass(frozen=True)
class StreamConfig:
    data_dir: Path
    tokens: Mapping[Role, str]
    lossless_queue_size: int = 256
    lossless_put_timeout_s: float = 10.0
    max_summary_points: int = 500
    max_raw_samples: int = 100_000
    max_events: int = 10_000
    store_flush_interval_s: float = 0.25
    governor_interval_s: float = 0.5
    live_max_hz: tuple[float, float] = (0.2, 10.0)

    def role_for(self, token: str | None) -> Role | None:
        """Constant-time lookup of the role a bearer token belongs to."""
        if not token:
            return None
        found = None
        for role, expected in self.tokens.items():
            if secrets.compare_digest(token.encode(), expected.encode()):
                found = role
        return found
