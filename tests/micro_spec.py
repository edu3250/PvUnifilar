"""A microinverter design (schema 0.2.0) built from the residential example and the catalogue.

Eight Jinko 650 W bifacial modules on two Growatt NEO 2500M-X2 units, one AC branch on a 10 AWG
trunk with a 30 A breaker, 2F-3H 220 V service.
"""

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from pvsld.catalogue import ComponentRegistry
from pvsld.resources import data_path
from pvsld.sizing.adapters import to_spec_microinverter, to_spec_module

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "residential_7p7kwp.yaml"
MODULE = "JINKO-JKM650N-66HL4M-BDV"
MICRO = "GROWATT-NEO-2500M-X2"


def micro_spec_data(*, units: int = 2, trunk: str = "10 AWG", ocpd_a: float = 30) -> dict[str, Any]:
    registry = ComponentRegistry.load(data_path("datasheets/records"))
    data = deepcopy(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")))
    data["schema_version"] = "0.2.0"
    data["modules"] = [to_spec_module(registry.get(MODULE)).model_dump(mode="json")]
    data["inverters"] = [
        to_spec_microinverter(
            registry.get(MICRO), inverter_id="INV1", qty=units, ac_voltage_v=220, phases=2
        ).model_dump(mode="json")
    ]
    data["strings"] = []
    data["ac_branches"] = [
        {
            "id": "R1",
            "trunk_size": trunk,
            "spacing_m": 4.5,
            "ocpd": "ITM-R1",
            "microinverters": [
                {"id": f"MI{n}", "inverter": "INV1", "inputs": dict.fromkeys("ABCD", "MOD1")}
                for n in range(1, units + 1)
            ],
        }
    ]
    data["dc_bos"] = {"combiners": [], "disconnects": [], "spds": []}
    data["ac_bos"]["ocpds"] = [
        {
            "id": "ITM-R1",
            "role": "I1",
            "poles": 2,
            "rating_a": ocpd_a,
            "voltage_v": 240,
            "kaic_ka": 10,
            "backfed": True,
            "at": "CC-1",
        }
    ]
    data["ac_bos"]["point_of_connection"]["breaker"] = "ITM-R1"
    data["circuits"] = [
        {
            "id": "C-R1",
            "kind": "inverter_output",
            "from": "R1",
            "to": "ITM-R1",
            "conductors": {"qty": 2, "size": trunk, "material": "Cu", "insulation": "THHW-LS"},
            "egc": {"size": trunk, "type": "desnudo"},
            "raceway": {"type": "EMT", "trade_size_mm": 21},
            "length_m": 15,
            "ambient_c": 35,
        }
    ]
    return data


def combiner_spec_data(*, main_a: float = 45, spd: bool = True) -> dict[str, Any]:
    """Two branches (2 + 1 NEO 2500M-X2) in AC combiner CCA-1, main breaker ITM-1 at CC-1."""
    data = micro_spec_data(units=3)
    branch = data["ac_branches"][0]
    second = branch["microinverters"].pop()
    data["ac_branches"].append({**branch, "id": "R2", "ocpd": "ITM-R2", "microinverters": [second]})
    data["ac_combiner"] = {"id": "CCA-1", "main_ocpd": "ITM-1", "spd": "DPS-CA1" if spd else None}
    r1 = {**data["ac_bos"]["ocpds"][0], "role": "other", "backfed": False, "at": "CCA-1"}
    data["ac_bos"]["ocpds"] = [
        r1,
        {**r1, "id": "ITM-R2", "rating_a": 15},
        {**r1, "id": "ITM-1", "role": "I1", "rating_a": main_a, "backfed": True, "at": "CC-1"},
    ]
    data["ac_bos"]["spds"][0]["at"] = "CCA-1"
    data["ac_bos"]["point_of_connection"]["breaker"] = "ITM-1"
    c_r1 = data["circuits"][0]
    data["circuits"] = [
        c_r1,
        {**c_r1, "id": "C-R2", "from": "R2", "to": "ITM-R2"},
        {
            **c_r1,
            "id": "C-CCA",
            "from": "CCA-1",
            "to": "ITM-1",
            "conductors": {**c_r1["conductors"], "size": "8 AWG"},
            "egc": {"size": "8 AWG", "type": "desnudo"},
        },
    ]
    return data
