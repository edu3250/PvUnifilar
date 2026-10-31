"""The calculation core imports without the render, report and server dependencies (ENG-014).

An on-device runtime (Pyodide) loads only the core; each of these packages costs seconds there, and
ezdxf alone about three. The check runs in a fresh interpreter, because this test process has
already imported everything.
"""

import subprocess
import sys

CORE = (
    "pvsld.core.model",
    "pvsld.core.calc",
    "pvsld.core.rules",
    "pvsld.core.validation",
    "pvsld.catalogue",
    "pvsld.sizing",
    "pvsld.symbols.cfe",
    "pvsld.specfile",
    "pvsld.palette",
    "pvsld.core.draft",
    "pvsld.sizing.choices",
)
HEAVY = ("ezdxf", "matplotlib", "reportlab", "openpyxl", "mcp", "PIL", "anyio")


def test_core_modules_do_not_load_heavy_dependencies() -> None:
    script = (
        "import importlib, sys\n"
        f"for name in {CORE!r}:\n"
        "    importlib.import_module(name)\n"
        f"print(','.join(m for m in {HEAVY!r} if m in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True, timeout=120
    )
    assert result.stdout.strip() == ""


def test_service_keeps_the_spec_file_helpers() -> None:
    from pvsld import service, specfile

    assert service.load_spec_file is specfile.load_spec_file
    assert service.SpecFileError is specfile.SpecFileError
