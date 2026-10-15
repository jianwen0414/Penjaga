"""Generate episode directories once and reuse them.

Episodes live under ``<root>/<generator>/<episode_id>/`` (``var/eval/episodes`` by default, not
committed). A cached episode is reused only if its ``manifest.json`` equals the manifest asked
for; otherwise it is regenerated. Generation runs in parallel processes.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from penjaga_contract.lab import EpisodeManifest
from penjaga_sim import GENERATOR_VERSION
from penjaga_sim.export import manifest_document, run_episode

DEFAULT_ROOT = Path("var/eval/episodes")


def episode_dir(root: Path, manifest: EpisodeManifest) -> Path:
    generator = manifest.generator_version.replace("/", "-")
    return root / generator / manifest.episode_id


def _is_current(path: Path, manifest: EpisodeManifest) -> bool:
    stored = path / "manifest.json"
    if not (stored.exists() and (path / "observed" / "meta.json").exists()):
        return False
    return bool(json.loads(stored.read_text(encoding="utf-8")) == manifest_document(manifest))


def _generate(args: tuple[str, str]) -> str:
    manifest_json, out = args
    manifest = EpisodeManifest.model_validate_json(manifest_json)
    run_episode(manifest, Path(out), overwrite=True)
    return out


def ensure(manifests: list[EpisodeManifest], root: Path, workers: int | None = None) -> list[Path]:
    """Episode directories for ``manifests`` (in order), generating the missing ones."""
    paths = [episode_dir(root, m) for m in manifests]
    for m in manifests:
        if m.generator_version != GENERATOR_VERSION:
            raise ValueError(f"{m.episode_id}: manifest generator {m.generator_version!r}")
    todo = [
        (m.model_dump_json(by_alias=True), str(p))
        for m, p in zip(manifests, paths, strict=True)
        if not _is_current(p, m)
    ]
    if todo:
        n = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
        if n == 1 or len(todo) == 1:
            for item in todo:
                _generate(item)
        else:
            with ProcessPoolExecutor(max_workers=min(n, len(todo))) as pool:
                list(pool.map(_generate, todo))
    return paths
