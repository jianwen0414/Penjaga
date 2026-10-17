"""Generate episode directories once and reuse them.

Episodes live under ``<root>/<generator>/<episode_id>/`` (``var/eval/episodes`` by default, not
committed). A cached episode is reused only if its ``manifest.json`` equals the manifest asked
for; otherwise it is regenerated. Generation runs in parallel processes.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from penjaga_contract.lab import EpisodeManifest
from penjaga_sim import GENERATOR_VERSION
from penjaga_sim.export import kept_streams, manifest_document, run_episode

DEFAULT_ROOT = Path("var/eval/episodes")
MIN_FREE_BYTES = 5 * 1024**3  # never let generation fill the disk


class DiskSpaceError(RuntimeError):
    pass


def check_free_space(root: Path, minimum: int = MIN_FREE_BYTES) -> None:
    """Refuse to generate when the disk holding ``root`` is nearly full."""
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    if free < minimum:
        raise DiskSpaceError(
            f"only {free / 1024**3:.1f} GB free under {root}; episode generation needs at least "
            f"{minimum / 1024**3:.0f} GB (delete old episodes under {root} first)"
        )


def episode_dir(root: Path, manifest: EpisodeManifest) -> Path:
    generator = manifest.generator_version.replace("/", "-")
    return root / generator / manifest.episode_id


def _is_current(path: Path, manifest: EpisodeManifest, streams: Sequence[str] | None) -> bool:
    stored, meta = path / "manifest.json", path / "observed" / "meta.json"
    water = path / "truth" / "water.parquet"  # written by every penjaga-sim/4+ export
    if not (stored.exists() and meta.exists() and water.exists()):
        return False
    if json.loads(stored.read_text(encoding="utf-8")) != manifest_document(manifest):
        return False
    kept = json.loads(meta.read_text(encoding="utf-8"))["streams"]
    return bool(kept == kept_streams(manifest, streams))


def _generate(args: tuple[str, str, tuple[str, ...] | None]) -> str:
    manifest_json, out, streams = args
    manifest = EpisodeManifest.model_validate_json(manifest_json)
    run_episode(manifest, Path(out), overwrite=True, streams=streams)
    return out


def ensure(
    manifests: list[EpisodeManifest],
    root: Path,
    workers: int | None = None,
    streams: Sequence[str] | None = None,
) -> list[Path]:
    """Episode directories for ``manifests`` (in order), generating the missing ones."""
    paths = [episode_dir(root, m) for m in manifests]
    for m in manifests:
        if m.generator_version != GENERATOR_VERSION:
            raise ValueError(f"{m.episode_id}: manifest generator {m.generator_version!r}")
    todo = [
        (m.model_dump_json(by_alias=True), str(p), None if streams is None else tuple(streams))
        for m, p in zip(manifests, paths, strict=True)
        if not _is_current(p, m, streams)
    ]
    if todo:
        check_free_space(root)
        n = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
        if n == 1 or len(todo) == 1:
            for item in todo:
                _generate(item)
        else:
            with ProcessPoolExecutor(max_workers=min(n, len(todo))) as pool:
                list(pool.map(_generate, todo))
    return paths
