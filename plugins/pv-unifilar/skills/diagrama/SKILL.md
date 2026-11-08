---
name: diagrama
description: Hace el diagrama unifilar de un sistema fotovoltaico interconectado (México, CFE, NOM-001-SEDE-2012), con inversor de cadena o con microinversores, a partir de una petición sencilla como "un diagrama con 8 paneles Jinko de 475 W" o "10 paneles con microinversores"; entrega el plano DWG o DXF, el PDF, la memoria de cálculo, la hoja de proyecto y el resumen de materiales. Úsala cuando pidan un diagrama unifilar, plano eléctrico o memoria de cálculo de paneles solares o de un sistema fotovoltaico.
argument-hint: "[petición, p. ej. 8 paneles Jinko 475 W]"
allowed-tools: mcp__plugin_pv-unifilar_pvsld__list_components mcp__plugin_pv-unifilar_pvsld__design_and_draw
---

# PvUnifilar: diagrama rápido

Design one grid-tied PV installation in Mexico from the user's request and deliver every file in one step, with the tools of the `pvsld` MCP server. Talk to the user in Spanish.

The request is what the user wrote with this command, or their last message.

## Steps

1. **Find the module.** Call `list_components` with `component_type: pv_module` (add `manufacturer` when the user named one). Match the user's words (brand, model, watts) to a `component_id`.
   - One clear match: use it. Several: ask which one, listing them with their watts.
   - None: the module is not in the catalogue. Tell the user, ask for its datasheet (PDF) and follow the `catalogo` skill (or prompt) of PvUnifilar to add it, then come back here.
2. **Find the size.** Use the number of modules the user gave (`module_count_min` = `module_count_max` = that number). If they gave a power instead ("un sistema de 5 kW"), pass `target_dc_power_w`. If they gave neither, ask how many modules.
3. **Only what the user said.** Pass `inverters` only when the user named an inverter (find its id with `list_components`, `component_type: string_inverter`). Pass `routing` lengths only when given. Never invent service, site or personal data: the tool's quick defaults cover them and leave personal data blank.
   - **Microinverters only when asked.** When the user asks for microinverters ("con microinversores") or names one (Enphase, Hoymiles, APsystems, Growatt NEO...), pass `microinverters`: the named model's id (find it with `list_components`, `component_type: microinverter`), or `"auto"` when they named none. Otherwise leave it out: the design uses a string inverter. Pass `ac_combiner: true` only when the user asks for an AC combiner box (caja combinadora de CA).
   - With a 127 V service (1F-2H) only a 120 V microinverter works; the tool says so when none fits.
4. **Call `design_and_draw`** once. Give `name` only when the user named the project.
5. **When it works**, tell the user:
   - why this inverter (or microinverter): `explanation_es`, as it is; with microinverters, also the branches and any warning about free inputs on the last unit;
   - the installation summary: `bom_text` in a code block;
   - the project folder and its files (DWG or DXF and the PDF, memoria de cálculo in Excel and PDF, hoja de proyecto, resumen de materiales);
   - that they can change any value in `hoja_de_proyecto.xlsx` and ask for the professional mode to redesign with it (in Claude Code: `/pv-unifilar:pro <proyecto>`);
   - that the drawing is a draft a responsible engineer must review and sign.

   Look at the preview image before you answer; mention anything that looks wrong.
6. **When no configuration fits**, explain the main reasons in plain Spanish (the `sizing` text) and propose a change: more or fewer modules, another inverter, or another module. A microinverter design larger than the sheet (more than 8 units or 3 branches) is calculated, but its drawing is refused: say so and propose fewer modules or string inverters.
7. **When the folder already exists**, ask before calling again with `overwrite: true`, or suggest another `name`.

## If the pvsld tools are not available

The MCP server `pvsld` is not connected: its first start downloads its dependencies and can take longer than the program waits, or `uv` is not installed. Do not design, calculate or draw by other means, and do not write a script that does. Tell the user, in Spanish:

1. In a terminal, `uv --version` must answer. If it does not, install uv (the PvUnifilar guide says how) and close and reopen the program.
2. Reconnect the `pvsld` server (in Claude Code: `/mcp`, choose `pvsld`, reconnect). The first time, wait a minute for the download to finish and reconnect again.
3. Ask again.
