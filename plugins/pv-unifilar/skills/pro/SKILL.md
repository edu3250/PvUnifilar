---
name: pro
description: Modo profesional de PvUnifilar. Entrega una hoja de proyecto en Excel para fijar cualquier valor (inversor o microinversor, cadenas, fusibles, calibres, troncal, tubería, datos del servicio y datos personales) o diseña a partir de una hoja ya llenada, devolviendo las celdas por corregir en rojo. Úsala cuando el usuario quiera una hoja de proyecto, fijar valores del diseño o rediseñar con su Excel.
argument-hint: "nuevo | <hoja_de_proyecto.xlsx> | <nombre del proyecto>"
allowed-tools: mcp__plugin_pv-unifilar_pvsld__new_project_sheet mcp__plugin_pv-unifilar_pvsld__design_from_project_sheet
---

# PvUnifilar: modo profesional

The project sheet (hoja de proyecto) is an Excel workbook: yellow cells are the user's input, AUTO lets the calculation decide, gray cells show the last calculation. Each project lives in its own folder of the server's output, and the tools work by project name, never by path. Talk to the user in Spanish.

The argument is what the user wrote with this command: `nuevo`, a project name, or the path of a sheet. Ask which one when there is none.

## "nuevo": a blank sheet

1. Ask for a short project name if the user gave none (letters, digits, `-` and `_`; for example `casa_lopez`).
2. Call `new_project_sheet` with that `name`.
3. Tell the user where the sheet is, that they fill in the yellow cells (AUTO where the calculation should decide; personal data is optional and goes to the drawing as written), save it in the same place, and ask for the design of that project when it is ready (in Claude Code: `/pv-unifilar:pro <nombre>`). For microinverters they choose "Microinversor" in "Tipo de inversor" (Equipos) and may fix the model, the AC combiner box, the trunk size and the spacing; the string rows then do not apply.

## A filled sheet

1. **Project name.** If the argument is a project name, use it. If it is a path to an `.xlsx` file, use the name of the folder that holds it when it is a project folder; otherwise ask for a project name.
2. **Call `design_from_project_sheet`** with `name`. If it answers that there is no `hoja_de_proyecto.xlsx` in the project folder, copy the user's file to the folder path it gives, named exactly `hoja_de_proyecto.xlsx` (create the folder if needed), and call it again.
3. **Cells to fix** (`issues`): list each one as "Hoja › Parámetro: razón", in the order given, and tell the user that `hoja_de_proyecto_revisar.xlsx` in the same folder marks them in red with the reason in a comment. They correct their own `hoja_de_proyecto.xlsx` (not the copy) and ask again.
   - `missing_components`: a model the catalogue does not have. Ask for its datasheet (PDF) and follow the `catalogo` skill (or prompt) of PvUnifilar to add it; then the sheet can be designed.
   - Never change the user's values yourself: explain what the cell needs.
4. **When it works**, tell the user as in the quick mode: `explanation_es` as it is, `bom_text` in a code block, the folder and its files, that their sheet now shows the last calculation in its gray column, and that the drawing is a draft a responsible engineer must review and sign. Look at the preview image first.

## If the pvsld tools are not available

The MCP server `pvsld` is not connected: its first start downloads its dependencies and can take longer than the program waits, or `uv` is not installed. Do not design, calculate or draw by other means, and do not write a script that does. Tell the user, in Spanish:

1. In a terminal, `uv --version` must answer. If it does not, install uv (the PvUnifilar guide says how) and close and reopen the program.
2. Reconnect the `pvsld` server (in Claude Code: `/mcp`, choose `pvsld`, reconnect). The first time, wait a minute for the download to finish and reconnect again.
3. Ask again.
