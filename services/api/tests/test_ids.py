from __future__ import annotations

import pytest

from penjaga_api.ids import UnknownIdError, validate_ids
from penjaga_api.testing import load_findings
from penjaga_contract.validate import load_context


def test_contract_ids_pass_in_order_without_duplicates() -> None:
    index = load_context().index()
    ids = ["circ_sockets_bed4", "mcb_sockets_bed4", "circ_sockets_bed4", "meter_water_main"]
    assert validate_ids(index, ids) == ("circ_sockets_bed4", "mcb_sockets_bed4", "meter_water_main")


def test_unknown_ids_are_rejected_and_named() -> None:
    index = load_context().index()
    # 3D-only names (house.spec.json) are not contract IDs until the team reconciles the two
    with pytest.raises(UnknownIdError) as err:
        validate_ids(index, ["fix_bath1_wc_cistern", "meter_water_main", "app_fan_living"])
    assert err.value.unknown == ["fix_bath1_wc_cistern", "app_fan_living"]


def test_every_id_in_the_captured_findings_is_a_contract_id() -> None:
    index = load_context().index()
    for name in ("l1_leak", "l1_burst", "l2_elderly", "l2_l5_wet", "l3", "l5_water"):
        for doc in load_findings(name):
            ids = doc["object_ids"] + [c["object_id"] for c in doc["candidates"]]
            validate_ids(index, ids)
