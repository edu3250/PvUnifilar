"""Microinverters through the MCP tools (Stage 3.10.6): size_pv_system and design_and_draw."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pvsld.mcp.sandbox import OutputSandbox
from pvsld.mcp.server import INSTRUCTIONS, create_server
from pvsld.resources import data_path
from test_mcp_server import call, text_of
from test_mcp_sizing import template

MODULE = "JINKO-JKM650N-66HL4M-BDV"


@pytest.fixture
def server(tmp_path: Path) -> Any:
    return create_server(
        OutputSandbox.at(tmp_path / "out"), catalogue_dir=data_path("datasheets/records")
    )


def test_micros_only_when_asked(server: Any) -> None:
    strings = call(
        server, "size_pv_system", {"template": template(), "module": MODULE, "module_count_max": 10}
    ).structured_content
    assert "branches" not in strings["selected"]
    micros = call(
        server,
        "size_pv_system",
        {
            "template": template(),
            "module": MODULE,
            "module_count_max": 10,
            "microinverters": ["GROWATT-NEO-2000M-X2"],
            "ac_combiner": True,
        },
    )
    data = micros.structured_content
    assert data["selected"]["kind"] == "microinverter"
    assert data["selected"]["ac_combiner"] is True
    assert data["spec"]["ac_combiner"]["main_ocpd"] == "ITM-1"
    assert "R1:" in text_of(micros)
    assert "microinverters" in INSTRUCTIONS


def test_design_and_draw_with_microinverters(server: Any) -> None:
    result = call(
        server,
        "design_and_draw",
        {
            "module": MODULE,
            "module_count_min": 8,
            "module_count_max": 8,
            "microinverters": "auto",
            "use_autocad": "never",
        },
    )
    data = result.structured_content
    assert data["ok"] is True, data
    assert "Microinversor" in data["bom_text"]
    assert "Microinversor" in data["explanation_es"]
