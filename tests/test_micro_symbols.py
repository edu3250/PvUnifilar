"""Generated microinverter and AC junction box symbols (Stage 3.9.4)."""

import ezdxf
import pytest

from pvsld.symbols.cfe import SYMBOLS, get_symbol
from pvsld.symbols.cfe.definitions import MAX_MICRO_INPUTS, microinverter, microinverter_name
from pvsld.symbols.cfe.loader import ensure_symbol


@pytest.mark.parametrize("n_inputs", [1, 2, 4, MAX_MICRO_INPUTS])
def test_one_input_per_module_on_top_and_ac_below(n_inputs: int) -> None:
    spec = get_symbol(microinverter_name(n_inputs))
    inputs = [p for p in spec.ports if p.kind == "DC"]
    assert [p.id for p in inputs] == list("ABCDEFGH"[:n_inputs])
    assert {(p.y, p.direction) for p in inputs} == {(7.5, "up")}
    xs = [p.x for p in inputs]
    assert xs == sorted(xs)
    (ac,) = [p for p in spec.ports if p.kind == "AC"]
    assert (ac.id, ac.y, ac.direction) == ("AC", -7.5, "down")
    assert spec.source.startswith("pvsld (composición)")
    assert spec.name not in SYMBOLS  # generated on demand, the legend keeps PVSLD_MICROINV


@pytest.mark.parametrize("n_inputs", [0, MAX_MICRO_INPUTS + 1])
def test_input_count_out_of_range(n_inputs: int) -> None:
    with pytest.raises(ValueError, match="inputs"):
        microinverter(n_inputs)


def test_the_junction_box_passes_the_trunk_through() -> None:
    spec = get_symbol("PVSLD_AC_JBOX")
    assert [(p.id, p.direction) for p in spec.ports] == [("IN", "left"), ("OUT", "right")]
    assert "PVSLD_AC_JBOX" not in SYMBOLS


def test_the_generator_defines_them_in_a_drawing() -> None:
    doc = ezdxf.new("R2018")
    for name in ("PVSLD_MICROINV_4IN", "PVSLD_AC_JBOX"):
        assert ensure_symbol(doc, name) == name
        assert name in doc.blocks
