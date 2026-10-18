"""Agent configuration from the environment. Model IDs are config, never code (PRD §18.1 🔒).

OPENROUTER_API_KEY        the key (backend .env only; never logged or printed)
PENJAGA_MODEL             primary model (default: DeepSeek V4.1 Flash)
PENJAGA_MODEL_FALLBACKS   comma-separated fallbacks, tried in order by OpenRouter
PENJAGA_LLM_TIMEOUT_S     per-request timeout in seconds (default 45)
PENJAGA_REASONING_EFFORT  reasoning effort for reasoning models: low (default), none, high...
                          (DeepSeek V4.1 Flash defaults to high, which the tasks don't need)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_FALLBACKS = ("anthropic/claude-haiku-5.5",)


@dataclass(frozen=True)
class AgentConfig:
    api_key: str | None
    model: str = DEFAULT_MODEL
    fallbacks: tuple[str, ...] = DEFAULT_FALLBACKS
    timeout_s: float = 45.0
    base_url: str = "https://openrouter.ai/api/v1"
    app_url: str = "https://penjaga.invalid"  # HTTP-Referer identifies the app, not a person
    max_tokens: int = 1200
    reasoning_effort: str = "low"  # "" leaves the provider's default

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def models(self) -> list[str]:
        """Priority order for OpenRouter's ``models`` fallback list."""
        return list(dict.fromkeys([self.model, *self.fallbacks]))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AgentConfig:
        e = os.environ if env is None else env
        raw = e.get("PENJAGA_MODEL_FALLBACKS", ",".join(DEFAULT_FALLBACKS))
        return cls(
            api_key=e.get("OPENROUTER_API_KEY") or None,
            model=e.get("PENJAGA_MODEL", "").strip() or DEFAULT_MODEL,
            fallbacks=tuple(m.strip() for m in raw.split(",") if m.strip()),
            timeout_s=float(e.get("PENJAGA_LLM_TIMEOUT_S", "45")),
            reasoning_effort=e.get("PENJAGA_REASONING_EFFORT", "low").strip(),
        )

    def __repr__(self) -> str:  # never show the key
        key = "set" if self.api_key else "unset"
        return f"AgentConfig(api_key={key}, models={self.models!r})"
