"""The DC surge protective device type of the catalogue (CLAMPER Solar, 2026-11-03)."""

import pytest
from pydantic import ValidationError

from pvsld.catalogue import ComponentRegistry, DcSpd, DcSpdVariant
from pvsld.resources import data_path
from pvsld.sizing import bos


@pytest.fixture(scope="module")
def registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def test_clamper_solar_loads_every_version(registry) -> None:
    spds = registry.dc_spds()
    assert len(spds) == 19
    spd = registry.get("CLAMPER-SOLAR-600V-18KA")
    assert isinstance(spd, DcSpd)
    assert (spd.ucpv_v, spd.in_ka, spd.imax_ka, spd.up_kv) == (600, 18, 40, 2.7)
    assert (spd.spd_type, spd.iscpv_ka, spd.product_code) == ("T2", 5, "015013")
    assert spd.certified
    assert not spd.remote_signalling
    assert not registry.get("CLAMPER-SOLAR-600V-10KA").certified
    assert registry.get("CLAMPER-SOLAR-600V-18KA-SR").remote_signalling
    assert "NOM-ANCE" in (spd.certifications or [])


def test_imax_below_in_is_rejected() -> None:
    with pytest.raises(ValidationError, match="imax_ka"):
        DcSpdVariant(component_id="X-1", ucpv_v=600, in_ka=20, imax_ka=10, up_kv=2)


@pytest.mark.parametrize(
    ("voc", "isc", "expected"),
    [
        (269, 2 * 18.17, "CLAMPER-SOLAR-300V-18KA"),
        (301, 18.17, "CLAMPER-SOLAR-600V-18KA"),
        (900, 18.17, "CLAMPER-SOLAR-1000V-18KA"),
        (1050, 18.17, "CLAMPER-SOLAR-1100V-18KA"),
        (1200, 18.17, None),
        (269, 5000, None),  # I_SCPV 5 kA < 1.25 x 5000 A
    ],
)
def test_spd_selection(registry, voc, isc, expected) -> None:
    spd = bos.spd_for(bos.spd_need(voc, isc), registry.dc_spds())
    assert (spd.component_id if spd else None) == expected


def test_spd_problem_names_the_rule(registry) -> None:
    spd = registry.get("CLAMPER-SOLAR-300V-18KA")
    assert "SPD-002" in (bos.spd_problem(spd, bos.spd_need(400, 10)) or "")
    assert "SPD-004" in (bos.spd_problem(spd, bos.spd_need(200, 5000)) or "")
    assert bos.spd_problem(spd, bos.spd_need(200, 10)) is None
