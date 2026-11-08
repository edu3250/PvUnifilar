---
name: catalogo
description: Agrega al catálogo local de PvUnifilar un equipo que no está (módulo, inversor, microinversor, fusible, interruptor, seccionador o cable) a partir de su hoja técnica en PDF, después de que el usuario confirma los valores. Úsala cuando falte un equipo en el catálogo o el usuario comparta la hoja técnica de un panel, inversor o protección.
argument-hint: "<hoja técnica.pdf>"
allowed-tools: mcp__plugin_pv-unifilar_pvsld__list_components mcp__plugin_pv-unifilar_pvsld__add_component_to_catalogue
---

# PvUnifilar: agregar un equipo al catálogo local

The bundled catalogue has the equipment the project owner reviewed. Anything else is added to the user's own local catalogue from its datasheet, and only after the user confirms the values. Never invent or guess a value. Talk to the user in Spanish.

The datasheet is the PDF the user gave with this command or in the conversation.

## Steps

1. **Is it already there?** Call `list_components` (with the manufacturer) and check that the model is really missing. If it is there, say so and stop.
2. **Read the format and the folders.** Read the MCP resource `pvsld://catalogue/record-examples` of the `pvsld` server. It gives `datasheets_folder` (where the PDF goes), `how_to`, and one example record per `component_type`.
3. **Copy the PDF** the user gave into `datasheets_folder` (create the folder if needed), keeping its file name. Ask for the file if the user has not given one.
4. **Read the datasheet** and write one record in the format of the example of its `component_type`: one family, one variant per model or power rating, every value exactly as printed, with the unit in the field name. Use the manufacturer's model names for `component_id`, in UPPERCASE with `-` (for example `TRINA-TSM-450NEG9R.28`). In `source` give only `title`, `pages`, `extraction_method` and `notes`.
   - **Microinverters** (`component_type: microinverter`): one module per input; the AC branch tables (units per trunk cable size, largest branch breaker) usually come from the installation manual. When the datasheet lacks a value, follow the owner's criteria and say each deduction in `notes`: real power = apparent power when the default power factor is at least 0.99; no largest breaker given: leave `max_ocpd_a` out; caps that change with the AC voltage: keep the 220 V column. A model the manufacturer declares not compatible with the 220 V grid in Mexico is not added.
5. **Show the values to the user** in a table per variant (and the family values above it), and say from which page each block comes. Ask them to compare with the datasheet and confirm. Fix anything they correct.
6. **After they confirm**, call `add_component_to_catalogue` with the `record`, the PDF's file name as `datasheet`, and `user_confirmed: true`. If the tool lists problems (a missing or implausible value), fix them from the datasheet; ask the user only when the datasheet does not say.
7. **Tell the user** the new `component_id`s, that the equipment is now in their local catalogue for every design and in the project-sheet lists, and continue with the design that needed it.

## If the pvsld tools are not available

The MCP server `pvsld` is not connected: its first start downloads its dependencies and can take longer than the program waits, or `uv` is not installed. Do not design, calculate or draw by other means, and do not write a script that does. Tell the user, in Spanish:

1. In a terminal, `uv --version` must answer. If it does not, install uv (the PvUnifilar guide says how) and close and reopen the program.
2. Reconnect the `pvsld` server (in Claude Code: `/mcp`, choose `pvsld`, reconnect). The first time, wait a minute for the download to finish and reconnect again.
3. Ask again.
