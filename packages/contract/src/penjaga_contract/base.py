"""Base class for every generated contract model.

JSON field names that are Python keywords (``from``) become ``from_`` with an alias. This
config makes the wire format the default in both directions, so no caller can emit
``"from_"`` by forgetting ``by_alias=True``:

* ``serialize_by_alias``: ``model_dump``/``model_dump_json`` emit the JSON names;
* ``validate_by_name`` + ``validate_by_alias``: build models with ``from_=`` in Python, or
  parse ``"from"`` from JSON.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    model_config = ConfigDict(
        serialize_by_alias=True,
        validate_by_name=True,
        validate_by_alias=True,
    )
