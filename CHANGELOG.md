# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Partial draft evaluation (ENG-002, part b): the string and voltage rules (VOLT-001, STR-001, STR-004, STR-007) run on a draft as soon as the site, standards, modules, inverters and strings are valid, before the AC side exists. `RuleDef.sections` declares what each rule reads, `calc.derive_strings` computes the string values alone, and the draft checks the strings' module, inverter and MPPT references itself.
- `pvsld.core.draft.evaluate_draft`: draft evaluation for the editor (ENG-002, part a). Each issue keeps `GEN-001` and adds a category (missing, incompatible, invalid structure, unsupported), its path and the affected component ids; rules that could not run are listed with the blocking sections. Missing values are never filled in.
- `pvsld.palette` and `pvsld palette [--json | --schema]`: the editor palette (ENG-001). It lists the 15 types the engine calculates, each with full symbol geometry, ports, attributes and the JSON Schema of its spec section, plus the reviewed catalogue equipment with provenance. The junction gets four anchors at the centre of each edge.

### Changed

- `import pvsld.sizing` no longer loads ezdxf: the spec-file reader moved to `pvsld.specfile` (still re-exported by `pvsld.service`). This cuts about 3 s from an on-device start (Pyodide, ENG-014).

## [0.6.1] - 2026-10-13

The draft notice is part of every result that delivers a drawing, so the user reads it whatever model relays the result; and the user guide covers Copilot CLI and Antigravity CLI.

### Added
- Every result that delivers a drawing carries the draft notice in Spanish, ready to pass on (`notice_es`, and the last line of the text): "Aviso: el plano y la memoria de cálculo son borradores. Un ingeniero responsable debe revisarlos y firmarlos antes de cualquier trámite o de la instalación." Before, the result only told the model to remind the user, and the model of one client left it out (Antigravity CLI, professional mode). `design_and_draw`, `design_from_project_sheet` and `generate_single_line_diagram` carry it; a result that delivers nothing does not. Checked with a real prompt in Antigravity CLI: the notice reaches the user word for word.
- The user guide and the README say how to install and use PvUnifilar in Copilot CLI and Antigravity CLI (Stage 3.7.4): the install commands, how to ask for each workflow without Claude Code's commands, where the projects are kept (a folder PvUnifilar in the user's home) and what was tested with which client. Gemini CLI and Codex CLI are named as not tested.

## [0.6.0] - 2026-10-13

PvUnifilar in other agent CLIs (Stage 3.7). The server is published on PyPI as `pvsld` and the plugin runs it with `uvx --from pvsld==0.6.0 pvsld-mcp`: no git clone, no build on the user's machine. One plugin folder installs in Claude Code, Copilot CLI and Antigravity CLI, and the repository is a Gemini CLI extension. Real prompts were run in Claude Code, Copilot CLI and Antigravity CLI; Gemini CLI (closed to personal accounts) and Codex CLI (paid) were not tested.

### Added
- The plugin's three workflows are also MCP prompts, `diagrama`, `pro` and `catalogo` (Stage 3.7), for the clients that connect to the server without loading the plugin's skills: Gemini CLI and VS Code list a server's prompts as commands. They are the plugin's own `SKILL.md` files, carried in the wheel (`pvsld.mcp.prompts`), so a prompt cannot differ from its skill. `PVSLD_MCP_PROMPTS=off` leaves them out; the plugin sets it where the client already shows the skills.
- The plugin folder is also an Agent Plugins 1.0 plugin (root `plugin.json` and `mcp.json` beside Claude Code's files), which Copilot CLI reads first, and the repository is a Gemini CLI extension (`gemini-extension.json` at the root, with a context file). Checked with Copilot CLI 1.0.96 and Gemini CLI 0.63.0: both install it and list the server; Copilot's own model designed an installation from a plain request. The documentation for users waits for Stage 3.7.1, because Copilot CLI gives a server 10 s to answer and `uvx` from a git tag takes longer.
- CI validates the Gemini extension with `gemini extensions validate`.
- The plugin folder also installs in Antigravity CLI, Google's successor of Gemini CLI for personal accounts (`agy plugin install <folder>`): it carries `mcp_config.json`, the file that client reads a plugin's servers from. Checked with Antigravity CLI 1.3.4: 3 skills and 1 MCP server.
- `release.yml` publishes the package on PyPI when a release tag is pushed (Stage 3.7.1), with trusted publishing: no token is stored. It checks that the tag is the package version, checks the description with `twine`, and designs an installation with the built wheel first. `docs/pypi.md` is the Spanish description PyPI shows (the repository README has relative image paths).

### Changed
- The skills follow the Agent Skills format so that other clients load them: they have `name` (Gemini CLI lists none without it), take the request from the conversation and not from Claude Code's `$ARGUMENTS`, and name Claude Code's commands only as an example. Each also forbids writing a script that draws when the server is not connected.

- The repository is now `edu3250/PvUnifilar`, the plugin's name (owner, 2026-10-12; it was `edu3250/Diagrama-unifilarAutoCAD`). The plugin's server, the package metadata, the README, the plugin guide and the user guide use the new address. GitHub redirects the old one, so the published `v0.5.0` plugin and a marketplace added under the old name keep working; checked for `uvx --from git+…`, `claude plugin marketplace add` and `marketplace update`. The Python package (`pvsld`), the plugin (`pv-unifilar`) and the marketplace (`pvsld`) keep their names.

### Fixed
- In Antigravity CLI the projects were written inside the installed plugin (`~/.gemini/config/plugins/pv-unifilar/out`), because that client starts a plugin's server in the plugin's folder and the server's default is `./out`. The portable manifests (`mcp.json`, `mcp_config.json`) now set the output folder to `~/PvUnifilar`. Found and checked with a real prompt in Antigravity CLI 1.3.4. Claude Code is not affected: it starts the server in the project.
- Copilot CLI marked the server as failed whenever it took more than about 10 s to start, which `uvx` from a git tag often does and a first start always does. The client sends `server/discover`, gives up and sends `initialize`; the MCP SDK answered the first and refused the second. The server now drops a discovery that is followed at once by the older handshake (`pvsld.mcp.handshake`), so the client connects. Checked with Copilot CLI 1.0.96 and a server started 13 s late.
- `PVSLD_OUTPUT_DIR` holding a placeholder nobody filled in (`${user_config.output_dir}`, which only Claude Code replaces) made the server write into a folder with that name. It now counts as not set.

## [0.5.0] - 2026-10-12

The Claude Code plugin **PvUnifilar** (Stage 3.6): from a plain request, or from a project sheet the user fills in, one tool call sizes, validates and draws the installation and writes the DWG or DXF, the PDF, the calculation report, the project sheet and the bill of materials. With a local catalogue for equipment added from its datasheet, the marketplace `pvsld`, and a Spanish README and user guide. Pre-release `v0.5.0rc1` on 2026-10-09.

### Added
- Spanish user guide, `docs/guia-de-usuario.md` (Stage 3.6.5): installation and first start, the quick mode and its defaults, the files of a project folder, the professional mode sheet by sheet, the catalogue and how to add equipment, with and without AutoCAD, what the calculation checks, frequent problems, updating and the scope. The README and the plugin guide link to it.
- Each skill of the plugin says what to do when the `pvsld` server is not connected (first start slower than Claude Code's 30 s wait, or `uv` missing): check `uv --version`, reconnect from `/mcp`, ask again; and not to design by other means.
- `tests/test_claude_plugin.py` also holds the README and the user guide to the release tag of the plugin's version and to installing from the default branch.
- README rewritten in Spanish (owner decision 2026-10-10: the system works with Mexican standards, so its front page speaks to Mexican installers; code and technical docs stay in English) around what a user gets, with images produced by the system itself (`docs/images/readme/`, fictitious names). It shows the A3 sheet and its parts, the calculation report in Excel, and the project sheet with a reviewed cell in red. It also covers how to install the plugin, how to run the MCP server from Claude Code, Claude Desktop or VS Code, the nine tools, the environment variables, the CLI and a diagram of the flow.
- The Spanish plugin guide gains the sheet image and the notes from the owner's install test: reopen Claude Code after installing uv; the 30 s server start-up wait.
- `examples/quick_request.yaml`: the three-line request of the quick mode.
- Claude Code plugin **PvUnifilar** (`pv-unifilar`, Stage 3.6.4b) and its marketplace `pvsld`.
  - `plugins/pv-unifilar/`:
    - `.claude-plugin/plugin.json`;
    - `.mcp.json`, which runs the server with `uvx --from git+https://github.com/edu3250/Diagrama-unifilarAutoCAD@v<version> pvsld-mcp` and takes an optional user-configured output folder;
    - a Spanish README.
  - Three skills: `/pv-unifilar:diagrama` (quick mode; a plain request also triggers it), `/pv-unifilar:pro` (the project sheet) and `/pv-unifilar:catalogo` (a datasheet into the local catalogue). Each pre-approves the pvsld tools it calls (`allowed-tools`).
  - `.claude-plugin/marketplace.json` at the repository root. Install with `/plugin install pv-unifilar --marketplace edu3250/Diagrama-unifilarAutoCAD`.
  - The package and the plugin share one version (first `0.5.0rc1`). The server answers a missing project sheet with the full folder path to copy it to.
  - CI job `claude-plugin` runs `claude plugin validate --strict` on the plugin and the marketplace. `tests/test_claude_plugin.py` checks that versions, release tag, tool names and resources agree.
- The data the code reads at run time travels inside the wheel (Stage 3.6.4a), so the plugin's server, built by `uvx` from a git tag, works without the repository.
  - `pvsld.resources.data_path` serves the catalogue records, the symbol library, the A3 sheet template and the residential example from the installed package (`force-include` under `pvsld/_data`), and falls back to the repository checkout for development installs.
  - The CLI's default catalogue is that bundled copy, not `./datasheets/records` of the current folder.
  - CI job `package` (Linux and Windows) builds the wheel, starts `pvsld-mcp` with `uvx --from .` and designs an installation with the installed wheel outside the checkout (`scripts/smoke_installed.py`).
- Local catalogue (Stage 3.6.3): equipment the bundled catalogue lacks, added from its datasheet.
  - `pvsld.catalogue.local`:
    - `local_catalogue_dir` locates the folder: `$PVSLD_LOCAL_CATALOGUE_DIR`, else `%APPDATA%\pvsld\catalogo`, or `~/.local/share/pvsld/catalogo` elsewhere. It has the layout of `datasheets/records` plus `hojas_tecnicas/` for the PDFs.
    - `load_catalogue` merges the local records with the bundled ones. A broken local record, or one that repeats a bundled id, is left out and reported (`ComponentRegistry.local_problems`), never fatal. `ComponentRegistry.is_local` tells the local components apart.
    - `add_local_record` validates a record like any bundled one and stamps its provenance: the PDF's name and SHA-256, the user as reviewer and today's dates. It refuses an id already in the catalogue; a local record can be replaced with overwrite.
  - MCP:
    - tool `add_component_to_catalogue` (record, datasheet file name in the local `hojas_tecnicas`, `user_confirmed`);
    - resource `pvsld://catalogue/record-examples`: one reviewed record per type, the local folders, how to add;
    - `list_components` marks local components and filters every type (AC breakers, PV switches, cables too);
    - the instructions tell Claude to offer the datasheet route whenever equipment is missing.
  - Every CLI command that sizes or designs (`size`, `design`, `pro`, `report`) and `catalogue list/show` see the local records. `pvsld catalogue add RECORD.yaml --datasheet PDF` adds one.
  - `registry.validate_family` validates a record given as a mapping.
- Professional mode (Stage 3.6.2b): design from the project sheet the user fills in.
  - `pvsld.design.pro`:
    - `read_project_sheet` reads the parameter rows by label, so moved or inserted rows still work;
    - `sheet_request` checks every cell and maps it to a sizing request that keeps the fixed values (inverter, strings, modules per string, fuse without breaker fallback, DC breaker, switch, ITM-1, ITM-P, cable, conductor sizes, EMT, lengths, voltage-drop limits, SPDs, bus, grounding), the service and the personal data;
    - `design_from_sheet` runs the one-step design.
  - Personal data is free text: the CP, RPU, cédula, e-mail and coordinates are taken as written, in the sheet and in the spec (owner decision 2026-10-09: its format is the user's business; the spec drops their patterns and the Mexico bounds of lat/lon, and a coordinate may be text).
  - A cell that cannot be used (a voltage CFE does not supply, 3 × 3 strings for 8 modules, a 3F-4H service) or a fixed value the calculation rejects (a fuse below 1.56 × Isc, a conductor too small) becomes an issue on its cell. When the equipment is complete, both kinds are reported in one round.
  - `write_reviewed_sheet` returns the user's own workbook with those cells in red, the reason as a comment and a summary on Instrucciones. A model missing from the catalogue is reported as a missing component (for Stage 3.6.3).
  - The project sheet among the deliverables is the user's workbook with its "Último cálculo" column refreshed (`design_and_draw(project_sheet=...)`).
  - MCP tools:
    - `new_project_sheet` writes a blank sheet into `<output>/<name>/`;
    - `design_from_project_sheet` designs from `<output>/<name>/hoja_de_proyecto.xlsx`, or writes `hoja_de_proyecto_revisar.xlsx` and lists the cells.
    - The server instructions describe both modes.
  - CLI: `pvsld pro new DIR`, `pvsld pro design SHEET -o DIR [--no-autocad]`.
- Values the user fixes are kept and checked, never replaced (Stage 3.6.2a, for the professional mode). New sizing request fields:
  - `n_strings` and `n_series` pin the string layout; a string count above the inverter's inputs is rejected (REQ-002);
  - `dc_conductor_size` and `ac_conductor_size` are checked for ampacity and protection (CON-002 names the smallest safe size) and for voltage drop (warning);
  - `main_breaker` takes the catalogue device of ITM-P, separate from `ac_breakers`, which restrict ITM-1 only;
  - `dc_fuse_fallback: false` rejects a configuration whose listed fuses do not fit instead of switching to a DC breaker.
- Personal data reaches the drawing and the calculation report when the spec gives it (Stage 3.6.2a, owner plan 2026-10-09: "si los llena, aparecen en el plano y la memoria; si no, quedan en blanco"):
  - the owner, address, coordinates, RPU, service and meter numbers, the responsible engineer and the reviewer are optional in the spec, and missing ones stay blank lines;
  - on the A3 sheet a given value is cut with "…" to the length of the blank line it replaces;
  - the responsible's company replaces "COMPAÑÍA INSTALADORA";
  - the quick template no longer carries "SIN DATOS" markers.
- One-step design for the plugin's quick mode (Stage 3.6.1b): `pvsld.design.design_and_draw`, the MCP tool `design_and_draw` and `pvsld design REQUEST -o DIR [--no-autocad]`. From a module and a size it sizes, validates and draws the installation, finishes it with the local AutoCAD (DWG and PDF) or renders the PDF itself, and writes the calculation report (xlsx, pdf), the project sheet, the bill of materials and the spec into one folder per project (`<output>/<name>/`, default `sfv_<kWp>kWp`). Without a template the quick defaults apply (`quick_template.yaml`: CFE BT 2F-3H 220/127 V, 10 kA, 100 A main breaker, a DC SPD in the box, default temperatures, personal fields blank on the sheet). A failed AutoCAD run falls back to the pvsld PDF and says so
- `render_pdf` / `write_pdf`: the A3 sheet as a one-page vector PDF, black on white like the AutoCAD plot with `monochrome.ctb`, byte-identical for the same drawing (for users without AutoCAD)
- `detect_autocad` / `AutocadInfo`: whether Core Console is installed and which AutoCAD release holds it
- `OutputSandbox.project_dir`: a validated project folder inside the output root
- `pvsld.report` (Stage 3.6.1a, the owner approved the prototypes on 2026-10-09):
  - the calculation report (memoria de cálculo) as an Excel workbook (Datos with sourced blue inputs, Memoria with live formulas and CUMPLE/NO CUMPLE checks with NOM references, Materiales, Validación) and as a letter-size PDF;
  - the professional-mode project sheet (hoja de proyecto) with AUTO defaults, catalogue dropdowns on a hidden sheet, the last calculation's choices and notes;
  - the bill of materials as rows and as the plain-text summary for the user.
  `pvsld report SPEC -o DIR [--explanation TEXT]` writes all four. String protections now record their catalogue reference (`model`). New runtime dependencies: openpyxl and reportlab
- Sizing ranks the inverters by a DC/AC ratio inside 1.10-1.25, or the closest to it, then less clipping, fewer warnings and the smaller inverter (owner decision 2026-10-09; before, a fixed module count fell to a cold-voltage tie-break that picked an oversized inverter). Each candidate carries `explanation_es`, a short Spanish reason for the choice that the report and the MCP summary show to the user
- Catalogue switchgear (Stage 3.4b, owner choices 2026-10-09): `ac_breaker` (`AcBreaker`, Square D QO plug-on 2-pole 120/240 Vac, 10-200 A, 10 kA) and `dc_switch` (`DcSwitch`, Suntree SISO-40 PV switch-disconnector, 4 poles, DC-PV2, rated current by wiring and voltage), both pending the owner's review. `pvsld size` takes ITM-1 (the next QO rating up with enough interrupting rating, NOM 110-9), ITM-P (the service main keeps its rating; the catalogue gives the device and its kAIC) and the box disconnect DCD-CD1 (two poles per string, four in series for one string, at the step above Voc(T_min), at least the string's maximum current) from the catalogue; the spec records them in `model`, `MainBreaker.kaic_ka` shows in the protection schedule, and with no fitting device the previous assumptions stay, with a warning. Request fields `ac_breakers` and `dc_switches`. OCP-005 (the inverter's maximum output OCPD) and EGC sizing per Table 250-122 are dropped (owner decision)
- EMT conduit fill (Stage 3.4b, owner choices 2026-10-09: EMT; PV cable on the DC side, THHW-LS on the AC side). `pvsld size` chooses the smallest EMT of NOM Chapter 10 Table 4 within the Table 1 fill for the string raceway (both conductors of every string) and the inverter output (phases and neutral), each with one bare grounding conductor (Table 8, 250-122(c)); PV cable areas come from the catalogue datasheet diameter (Table 1 note 5), THHW-LS from Table 5 (THHW row; the table's area column has misprints, so areas are computed from its diameters). A given `routing.dc_raceway_trade_size_mm` / `ac_raceway_trade_size_mm` is checked instead. The spec carries `conductors.outer_diameter_mm`; the sheet shows "EMT 27 mm (1\")"
- Rule CON-006: an EMT raceway filled beyond NOM Chapter 10 Table 1 is an error that names the EMT that fits; a conductor without a known diameter is a warning
- Cable records in the catalogue (`component_type: conductor`, `Cable`, `registry.cables()`): Viakon PV Wire XLPE 2000 V, 14 AWG to 4/0 AWG (pending the owner's review); request field `dc_cable`
- gPV fuses in the catalogue (`component_type: dc_fuse`, `DcFuse`, `registry.dc_fuses()`): Littelfuse SPF 10x38 mm 1000 V dc 1-30 A (20 kA to 20 A, 50 kA at 25-30 A) and Eaton Bussmann PV 10x38 mm 1000 Vdc 1-20 A (50 kA); both records await the owner's review. Extraction method `pdf_text_layer`
- The DC protection box protects each string with a gPV fuse-disconnector by default (owner decision 2026-10-08): `pvsld size` / `size_pv_system` take `dc_ocpd_device: fuse | breaker` (default fuse) and `dc_fuses`; the fuse window is >= 1.56 x Isc and <= the module's maximum series fuse, rated voltage >= Voc(T_min), interrupting rating above the fault current; with no fitting fuse the box keeps a DC breaker and the reason says so. String fuse-disconnectors are `FUS-S<n>` ("Fusible gPV (cadena)" in the schedule); `DcOcpdChoice.breaker_id` is now `device_id` with `device`
- The sheet draws the box with the library 0.8.0 blocks, both variants kept (fuse-disconnector or breaker): field connector `PVSLD_PV_CONNECTOR` at the end of each string (the string circuit and its marker are the run from it to the box), box terminals `PVSLD_TERMINAL` on the DC layer, `PVSLD_SPD_DC_BOX` below the strings fed by both (junction dots `PVSLD_JUNCTION` where the taps leave the strings; the upper tap crosses the lower string), one ganged `PVSLD_DC_DISCONNECT_<n>S`; the box grows to hold them
- Symbol library 0.8.0, for the DC protection box of the owner's reference (2026-10-08): `PVSLD_DC_DISCONNECT_2S`, one ganged DC switch-disconnector for every string of the box (a pole pair per string 20 mm apart, the handle on the top pole, a dotted mechanical link; parametric `PVSLD_DC_DISCONNECT_<n>S` for 1 to 12 strings, drawn at the box size); `PVSLD_FUSE_DISC_DC`, the DC fuse-disconnector (gPV fuse holder); `PVSLD_SPD_DC_BOX`, the DC SPD module with two line inputs and the earth lead; `PVSLD_PV_CONNECTOR`, the plug-and-socket (MC4 type) field connector. `PVSLD_TERMINAL` is coloured BYBLOCK, so it takes the colour of the layer it is inserted on (`Circle.color_by_block`)
- DC protection box on the sheet template (owner decision 2026-10-08): each string runs through its DC breaker (`PVSLD_CB_DC`) and the DC disconnect to the inverter; the DC SPD hangs after the lower breaker (before the disconnect when there is no breaker) and goes to earth; a dashed enclosure "CAJA DE PROTECCIONES CD" of the load centre's size (30 x 35 mm) holds the breakers and the SPD, drawn at 60 % and 50 % with their texts (`SymbolInstance.scale`, read back with the INSERT scale). `pvsld size` (and the MCP tool) now selects a DC breaker per string by default (`dc_ocpd: always`); the protection schedule names them "ITM de CD (cadena)" in "Caja CD"
- DC protection box after the owner's reference box (2026-10-08): both strings, after their breakers, feed the DC SPD to earth, then the box disconnect `DCD-CD1` (two poles per string, one tag, a dashed mechanical link between the pole pairs) opens them ahead of the inverter, inside the box; junction dots mark where the SPD taps leave the strings, so the SPD's earth line visibly crosses the lower string. The box stays 35 mm tall and grows in width to hold the disconnect. `pvsld size` adds `DCD-CD1` whenever it selects string breakers (assumption noted: ratings of the string breaker; the catalogue has no switch-disconnectors yet). `CircleItem.filled` draws a junction dot as a DONUT.
- Symbol library 0.7.0: `PVSLD_CB_DC` (the CFE breaker with DC ports); the inverter is 30 x 30 mm and the load centre 30 x 35 mm (owner request: smaller); the DC disconnect shows its tag above and its description below

### Changed
- The README and the plugin guide install the plugin from the default branch (`/plugin marketplace add edu3250/Diagrama-unifilarAutoCAD`, then `/plugin install pv-unifilar@pvsld`), without `@develop`: `develop` is the repository's default branch since 2026-10-10. They also name Git as a requirement and `/plugin configure pv-unifilar@pvsld` for the output folder.
- `list_components` and `ComponentRegistry.find` match the manufacturer by a part of its name ("Jinko" finds "Jinko Solar").
- The bill of materials names every circuit of a shared conduit ("EMT 27 mm para C-S1 y C-S2").

### Fixed
- (3.6.5 real-prompt tests) AutoCAD could not finish a design whose project folder had a long path (Windows error 206) or a character outside ASCII, and every design it did finish left a `.pvsld-staging-…` folder in the user's output folder, kept open by Autodesk's background services. Core Console now works on a copy of the DXF in a temporary folder; the DWG and the PDF are moved to the project folder, and work folders that could not be removed are swept by a later run.
- (3.6.5 real-prompt tests) A fuse or DC breaker fixed in the project sheet that does not fit was reported as "Ningún fusible gPV del catálogo cumple", which sent the user after a device the catalogue has. The cell now says "El fusible elegido no cumple (…) Elija otro de la lista o deje AUTO."
- (3.6.5 real-prompt tests) The project sheet's instructions and the missing-component message named the commands of the first plan (`/unifilar-pro`, `/unifilar-catalogo`) instead of `/pv-unifilar:pro` and `/pv-unifilar:catalogo`.
- On the A3 sheet, a protection schedule of eight rows (two string fuses) reached the last line of the grounding electrode's label; the earthing bus now rises just enough to clear it
- With the DC protection box, the protection schedule places the DC SPD in "Caja CD" (it gave the spec location, e.g. INV1.dc)
- The box disconnect DCD-CD1 records the switch's 4 poles when a single string runs through all of them in series
- Drawings on the owner's sheet template are editable in one place (owner request): the frame, template boxes, fixed texts, fields, symbology, schematic and protection schedule are all in model space at 1:1, and the file opens on the Model tab. The `A3` layout keeps only the 1:1 viewport, for plotting (`Diagram.sheet_in_model`). Every text, line and block can be edited directly; blocks keep their attributes
- Drawings and the symbol library open on their sheet (`A3`, `Legend`) in AutoCAD: `$TILEMODE` is 0. With ezdxf's default 1 they opened on the Model tab, where the sheet template, frame and title block (paper space) are not visible. The golden DXF, the library DXF and its DWG were regenerated

### Added
- Phase 3, Stage 3.5: MCP tools `list_components` and `get_component` (reviewed catalogue, with datasheet provenance) and `size_pv_system` (the sizing engine: the selected design as a complete spec, ranked alternatives and rejections grouped by rule; about 3k tokens per result); resource `pvsld://examples/sizing-template`; `$PVSLD_CATALOGUE_DIR` (default `datasheets/records`). The server instructions now start from the catalogue

## [0.4.0-symbols] - 2026-10-08

Symbol library, the generator drawing with it, and the owner's sheet template. Also ships the first part of Phase 3 (component catalogue and the sizing engine v1).

### Added
- Symbol library (Phase 4, ADR-0005): 57 blocks `PVSLD_<FUNCTION>` (library 0.6.0) redrawn as vector geometry, each with its ports (DC, AC, PE, SIG, ANY) in XDATA, its attributes and the standard it comes from (hidden `SOURCE_STANDARD` and the *Fuente* column of the legend). `symbols/pvsld-symbols.dxf` (R2018, byte-reproducible) holds them with three `Legend` layouts grouped by family; `symbols/pvsld-symbols.dwg` is the same library as a TrustedDWG 2018 (AUDIT 0/0). `pvsld symbols build|list|validate|dwg`, `scripts/build_symbol_library.py`
- Parametric blocks defined on demand: the combiner box for 1 to 24 strings (`PVSLD_COMBINER_<n>S`) and a PV string with every module drawn (`PVSLD_PV_STRING_<n>M_UP|DN`, up to 30 modules); AC fuse `PVSLD_FUSE_AC`
- Layout template `a3_plantilla_v1`: the schematic on the owner's A3 sheet. `pvsld sheet import TEMPLATE.dwg|dxf` writes the neutral template `sheet_templates/a3_plantilla_v1.dxf` (published); the layout fills the heading, capacity, service, calculation summary, notes, circuit boxes, module and inverter data, symbology of the blocks drawn, protection schedule, drawing number and date. Personal data stays blank and the title block reads "COMPAÑÍA INSTALADORA". `pvsld size` produces designs on this sheet by default
- Component catalogue (Phase 3, ADR-0004): reviewed datasheet records under `datasheets/records/`, `pvsld catalogue validate|list|show`, review gate in CI
- Sizing engine v1 (Phase 3, Stage 3.4): `pvsld size REQUEST.yaml` enumerates string configurations per catalogue inverter, rejects those that break a rule, ranks the rest and writes a full specification; rule STR-007 and the DC/AC ratio policy (Stage 3.3)
- Diagram model: text styles and centred text, circles, scaled symbol samples; tables with a cell style and a centred title

### Changed
- The generator imports the blocks a diagram uses from the library DXF (port XDATA included) instead of defining symbols in code; instances carry `SOURCE_STANDARD`, the inverter has no `CERT` attribute (certifications are out of scope); the symbology table cites each symbol's source; the golden DXF was regenerated
- The thermomagnetic breaker of the diagrams is `PVSLD_CB` (owner decision); `PVSLD_CB_IEC` stays as an alternative
- `pvsld.core.rules`: `Severity` lives in `pvsld.core.severity`; `calc.max_ocpd_for_ampacity_a` is shared by CON-003 and the conductor sizing

### Removed
- `pvsld.symbols.catalogue` (the Phase 2 code-defined symbols) and `pvsld.backends.dxf.render_symbol_library`

## [0.2.0-spike] - 2026-10-06

Phase 2 (MCP Server Prototype & Connectivity Spike) complete; ADR-0001 confirmed.

### Added
- Phase 2, Stage 2.5: `docs/spikes/phase-2-review.md`, the spike review. It holds the per-stage results against the criteria, the ADR-0001 exit-gate evaluation (passed), the status of reversal triggers T1–T9 (none fired), the findings, the open items and the proposed (tentative) scope for Phases 3–5
- Phase 2, Stage 2.4 (S4, PR #6): Core Console finisher `pvsld.finishers.core_console` and `pvsld finish`: DXF → DWG 2018 (TrustedDWG) plus an AutoCAD-plotted A3 PDF through `accoreconsole.exe /isolate`, in 4.2–10.8 s per sheet. A run is ok only on several independent signals (exit code, progress markers, AUDIT summary, output formats), so failures are never silent. It also adds a CI fake runner, live `autocad` tests and the `scripts/s4_measure.py` harness; report `docs/spikes/s4-core-console.md`
- Phase 2, Stage 2.3 (S3, PR #5): stdio MCP server `pvsld-mcp` (`mcp` 2.3) with `validate_pv_design` and `generate_single_line_diagram`, plus the resources `pvsld://schema/pv-system-spec`, `pvsld://rulepack/mx-gd-2026.10` and `pvsld://symbols`. It writes into an output sandbox and returns a size-bounded PNG preview (`PVSLD_PREVIEW_MAX_BYTES`). The repo `.mcp.json` uses `${PVSLD_MCP_COMMAND:-pvsld-mcp}`. Pillow is a declared dependency, and specs over 256 KiB are refused. Report `docs/spikes/s3-mcp-round-trip.md`
- Phase 2, Stage 2.1 (S1, PR #3): deterministic core (`pvsld.core`: Pydantic parameter model, calculations, rule-pack subset VOLT-001, STR-001, STR-004, CON-003, PCC-002, MET-001, DIS-003, DIS-004), code-defined symbol catalogue (`pvsld.symbols`), backend-neutral diagram model and ezdxf DXF R2018 backend with PNG preview; `pvsld validate` and `pvsld generate`; byte-identical golden DXF on Windows and Linux
- Phase 2, Stage 2.2 (S2, PR #2): AutoCAD 2027 .NET 10 plug-in (bridge, Core-safe render assembly, desktop host) on a current-user named pipe with per-start secret; Python pipe client, fake plug-in and COM baseline; CI job building the plug-in and running its bridge tests
- `docs/decisions/ADR-0003-symbol-library-format.md`: symbol library format and `PVSLD_*` naming (accepted 2026-10-06)
- Phase 2, Stage 2.0 (Foundation): Python project scaffold for the `pvsld` package
  - `pyproject.toml`: hatchling build, `src/` layout, version `0.2.0.dev0`, Python >= 3.11, MIT; runtime dependencies `ezdxf`, `mcp` 2.x, `pydantic` 2.x and `PyYAML`; extras `dev` (pytest, pytest-cov, ruff) and `autocad` (`pywin32`, Windows only); console script `pvsld`
  - `src/pvsld/`: `__version__` read from package metadata, `py.typed`, and empty `core/`, `backends/`, `transports/` and `finishers/` subpackages whose docstrings state their role in ADR-0001
  - `pvsld --version` (the Stage 2.0 `validate-example` smoke check was replaced by `pvsld validate` in Stage 2.1)
  - `examples/residential_7p7kwp.yaml`: the 7.70 kWp / 6 kW @ 220 V worked example from the vault's PV SLD Parameter Model, marked as an illustrative sample
  - `tests/`: package, CLI, `pyproject.toml` and example tests, and a `--run-autocad` option that skips `autocad`-marked tests (with a reason) unless it is given on Windows
  - Ruff lint and format configuration; pytest with strict markers; coverage floor of 60 % on `pvsld`
  - `.github/workflows/ci.yml`: ruff and pytest on Ubuntu and Windows with Python 3.11 and 3.12 (`contents: read` permissions)
  - "Development setup" sections in `README.md` and `CONTRIBUTING.md` (virtual environment, ruff, pytest, AutoCAD tests)

### Fixed
- Backend (PR #7): the overall paper-space viewport is on layer `0` and the layout names the canonical `ISO_full_bleed_A3_(420.00_x_297.00_MM)` paper. AutoCAD 2027 AUDIT of the S1 DXF goes from 2 errors to 0 errors, 0 fixed (Core Console). The read-back counts that viewport apart from drawing content; the golden DXF is regenerated, and ADR-0003 states the layer-0 exception

### Changed
- `docs/decisions/ADR-0001-claude-autocad-integration.md`: "Phase 2 validation (2026-10-06)" section appended. The ADR is confirmed by spikes S1–S4 and its status is unchanged
- `IMPLEMENTATION_PLAN.md`: Stages 2.3 and 2.4 Complete; Stage 2.5 Complete except the `v0.2.0-spike` tag; Phase 2 Complete pending release; tentative scopes for Phases 3–5 refined from the review
- `README.md`: status (Phase 2 complete, pending `v0.2.0-spike`), roadmap, and quick-start notes for the MCP server and `PVSLD_MCP_COMMAND`
- `docs/README.md`: lists `docs/spikes/` and ADR-0003; update notes on the S2, S3 and S4 reports point to the review
- `IMPLEMENTATION_PLAN.md`: Stage 1.7 records the public GitHub repository and the `v0.1.0-docs` GitHub release
- `IMPLEMENTATION_PLAN.md`: Stages 2.0–2.2 Complete; the `.mcp.json` placeholder is replaced by creating `.mcp.json` in Stage 2.3 together with a working server
- `.gitignore`: ignore `.venv/` and `.ruff_cache/`
- `CONTRIBUTING.md`: the Python checks are now ruff and pytest (previously black and flake8)

## [0.1.0-docs] - 2026-10-05

Phase 1 (Documentation & Regulatory Research) complete.

### Added
- Initial repository scaffold with GitFlow structure (main, develop, feature/* branches)
- `README.md` with project vision, current status, and roadmap
- `CONTRIBUTING.md` with detailed GitFlow workflow and Conventional Commits guidelines
- `LICENSE` (MIT, Copyright 2026 edu3250)
- `.gitignore` covering Python, Node.js, AutoCAD, IDE, and OS temporary files
- `CHANGELOG.md` (this file) following Keep a Changelog format
- `IMPLEMENTATION_PLAN.md` with seven detailed stages for Phase 1 (Documentation & Research)
- `docs/README.md` explaining technical documentation structure
- Obsidian research vault scaffold (author's local vault, not part of this repository) with research domains
- Phase 1 research corpus in the Obsidian vault (Stages 1.2, 1.3, 1.5, 1.6): 252 research notes (129 sources, 78 entities, 37 concepts, 8 comparisons). It covers:
  - Autodesk APIs and MCP servers;
  - MCP and Claude integration;
  - a survey of community AutoCAD MCP servers;
  - programmatic DWG/DXF generation, including a hands-on ezdxf probe;
  - Mexican regulation, including the machine-checkable SLD checklist MX-A01…MX-I07;
  - PV single-line-diagram engineering: parameter model v0.1.0 and validation rules.
- `docs/decisions/ADR-0001-claude-autocad-integration.md`: ADR-0001 accepted (Stage 1.4). It adopts a layered hybrid: a Python stdio MCP server, a deterministic core (parameter model → calculations → `mx-gd-2026.10` rule pack → diagram model), an ezdxf DXF backend first and an AutoCAD 2027 .NET 10 plug-in backend second. Vault original: `wiki/decisions/ADR-0001 Integration Approach.md`
- `docs/research/phase-1-summary.md`: condensed English summary of the Phase 1 findings, open questions and a map of the vault
- `.gitattributes`: LF normalization for text; DXF/DWG/PDF/PNG kept byte-exact for golden-file tests
- Stage 1.7 vault lint (`Lint Report 2026-10-05` in the vault): 0 dead links, 0 orphans, 0 frontmatter failures; 2 errors and 10 warnings fixed (8 uncovered MX checklist items received rules, rule pack now 98 rules; pre-ADR notes aligned with ADR-0001)

### Changed
- `IMPLEMENTATION_PLAN.md`: Stages 1.1–1.7 marked Complete. Phase 2 is refined into tentative stages 2.1–2.5 (spikes S1–S4 from ADR-0001 plus a review), still to be confirmed by the owner
- `README.md`: status, roadmap and documentation links updated
- `docs/README.md`: directory tree lists `docs/research/`
- Checklist coverage figures corrected after the vault lint: 77 MX items, 71 machine-checkable (92 %)

---

## Version Numbering Convention

- **`v0.1.0-docs`** — Phase 1 complete; documentation and research published
- **`v0.2.0-spike`** — Phase 2 complete; MCP connectivity prototype validated
- **`v0.3.0-engine`** — Phase 3 complete; parametric PV sizing engine
- **`v1.0.0`** — Phase 5 complete; full product release with Mexican regulatory compliance

---

**Repository:** https://github.com/edu3250/PvUnifilar  
**Maintainer:** edu3250 (edu3250@gmail.com)  
**Last updated:** 2026-10-13
