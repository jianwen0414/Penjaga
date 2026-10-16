"""Finding store: idempotent writes, latest revisions, history, the SSE cursor, guided checks."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from penjaga_detect.store import FindingStore


def doc(
    fid: str, revision: int, *, layer: str = "L1", status: str = "active", **extra: Any
) -> dict[str, Any]:
    return {"id": fid, "revision": revision, "layer": layer, "status": status, **extra}


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FindingStore]:
    s = FindingStore(tmp_path / "f.sqlite")
    yield s
    s.close()


def test_replaying_an_episode_stores_nothing_twice(store: FindingStore) -> None:
    first = store.add("1@0", [(doc("a", 1), 10), (doc("a", 2), 20)])
    assert [r.seq for r in first] == [1, 2]
    assert store.add("1@0", [(doc("a", 1), 10), (doc("a", 2), 20)]) == []
    assert store.conflicts == 0
    assert store.last_seq() == 2


def test_a_different_document_under_the_same_revision_is_a_conflict(store: FindingStore) -> None:
    store.add("1@0", [(doc("a", 1, note="x"), 10)])
    assert store.add("1@0", [(doc("a", 1, note="y"), 10)]) == []
    assert store.conflicts == 1
    assert store.history("1@0", "a")[0]["note"] == "x"  # the stored one is kept


def test_latest_history_and_filters(store: FindingStore) -> None:
    store.add(
        "1@0",
        [
            (doc("a", 1), 10),
            (doc("b", 1, layer="L2"), 15),
            (doc("a", 2, status="cleared"), 30),
        ],
    )
    store.add("2@99", [(doc("a", 1), 5)])  # another run never mixes in
    latest = store.latest("1@0")
    assert [(d["id"], d["revision"]) for d in latest] == [("a", 2), ("b", 1)]
    assert [d["id"] for d in store.latest("1@0", status="active")] == ["b"]
    assert [d["id"] for d in store.latest("1@0", layer="L1")] == ["a"]
    assert [d["id"] for d in store.latest("1@0", since_us=20)] == ["a"]
    assert [d["revision"] for d in store.history("1@0", "a")] == [1, 2]
    assert store.history("1@0", "zzz") == []


def test_cursor_returns_revisions_in_storage_order(store: FindingStore) -> None:
    store.add("1@0", [(doc("a", 1), 10), (doc("b", 1), 11)])
    store.add("1@0", [(doc("a", 2), 12)])
    assert [(r.doc["id"], r.doc["revision"]) for r in store.after(1)] == [("b", 1), ("a", 2)]
    assert store.after(3) == []


def test_checks_round_trip_and_pending_list(store: FindingStore) -> None:
    check = {"check_id": "chk_000001", "status": "pending"}
    store.put_check("1@0", check)
    assert store.get_check("chk_000001") == ("1@0", check)
    assert store.pending_checks("1@0") == [check]
    store.put_check("1@0", {**check, "status": "complete"})
    assert store.pending_checks("1@0") == []
    assert store.get_check("nope") is None


def test_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "f.sqlite"
    s = FindingStore(path)
    s.add("1@0", [(doc("a", 1), 10)])
    s.close()
    again = FindingStore(path)
    assert again.add("1@0", [(doc("a", 1), 10)]) == []
    assert again.last_seq() == 1
    again.close()
