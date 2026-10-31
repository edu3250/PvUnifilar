# Implementation Plan

All work is tracked in phases and stages. Each stage has specific deliverables, success criteria, verification tests, and a status tracker.

Update status after each merge. Completed phases are condensed into `CHANGELOG.md`; delete this file only when no planned phases remain in it.

---

## Phase 1: Documentation & Regulatory Research

**Goal:** Exhaustive research into AutoCAD connectivity patterns, MCP integration, Mexican electrical regulations, and PV single-line diagram standards. All knowledge captured in the Obsidian vault.

**Target Release:** `v0.1.0-docs`

---

### Stage 1.1: Vault Scaffold & Conventions

**Goal:** Establish the Obsidian vault structure, templates, and frontmatter conventions to standardize all research documentation.

**Success Criteria:**
- Vault directory tree created in the author's local Obsidian vault
- All template files in `_templates/` ready for researchers
- Five research domains initialized with stub pages
- Frontmatter conventions documented in vault `CLAUDE.md`
- First research entry logged in `wiki/log.md`

**Tests:**
- All directories exist and contain `.gitkeep` or initial files
- `wiki/index.md` renders without broken links
- All domain pages include required frontmatter fields
- `_templates/*.md` contain valid YAML frontmatter
- `.manifest.json` in `.raw/` is valid JSON

**Status:** Complete

---

### Stage 1.2: Official Autodesk APIs & MCP/Claude Documentation Research

**Goal:** Deep dive into official Autodesk AutoCAD APIs, Model Context Protocol (MCP) specifications, and Claude integration documentation.

**Success Criteria:**
- Comprehensive research of Autodesk's official AutoCAD API documentation (AutoCAD .NET SDK, COM, AutoLISP, RealDWG)
- MCP protocol specification fully documented with use cases
- Claude API integration patterns researched (tool use, agent mode, function calling)
- All sources saved to vault with citation metadata
- Decision factors identified for integration approach (latency, complexity, licensing)

**Tests:**
- Vault lint: no dead links in sourced materials
- All sources cited with `source_type`, `url`, `confidence`, and `key_claims`
- Entity pages created for Autodesk, Anthropic, AutoCAD products
- Concept pages for MCP, Claude tools, API patterns
- Coverage checklist: AutoCAD API documentation thoroughly ingested

**Status:** Complete

---

### Stage 1.3: Community GitHub AutoCAD↔LLM/MCP Implementations Survey

**Goal:** Systematic survey of existing open-source projects attempting to integrate AutoCAD with LLMs, Claude, or MCP.

**Success Criteria:**
- GitHub search across keywords: "AutoCAD API", "AutoCAD Claude", "AutoCAD LLM", "AutoCAD MCP", "DXF generation", "AutoLISP AI"
- Each relevant repository documented in vault with:
  - Project purpose and maturity level
  - Integration approach (API, COM, file-based, headless)
  - Source code language and dependencies
  - Licensing and viability for reference/reuse
  - Lessons learned and limitations
- Comparison matrix created (Stage 1.4)

**Tests:**
- Vault lint: all repository entities linked correctly
- Each repository has a dedicated entity page with contact info and known issues
- Source citations include GitHub URLs and commit hashes (reproducible)
- Coverage checklist: at least 15–20 relevant projects surveyed

**Status:** Complete

---

### Stage 1.4: Integration Approach Comparison & ADR-0001

**Goal:** Synthesize research from Stages 1.2 and 1.3 into an Architecture Decision Record (ADR) recommending the optimal integration approach.

**Success Criteria:**
- ADR-0001 document created at `docs/decisions/ADR-0001-claude-autocad-integration.md`
- Comparison of integration options:
  - AutoCAD .NET with MCP bridge
  - AutoLISP + external Claude API calls
  - Design Automation API + MCP
  - ODA (Open Design Alliance) file generation
  - Pure DXF/DWG file generation (ezdxf, libredwg, etc.)
- Decision rationale: cost, latency, complexity, compliance, maintainability
- Accepted approach clearly stated with trade-offs documented
- ADR written in the vault as `wiki/decisions/ADR-0001 Integration Approach` (original) and mirrored to `docs/decisions/`

**Tests:**
- ADR document is well-formed markdown
- All options compared with pros, cons and an explicit verdict
- Decision is defensible from user's architectural constraints
- References to Stage 1.2 and 1.3 research intact

> ADR-0001 accepted 2026-10-05: layered hybrid, ezdxf DXF backend first, AutoCAD 2027 .NET 10 plug-in backend second; see `docs/decisions/ADR-0001-claude-autocad-integration.md`.

**Status:** Complete

---

### Stage 1.5: Mexican Electrical Regulatory Research

**Goal:** Comprehensive research into Mexican electrical standards for distributed PV generation and interconnection.

**Success Criteria:**
- NOM-001-SEDE (Electrical installations) coverage:
  - Single-line diagram requirements
  - Labeling and symbology standards
  - Protection device specifications
- CRE (Comisión Reguladora de Energía, replaced by the CNE in 2025) interconnection rules researched
- CNE (Comisión Nacional de Energía) and CFE (Comisión Federal de Electricidad) guidelines documented
- UVIE (Unidad de Verificación de Instalaciones Eléctricas) inspection checklist understood
- Local utility requirements (e.g., CFE plant interconnection rules)
- Compliance checklist created for diagram validation (Stage 1.6)

**Tests:**
- Vault lint: all regulation entities have titles, sources, and key requirements
- Each standard page contains relevant excerpts and official references
- Concept pages for "distributed generation", "interconnection", "protection zones"
- Coverage checklist: all major Mexican regulations and their diagram implications documented

**Status:** Complete

---

### Stage 1.6: PV Single-Line Diagram Best Practices & Parametric Model

**Goal:** Research single-line diagram conventions for photovoltaic systems and define the parametric data model that will drive automatic diagram generation.

**Success Criteria:**
- Industry best practices for PV single-line diagrams researched (IEEE, IEC, regional variants)
- Mexican-compliant symbology standards documented (per NOM-001-SEDE)
- Typical PV system components identified:
  - DC side: arrays, strings, string combiners, DC disconnects, DC protections (breakers, fuses)
  - Inverters: string, central, hybrid configurations
  - AC side: inverter breakers, AC disconnects, transformers, grid interconnect points
  - Protections: overcurrent, overvoltage, grounding, islanding detection
- Example single-line diagrams for common configurations (5kW residential, 30kW commercial, 100kW utility)
- Parametric model defined:
  - Input parameters: array count, inverter type/count, string configuration, protection specs
  - Output diagram data model: symbol types, connections, labels, zones
- Symbol library requirements documented (vector format, attributes per symbol)

**Tests:**
- Vault lint: all example diagrams linked and sourced
- Concept pages for each diagram component with symbol descriptions
- Parametric model documented in machine-readable format (JSON/YAML schema stub)
- Coverage checklist: main PV topologies (radial, series-parallel, multi-inverter) covered

**Status:** Complete

---

### Stage 1.7: Vault Lint & Phase 1 Review; Release v0.1.0-docs

**Goal:** Validate vault completeness and health, perform phase review, and release Phase 1 as `v0.1.0-docs`.

**Success Criteria:**
- Vault lint pass:
  - No orphaned or dead-link pages
  - All wikilinks resolve correctly
  - All entity and concept pages have complete frontmatter
  - All sources cited with proper metadata
  - No duplicate entities or concept definitions
- Phase 1 review checklist:
  - All 6 prior stages marked Complete
  - `wiki/index.md` updated with final page count and source ingestion count
  - `wiki/hot.md` summarizes key findings and open questions
  - `wiki/overview.md` readable by newcomers
- Git release:
  - `release/0.1.0-docs` branch created from develop (git-flow, tag prefix `v`)
  - `CHANGELOG.md` updated with stage summaries
  - `IMPLEMENTATION_PLAN.md` updated (Phase 1 condensed; Phases 2–5 stay planned here)
  - Merge to main, tag `v0.1.0-docs`
  - Merge back to develop

**Tests:**
- Vault lint tool (provided by `claude-obsidian:wiki-lint`) passes with zero errors
- All files UTF-8 encoded, no broken image links
- GitHub release created for `v0.1.0-docs` with release notes (once the public repository is published)
- Tag `v0.1.0-docs` exists on main branch

> Closed 2026-10-05: vault lint 0 errors after fixes (`Lint Report 2026-10-05`), tag `v0.1.0-docs` on main. Public repository published the same day (https://github.com/edu3250/Diagrama-unifilarAutoCAD) with GitHub release `v0.1.0-docs`.

**Status:** Complete

---

## Phase 2: MCP Server Prototype & Connectivity Spike

**Goal:** Prove ADR-0001 before committing to production code. Five stages cover the Python project scaffold (2.0), the ezdxf rendering path (2.1), AutoCAD 2027 connectivity (.NET 10 plug-in vs COM, 2.2), the MCP round trip from Claude Code (2.3), the Core Console finisher (2.4), and spike review + ADR confirmation (2.5). All numeric thresholds are proposed targets from the ADR.

**Target Release:** `v0.2.0-spike`

**Status:** Complete (released as `v0.2.0-spike`, 2026-10-06)

---

### Stage 2.0: Foundation – Python Project Scaffold & CI

**Goal:** Establish the Python project structure, development environment, testing framework and continuous integration pipeline to support Stages 2.1–2.5.

**Success Criteria:**

- Project layout: `src/pvsld/` (package), `tests/`, `examples/`, `.github/workflows/`, `pyproject.toml`
- `pyproject.toml` with src layout, package name `pvsld`, runtime dependencies (ezdxf, mcp, pydantic, PyYAML), a `dev` extra (pytest, pytest-cov, ruff), an `autocad` extra (pywin32, Windows only), build backend (hatchling), and CLI entry point
- Tooling: ruff (lint + format), pytest (with `autocad` marker excluded by default in CI), coverage ≥60% for changed code
- CI matrix: `windows-latest` and `ubuntu-latest`, Python 3.11+
- `.mcp.json` is created in Stage 2.3 together with a working server (a placeholder would make Claude Code try to start a non-existent server)
- Sample input file `examples/residential_7p7kwp.yaml` from the vault's worked example (PV SLD Parameter Model)
- `venv` instructions in `README.md` and `CONTRIBUTING.md` updated

**Tests:**

- Verify `pyproject.toml` is valid, can be parsed and build succeeds
- Confirm toolchain (ruff, pytest) runs without errors on a clean checkout
- Check that AutoCAD-marked tests are skipped in CI (marked tests report "skipped", not failures)
- Verify sample parameter file is valid YAML and matches the schema stub
- GitHub Actions workflow passes on both Windows and Ubuntu for Python 3.11

> Merged 2026-10-06 (PR #1): CI green on Ubuntu and Windows, Python 3.11 and 3.12.

**Status:** Complete

---

### Stage 2.1: S1, ezdxf Minimal PV SLD

**Goal:** Render the 7.70 kWp residential sample parameter file to a DXF R2018 SLD through a backend-neutral diagram model. The sheet has an A3 layout, title block and string table, and uses a minimal symbol library.

**Success Criteria:**
- `doc.audit()` reports 0 errors and 0 fixes
- Output is byte-identical across two runs and across Windows and Linux CI runners (fixed metadata)
- INSERT count per block equals the model count; 100 % attribute round-trip by `COMP_ID`; 0 dangling ports; 0 entities on layer `0`
- Build + write ≤ 1 s; PNG preview ≤ 3 s
- The DXF opens in AutoCAD 2027 and AUDIT reports 0 errors (manual check)

**Tests:**
- Golden-file test against a committed DXF fixture
- Semantic ezdxf query tests (blocks, attributes, layers, port connectivity)
- Rule-subset tests (VOLT-001, STR-001, STR-004, CON-003, PCC-002, MET-001, DIS-003/004) pass on the sample and fail as expected on 3 mutated specs

> Merged 2026-10-06 (PR #3): audit 0/0; byte-identical output across runs, processes and the Windows/Linux CI matrix; 9 blocks / 11 inserts; 147/147 attributes round-trip by `COMP_ID`; 0 dangling ports; 0 entities on layer 0; build + write 97 ms; PNG preview ~1.1 s. The AutoCAD AUDIT check is measured headlessly in Stage 2.4. Symbol naming: ADR-0003 (accepted).

**Status:** Complete

---

### Stage 2.2: S2, AutoCAD 2027 Connectivity (.NET 10 Plug-in vs COM)

**Goal:** Prove the AutoCAD backend transport. A `net10.0` plug-in listens on a current-user named pipe (newline-delimited JSON-RPC plus a secret generated at each start). It answers a ping, inserts an attributed block and reads attributes back. A throwaway Python COM spike against `AutoCAD.Application.26` serves as the baseline.

> .NET 10 SDK 10.0.401 installed with the owner's approval on 2026-10-05. Attended run on AutoCAD 2027 (2026-10-06): 22/22 tests; ping p95 2.25 ms; 200 + 200 batch 47 ms (71 ms worst); 100/100 runs; busy errors in 0.001–1.89 s; 5/5 injected failures rolled back; unauthenticated client refused. COM was 37–186× slower for drawing work. See `docs/spikes/s2-autocad-connectivity.md` (PR #2).

**Success Criteria:**
- COM baseline: pywin32 spike against `AutoCAD.Application.26` with message-filter retries; operations (ping, insert attributed block, read attributes, batch of 200 inserts + 200 lines); reliability metrics and latency recorded
- .NET 10 plug-in: named pipe with newline JSON-RPC and secret; plug-in ping p95 < 50 ms; 200-insert batch < 2 s; COM/plug-in latency ratio recorded
- 100/100 consecutive runs without unhandled errors
- With a modal dialog or an active command, the plug-in returns an actionable "busy" error within 5 s, and AutoCAD never hangs
- An injected mid-transaction failure leaves the drawing unchanged
- A client without the secret is refused; the pipe ACL is current-user only

**Tests:**
- COM spike: pywin32 benchmark harness (marked `autocad`, run manually on the licensed workstation)
- .NET 10 plug-in benchmark: marked `autocad` and run manually
- Protocol tests of the pipe client against a fake server (CI, SDK-independent)
- Benchmark results recorded in the vault

**Status:** Complete

---

### Stage 2.3: S3, MCP Round Trip from Claude Code

**Goal:** Build a Python MCP server (`mcp` 2.x, stdio) registered through a repo `.mcp.json`. It exposes `validate_pv_design`, `generate_single_line_diagram` (ezdxf backend) and the parameter-schema resource.

**Success Criteria:**
- The server starts and lists tools in < 5 s (Claude Code's startup timeout is 30 s)
- Claude completes validate → generate on the sample in ≤ 4 tool calls; the result (drawing_id, DXF path, PNG preview) stays under the 25k-token output cap
- An invalid spec (12 modules per string) returns a VOLT-001 tool error that Claude then corrects

**Tests:**
- In-memory MCP client tests in CI
- MCP Inspector session
- Optional: the same flow in Claude Desktop within 240 s

> Merged 2026-10-06 (PR #5): `mcp` 2.3.0 stdio server with `validate_pv_design`, `generate_single_line_diagram` and three resources. It starts and lists tools in 2.0–2.6 s, and a result with the preview is about 20.9k tokens (estimate). In the owner's manual Claude Code run, validate → generate took 2 calls, and Claude corrected the 12-module VOLT-001 spec in 4. Claude also spotted an inverter DC overload that no implemented rule caught (STR-007, Phase 3). MCP Inspector was not run (no Node.js) and Claude Desktop was not tested. See `docs/spikes/s3-mcp-round-trip.md`.

**Status:** Complete

---

### Stage 2.4: S4, Core Console Finisher

**Goal:** Convert the S1 DXF to DWG 2018 and plot a PDF headlessly with `accoreconsole.exe` on the licensed workstation.

**Success Criteria:**
- The DWG opens in AutoCAD 2027 without the "not saved by Autodesk" notice; a PDF is produced
- ≤ 15 s per sheet
- Running outside a licensed session produces a reported error, never a silent success

**Tests:**
- Scripted finisher run (marked `autocad`) that checks the exit code, the existence of the output files and a timeout

> Merged 2026-10-06 (PR #6): `pvsld finish` gives DWG `AC1032` (TrustedDWG) and a 1-page A3 PDF in 4.2–10.8 s per sheet, and 4/4 failure modes are reported. It also found that AutoCAD AUDIT rejects the S1 overall paper-space viewport off layer 0; PR #7 fixed this, and AUDIT now reports 0/0 (12.2 s cold). See `docs/spikes/s4-core-console.md`.

**Status:** Complete

---

### Stage 2.5: Spike Review, ADR-0001 Confirmation and Release v0.2.0-spike

**Goal:** Review the S1–S4 results against ADR-0001's exit gate and reversal triggers. Either confirm the ADR or record a superseding ADR, then plan Phases 3–5 to match.

**Success Criteria:**
- S1 and S3 meet their criteria, and S2 shows the plug-in meets its reliability criteria; otherwise a superseding ADR is recorded
- Spike measurements and decisions are logged in the vault
- `CHANGELOG.md` is updated and `v0.2.0-spike` is tagged per GitFlow

**Tests:**
- All CI tests green on Windows and Linux
- Vault lint passes for the new notes

> Review 2026-10-06 (`docs/spikes/phase-2-review.md`): the exit gate is passed and ADR-0001 is confirmed, with no superseding ADR. Reversal triggers T1–T9 were not fired. A "Phase 2 validation" section is appended to ADR-0001 (repository mirror and vault original), and the vault note "Phase 2 Spike Review 2026-10" records the review. `CHANGELOG.md` is updated. Released as `v0.2.0-spike` (GitFlow release branch, tag on `main`, GitHub release).

**Status:** Complete

---

## Phase 3: Parametric PV Sizing Engine & Complete Rule Pack

**Goal:** Implement the deterministic PV system sizing engine, store component specifications from manufacturer datasheets, complete the validation rule pack to all 98 rules (including STR-007, the gap found in Phase 2), and expose sizing as an MCP tool to Claude. The owner supplies component datasheets; the team extracts specifications, validates them, and stores them in a versioned catalogue. The sizing engine proposes string configurations, inverter matching, and balance-of-system (BOS) sizing deterministically, while Claude elicits parameters and explains trade-offs.

**Target Release:** `v0.3.0-engine`

---

### Stage 3.1: Component Catalogue Data Model & Storage Format (ADR-0004)

**Goal:** Design and implement the component catalogue schema and storage backend. Define how equipment datasheets map to the parameter model, including PV module specs (Pstc, Voc, Vmp, Imp, temp coefficients), inverter specs (Pdc_max, Vdc_max, MPPT count and limits, AC power, certifications), and balance-of-system components (conductors, fuses, breakers, SPD ratings). Establish a workflow for datasheet ingestion: PDF → markitdown → human review → versioned catalogue.

**Success Criteria:**

- ADR-0004 (proposed) documents the chosen storage format (YAML per component, SQLite, or JSON), with rationale for cost-of-change, auditability, and version control
- Component schema defined and validated:
  - PV modules: `pstc_w`, `voc_v`, `vmp_v`, `imp_a`, `temp_coef_voc` (V/K), `temp_coef_pmp` (%/K), `temp_coef_imp` (A/K), `isc_a`, dimensions, weight, frame type, source (datasheet SHA-256, page ref, extraction date, reviewed flag)
  - String/hybrid inverters: `pdc_max_w`, `vdc_max_v`, `vdc_min_v`, `mppt_count`, per-MPPT `isc_max_a`, `vmp_window_v`, `pac_nominal_w`, `pf_range`, `thd_max`, certifications (UL-1741-SB, IEEE-1547, NMX), source fields
  - Conductors: rated ampacity vs. temperature, insulation type, core cross-section, voltage drop per meter at rated current
  - Overcurrent devices: interrupting rating (KAIC), voltage rating, current rating, certifications
- `src/pvsld/catalogue.py` module with typed schema (Pydantic) and a `ComponentRegistry` class
- Git-ignored `datasheets/inbox/` and `datasheets/cache/` directories (raw PDFs and markitdown); committed `datasheets/records/` with per-component JSON/YAML records including provenance
- Unit validation tests (e.g., Vmp < Voc, Imp < Isc, temperature coefficients in plausible ranges)
- Documentation in `CONTRIBUTING.md` on the datasheet ingestion workflow

**Tests:**

- Schema validation: 10+ fixtures pass Pydantic type checking and unit plausibility
- Three mutated specs fail expected validations (e.g., Voc < Vmp, negative temp coeff where positive expected)
- Roundtrip test: record → model → JSON serialization is lossless
- File integrity test: source SHA-256 and extraction metadata are present and immutable after commit
- Coverage: at least 3 PV modules, 3 inverters, 5 conductor types, 10 protection devices in the initial commit

**Status:** Not Started

---

### Stage 3.2: Datasheet Ingestion Pipeline & Validation Workflow

**Goal:** Create a semi-automated pipeline to extract component specifications from manufacturer PDFs using markitdown and Claude's reading ability, validate the extracted facts, and store them in the catalogue. Establish a human-review workflow so the owner can approve extracted records before they're committed.

**Success Criteria:**

- `docs/datasheet-ingestion-protocol.md` documents the workflow:
  1. Owner places PDF in `datasheets/inbox/<component_type>/<model>.pdf`
  2. markitdown converts to markdown, cached in `datasheets/cache/` (git-ignored)
  3. Claude reads the markdown and extracts structured facts into a JSON template
  4. Python validation script checks plausibility (units, ranges, cross-field consistency)
  5. Owner reviews the extracted record and either approves or flags for re-read
  6. Approved record is committed to `datasheets/records/<component_type>/<model>.yaml`
- `src/pvsld/ingest.py` script:
  - `ingest_datasheet(pdf_path: Path, component_type: str) → dict` (returns extraction template for human review)
  - `validate_component_record(record: dict, schema: Type[BaseModel]) → tuple[bool, list[str]]` (returns validity and any warnings)
  - Token budget for PDFs: estimate tokens with markitdown, then chunk if > 50k
- Example ingestion of 3 real datasheets (modules, inverter, protection device) in the repo history
- Validation catches at least 5 common datasheet mistakes (ambiguous specs, unit mismatches, out-of-range values)
- `src/pvsld/rules.py` extended with a datasheet-sourced fact check (rule DTS-001: every catalogue component has a source and extraction date)

**Tests:**

- Unit test: `test_validate_component_record` with 10+ fixture records (valid, under-spec'd, out-of-range, missing required fields)
- Integration test: `test_ingest_example_datasheet` reads a sample markdown (synthetic, no copyright), extracts to template, validates, and compares against expected fields (photosynthetic match, not byte match)
- Workflow test: a dry run of the full pipeline on one owner-supplied PDF (marked `datasheets`, skipped in CI because the PDF is not in the repository; only the reviewed record is committed)
- No full PDFs committed to the public repo; `.gitignore` enforces this

**Status:** Not Started

---

### Stage 3.3: Rule Pack Completion & STR-007 Priority

**Goal:** Implement all 98 validation rules in the Mexican PV SLD rule pack (`mx-gd-2026.10`), with immediate focus on STR-007 (inverter DC power and DC/AC ratio), which Phase 2 identified as a gap. Update the vault tables against the current NOM-001-SEDE-2012 and CRE regulations.

**Success Criteria:**

- Phase 2 baseline: 8 of 98 rules implemented; Phase 3 target: ≥ 80 of 98 (85 %)
- **STR-007 (inverter DC power)** implemented and tested:
  - Rule: Σ(P_STC of modules on each string × number of strings) ≤ inverter P_dc,max (error if violated)
  - DC/AC ratio check: ratio ≥ 1.0 and ≤ policy ceiling (e.g., 1.35 for warnings, 1.5+ as errors per owner policy; configurable)
  - Test case: the Phase 2 owner test (2 × 11 × 550 W = 12.1 kWp vs. inverter P_dc,max = 9 kW) correctly rejects as STR-007
- Vault note `PV SLD Validation Rules.md` re-read against NOM-001-SEDE-2012 (published text, not phase 2 memory); all 98 rules updated with:
  - Rule ID (`STR-NNN`, `VOLT-NNN`, `CON-NNN`, `PCC-NNN`, `MET-NNN`, `DIS-NNN`, `PROT-NNN`, `I-NNN`)
  - Mexican regulation reference (NOM, CRE/CNE table, CFE guideline, UVIE requirement)
  - Machine-checkable condition (formal or pseudo-code)
  - Severity (error vs. warning/info)
- Coverage test per MX-xx checklist item: 65 of 71 machine-checkable items (MX-A through MX-I) have a mapping in `src/pvsld/rules.py`
- `src/pvsld/rules.py`:
  - One rule class per catalogue rule (factory or registry pattern)
  - `RulePack` with a `check(spec: PVSystemSpec, catalogue: ComponentRegistry) → list[Finding]`
  - `Finding` dataclass with rule_id, severity, description, affected_component_ids, remediation hint
- Rule test suite:
  - Happy path: the residential_7p7kwp example passes all implemented rules
  - Sad paths: 30+ mutations of the example (bad string voltage, bad conductor size, too many modules, inverter overload, etc.) each fail the expected rule
  - Regression: the Phase 2 8-rule subset still passes

**Tests:**

- `test_rule_str_007_dc_power`: nominal, at limit, and over limit cases
- `test_rule_pack_coverage`: every MX-xx item has a mapping
- `test_residential_7p7kwp_passes`: golden spec passes all implemented rules
- `test_mutations_fail_expected_rules`: 30+ mutations each trigger exactly one expected rule
- Vault lint: all 98 rule IDs in the vault have a corresponding Python implementation or a deferred note
- CLI: `pvsld check examples/residential_7p7kwp.yaml` reports 0 findings

**Status:** In Progress. STR-007 is implemented (9 of 98 rules; PR for Stage 3.4): array STC power against the inverter PV power limit (error), DC/AC ratio policy with defaults warning above 1.35 and error above 1.50 in `pvsld.core.policy` (owner decision pending). The other 89 rules, the vault re-read and the coverage tests remain.

---

### Stage 3.4: Parametric Sizing Engine (Strings, Inverters, Conductors, OCPD)

**Goal:** Implement the deterministic sizing engine: given a site (min/max temperature, utility service voltage/frequency), a target system size (kWp), and a choice of components from the catalogue, compute viable string configurations, inverter matching, DC/AC ratio, conductor sizes, overcurrent protection, and voltage drop. Return a ranked list of candidate designs and recommend one. Explain why alternatives were rejected.

**Success Criteria:**

- `src/pvsld/sizing.py`:
  - `propose_string_configs(modules: list[PVModule], vdc_window: tuple[float, float], mppt_isc_max_a: float, site: SiteParams) → list[StringConfig]`
    - Input: module model, min/max counts per string (from voltage rules VOLT-NNN), MPPT current limit, temperature extremes
    - Output: ≥ 1 and ≤ 10 candidate configs with (modules_per_string, count, voc_min_hot, voc_max_cold, isc, pdc)
    - Compute temperature-corrected Voc at T_min (cold) and T_max (hot) using module temp coefficient
    - Reject configs where Voc_cold > MPPT V_max or Voc_hot < MPPT V_min (VOLT rules)
    - Reject configs where string Isc > MPPT I_max or total Pdc > inverter Pdc_max (STR-007)
  - `match_inverters(target_pdc_w: float, target_vdc_window: tuple, site: SiteParams, catalogue: ComponentRegistry) → list[InverterMatch]`
    - Propose ≥ 1 inverter from the catalogue that fits the DC power and voltage window
    - Compute DC/AC ratio and report as info/warning/error per policy
  - `size_conductors_and_ocpd(strings: list[StringConfig], pdc_total: float, vdc: float, circuit_type: str, max_voltage_drop_pct: float = 3.0) → BosSpec`
    - Input: DC current (Isc × safety factor), AC current (Pac / V_ac), conductor routing length, ambient temperature
    - Output: conductor AWG/mm², OCPD type and rating, voltage drop %, grounding requirements
    - Use NOM-001 tables from vault for copper conductor ampacity (temperature-derating and bundling)
    - Apply 125 % factor for continuous loads, 80 % factor for non-continuous; STR rules for string current limits
    - Reject sizing where voltage drop > max (3 % DC typical, 3 % AC typical per NOM)
  - `size_pv_system(site: SiteParams, target_pdc_w: float, module_model: str, inverter_choice: Optional[str], owner_constraints: dict) → SizingResult`
    - Orchestrator: call propose_string_configs, match_inverters, size_conductors, then validate full spec with rule pack
    - Return a `SizingResult` with a ranked list of candidate specs (best first), selected spec, and a human-readable report explaining trade-offs and rejections
  - All sizing is deterministic Python; no LLM calls
- Example (the Phase 2 owner test): with the sample 550 W module and the 6 kW inverter (P_dc,max 9 kW, V_dc,max 600 V) at T_min −3 °C, the engine rejects 2 × 12 (VOLT-001, 640 V) and 2 × 11 (STR-007, 12.1 kWp > 9 kW), and selects 2 × 8 (8.8 kWp, DC/AC 1.47, Voc 426.8 V), reporting why each alternative was rejected; its conductors, OCPD and voltage drop pass the rule pack
- Test harness: `test_size_residential_7p7kwp` passes known configurations
- Token budget: sizing result (candidate list + selected spec + explanations) stays under 10k tokens (leaves room for Claude's reasoning)

**Tests:**

- `test_string_config_temp_corrected_voc`: Voc at hot and cold temperatures is within voltage window
- `test_string_config_reject_overvoltage`: config with Voc_cold > MPPT_Vmax is rejected
- `test_string_config_reject_overpower`: config with Pdc > inverter Pdc_max is rejected (STR-007)
- `test_inverter_match`: only inverters with Vdc_min ≤ string_vmp ≤ Vdc_max are matched
- `test_conductor_size_voltage_drop`: 7.7 kWp system has ≤ 3 % drop on 50 m DC run with chosen conductor
- `test_ocpd_rating_continuous`: DC OCPD is sized at 125 % × Isc (continuous load rule)
- `test_sizing_result_passes_rule_pack`: proposed spec runs through full rule validation and reports 0 errors (warnings/info allowed)
- `test_residential_example`: end-to-end sizing of the Phase 2 7.7 kWp example returns ≥ 1 candidate, selected spec, no validation errors

**Status:** In Progress. v1 is done in `pvsld.sizing` (strings, string OCPD from the catalogue, inverter-output breaker, copper conductors and voltage drop; `pvsld size`). Stage 3.4b remains: EGC per Table 250-122, conduit fill, external DC disconnect, inverter maximum OCPD (OCP-005), multiple inverters, three-phase services, aluminium, optimizer limits (STR-009).

---

### Stage 3.5: MCP Tool Surface & Token Budget Confirmation

**Goal:** Expose the sizing engine as an MCP tool (`size_pv_system`) and publish the component catalogue and rule pack as MCP resources. Confirm the preview token budget with real Claude runs and document the limits.

**Success Criteria:**

- `src/pvsld/mcp/server.py` extended with:
  - `size_pv_system` tool: input schema (site, target_pdc_w, preferred_components, owner_constraints), returns result JSON with candidates, selected spec, report text, and a visual diagram preview (SVG or small PNG, ≤ 5 kB)
  - `list_components` resource / tool: filter by type (module, inverter, conductor, ocpd), return brief catalogue
  - `get_component` tool: fetch full datasheet-sourced spec of one catalogue entry
  - `ingest_datasheet` tool (optional, early): upload a PDF and trigger the extraction pipeline (returns a template for owner review, not a committed record)
- `.mcp.json` updated to list the new tools
- Token budget test (real Claude Code run or estimate):
  - Sizing result (full candidate list + explanations): ≤ 8k tokens
  - Rule catalogue (all 98 rules + MX references): ≤ 15k tokens (published as a resource, not in every call)
  - Total per `size_pv_system` call: ≤ 20k tokens (margin for Claude's reasoning: 25k hard cap, so 5k buffer)
- Environment variable `PVSLD_MCP_COMMAND` documented in README and `.mcp.json` configured correctly
- Optional MCP Inspector session run (if Node.js is installed; confirm tools and schemas are listed)
- User-facing documentation: a worked example in the README ("Size a 10 kWp system for Guadalajara") that calls `size_pv_system`, shows the result and selected spec

**Tests:**

- `test_mcp_size_pv_system_schema`: tool input and output schemas are valid OpenAPI
- `test_size_pv_system_result_under_token_cap`: result JSON serialization is ≤ 20k tokens
- `test_mcp_list_components_by_type`: filter works and returns expected items
- `test_mcp_get_component_detail`: fetches full spec including datasheet provenance
- Integration test: `test_mcp_server_lists_tools`: the server starts, lists tools in < 5 s, and schemas match the code
- Optional: manual Claude Code session ("size a 15 kWp commercial system") within 240 s, no output-cap errors

**Status:** In Progress (implemented in PR; a real Claude Code session by the owner pending)

**As built:**

- `size_pv_system` takes the module, the inverters (ids or `auto`), a target (`target_dc_power_w` or `module_count_min/max`), the non-sizing `template` (example in the resource `pvsld://examples/sizing-template`), `routing`, `dc_ocpd` and `layout_template`.
- It returns the selected candidate, its complete spec, up to 5 brief alternatives, rejections grouped by rule with one Spanish example, and the assumptions.
- A result is about 3k tokens with `inverters: auto` (budget 8k, tested). The rule-pack resource is tested at 15k or less.
- No diagram preview in the sizing result: `generate_single_line_diagram` returns one.
- `list_components` and `get_component` read `$PVSLD_CATALOGUE_DIR` (default `datasheets/records`), reviewed records only unless `include_unreviewed`.
- `ingest_datasheet` (optional) is left for Stage 3.6.

---

### Stage 3.6: Claude Code Plugin (plan approved by the owner 2026-10-09)

**Goal:** Anyone, with or without AutoCAD, installs the `pvsld` plugin from the public marketplace and gets a complete design from a plain request ("genérame un diagrama con 5 paneles Jinko, escoge lo mejor") or from a professional Excel project sheet.

**Deliverables for every design:**
- Editable diagram: DWG and DXF with AutoCAD; DXF without it.
- Diagram PDF: AutoCAD's PDF, or the plugin's own vector A3 PDF.
- Calculation report (memoria de cálculo): Excel with live formulas, and PDF.
- Prefilled project sheet (Excel).
- Chat summary: the installation's bill of materials, plus a short explanation of the inverter choice.

**Commands:**

| Command | Use |
|---|---|
| `/unifilar <request>` | Quick mode |
| `/unifilar-pro nuevo` | Blank project sheet |
| `/unifilar-pro <sheet.xlsx>` | Read and validate the sheet (failing cells come back in red), then generate |
| `/unifilar-catalogo agregar <datasheet.pdf>` | Add a component to the local catalogue |

A plain request also triggers the skill.

**Owner decisions (2026-10-09):**
- Ranking prefers a DC/AC ratio of 1.10–1.25 (else the closest), then less clipping, then the smaller inverter. The user gets a short Spanish explanation of the choice.
- DC/AC limits: warning above 1.35, error above 1.50, information below 1.0.
- Keep the 1.25 factor in STR-004; STR-002 stays a rejection.
- Site temperatures use the defaults.
- Personal data stays blank unless the user fills it in, in professional mode.
- A missing component is added to the user's local catalogue from its datasheet, after the user confirms the values.
- Publish on the public marketplace; ask the owner first.

**Sub-stages (one PR each):**
- **3.6.0:**
  - ranking by DC/AC band, with the explanation (`Candidate.explanation_es`);
  - the box switch uses all its poles (4 for one string);
  - remove the old worktrees.
- **3.6.1:** `design_and_draw`, a one-step MCP tool:
  - sizing → validation → drawing → DWG/PDF, or DXF plus the own PDF;
  - report in xlsx and pdf, project sheet, BOM text;
  - also: the server-side BOM, the own diagram PDF and AutoCAD detection;
  - openpyxl and reportlab become dependencies.
- **3.6.2:** professional mode:
  - project-sheet generator, reader and validator;
  - engine overrides for every user-fixed value;
  - personal data pass-through.
- **3.6.3:** local catalogue:
  - a user folder merged with the bundled catalogue;
  - datasheet extraction, confirmed by the user;
  - offered automatically when a component is missing.
- **3.6.4:** packaging (plan approved by the owner on 2026-10-09, checked against the official
  Claude Code plugin documentation):
  - **3.6.4a**, data inside the wheel:
    - `pvsld.resources.data_path` serves the records, symbol library, A3 template and example from the
      installed package (`force-include` under `pvsld/_data`) or from the repository;
    - CI job `package` builds the wheel, starts `pvsld-mcp` with `uvx --from .` and designs an
      installation with the installed wheel outside the checkout (Linux and Windows).
  - **3.6.4b**, the plugin:
    - plugin `pv-unifilar` (display name "PvUnifilar") in `plugins/pv-unifilar/`. `plugin/` is the .NET AutoCAD bridge;
    - skills `diagrama`, `pro` and `catalogo`, run as `/pv-unifilar:diagrama`, `/pv-unifilar:pro` and
      `/pv-unifilar:catalogo` (Claude Code always prefixes the plugin name); a plain request also
      triggers the quick mode; `allowed-tools` pre-approves the pvsld tools while a skill runs;
    - `.mcp.json`: `uvx --from git+https://github.com/edu3250/Diagrama-unifilarAutoCAD@<tag> pvsld-mcp`;
      optional user configuration of the output folder;
    - marketplace `pvsld` in `.claude-plugin/marketplace.json` at the repository root, visible in
      develop from 3.6.4;
    - `claude plugin validate --strict` and manifest tests in CI;
    - the owner authorized the pre-release tag `v0.5.0rc1` once 3.6.4 is merged, to test the
      installed plugin from GitHub.
- **3.6.5:**
  - Spanish user guide (`docs/guia-de-usuario.md`);
  - real-prompt tests on a clean install, with and without AutoCAD (results below);
  - release v0.5.0 (the owner asked for it on 2026-10-12): version 0.5.0 in the package, the plugin
    and the documents, merged to `main`, tagged `v0.5.0`; the marketplace is the repository itself.

**Real-prompt tests of 3.6.5 (2026-10-12, headless Claude Code 2.1.296, default permission mode):**
- Clean configuration folder: `claude plugin marketplace add edu3250/Diagrama-unifilarAutoCAD` and
  `claude plugin install pv-unifilar@pvsld` install from the default branch, as does the one-line
  `--marketplace` form. Git is needed (the marketplace is cloned over HTTPS).
- Start-up of `uvx --from git+…@v0.5.0rc1 pvsld-mcp` until it answers `initialize`: 31.6 s with an
  empty uv cache (23.3 s from a local wheel, so about 8 s are the clone and the build), 6 to 18 s
  with the cache. Claude Code waits 30 s, so the first start can fail; the guide and the skills say
  how to continue. A wheel on the release or on PyPI would shorten it: offered to the owner.
- Installed plugin (v0.5.0rc1): the quick mode by command and by a plain request (8 files without
  AutoCAD), a module that is not in the catalogue (asks for its datasheet), a blank project sheet,
  and a sheet with a fuse that does not fit (the cell and the reviewed copy). The skills'
  `allowed-tools` work: no tool was denied when the user ran the command; a plain request asks
  permission for the skill first.
- Fixes found by these tests: see the CHANGELOG. Tested again with the corrected code loaded with
  `--plugin-dir`: the quick mode with real AutoCAD in a long folder with accents (9 files, 35 s),
  the professional mode with a wrong fuse and then with AUTO (personal data with any character
  reaches the drawing), and a server that never starts (the skill sends the user to `/mcp`).
- Not tested: adding a component from a datasheet with the installed plugin (no new datasheet at
  hand; covered by the 3.6.3 tests), macOS, and a machine without Python.

**Limitations (documented for users):**
- Claude Code only.
- Needs `uv`.
- AutoCAD output needs Windows and a licence.
- Scope: 1 inverter, up to 2 strings, residential LV, CFE, NOM-001-SEDE-2012.
- New equipment needs its datasheet.
- Drawings are drafts for the responsible engineer.

**Tests:**
- Unit and integration tests per sub-stage.
- CI checks the manifest JSON.
- Owner's manual run of both modes on a clean install.

**Status:** Complete (released as v0.5.0 on 2026-10-12)

---

### Stage 3.7: PvUnifilar in any agent CLI (plan approved by the owner 2026-10-12)

**Goal:** The same design from a plain request in Copilot CLI, Gemini CLI, Codex CLI and any other
MCP client, not only in Claude Code.

**Approach:** The MCP server is the product, because it is the one thing every client loads the
same way; the skills are a layer over it. One plugin folder serves every client that reads plugins,
and thin manifests cover the rest. Packaging the skills once per client was the alternative, and it
would have left three copies of the workflow to keep equal.

**What each client reads (checked with the CLIs installed, 2026-10-12):**

| Client | Reads | Install |
|---|---|---|
| Claude Code | `.claude-plugin/plugin.json`, `.mcp.json`, `skills/` | as in Stage 3.6 |
| Copilot CLI 1.0.96 | Agent Plugins 1.0 (root `plugin.json`, `mcp.json`, `skills/`) first, else Claude Code's files; `.claude-plugin/marketplace.json` | `copilot plugin marketplace add edu3250/PvUnifilar`, then `copilot plugin install pv-unifilar@pvsld` |
| Gemini CLI 0.63.0 | `gemini-extension.json` at the root of the repository; skills only with `name` in `SKILL.md`; MCP prompts as commands | `gemini extensions install https://github.com/edu3250/PvUnifilar` (organizations and API keys only: see below) |
| Antigravity CLI 1.3.4 (`agy`) | root `plugin.json`, `skills/` and `mcp_config.json`; it also converts a Claude Code plugin folder with `agy plugin import <folder>` | clone the repository, then `agy plugin install <clone>/plugins/pv-unifilar` (no command adds a marketplace) |
| Codex CLI | Agent Plugins 1.0 and Claude Code's marketplace (documentation only: not installed, needs a paid plan) | not tested |
| Any other | the MCP server | one `uvx` configuration block |

**Sub-stages:**
- **3.7.2 and 3.7.3 (one PR, they share the skill texts): the server carries the workflows and the
  plugin is portable.**
  - The three skills are also MCP prompts (`diagrama`, `pro`, `catalogo`), read from the same
    `SKILL.md` files inside the wheel. `PVSLD_MCP_PROMPTS=off` leaves them out where the client
    already shows the skills.
  - Skills in the Agent Skills format: `name`, no `$ARGUMENTS`, no wording that only fits Claude
    Code. Each tells the model not to draw by other means when the server is not connected.
  - Agent Plugins 1.0 manifests beside Claude Code's; `gemini-extension.json` with a context file.
  - An unfilled `${...}` placeholder in `PVSLD_OUTPUT_DIR` counts as not set.
- **3.7.1: a server that connects however long it takes to start, and the package on PyPI.**
  - `pvsld.mcp.handshake`: when the first frames in the pipe are `server/discover` followed by
    `initialize`, the client gave up on the first, so it is dropped and the SDK serves the
    handshake the client is waiting for. Checked with Copilot CLI and a server started 13 s late:
    connected. This replaced the plan of importing the drawing libraries on first use, which would
    have saved about 1 s of 3 and left the first start (23 to 46 s) failing.
  - `release.yml` publishes the package on PyPI from a release tag with trusted publishing (name
    `pvsld`, chosen by the owner on 2026-10-12, who registers the publisher on pypi.org).
    `docs/pypi.md` is what PyPI shows.
  - The plugin, the extension and the documents change to `uvx --from pvsld==<version> pvsld-mcp`
    in the release commit, when the package exists: a faster start and no need for Git.
- **3.7.4: real-prompt tests in Gemini CLI and Copilot CLI with the published package, a section
  per client in the user guide, release v0.6.0 and the MCP registry.**

**Findings of the first real tests (2026-10-12):**
- Copilot CLI with its own model (`gpt-6-luna`) and this plugin: a plain request loads the skill
  `diagrama`, calls `list_components` and `design_and_draw`, and delivers the 9 files with the DWG,
  in Spanish, with the reason for the inverter and the draft warning. This needed a server that
  starts fast (the repository's own environment).
- **Copilot CLI waits 10 s for the server's first answer.** It sends `server/discover` (protocol
  2026-07-28), and after 9.7 s without an answer it sends the older `initialize`. The MCP SDK
  (2.3) has by then fixed the connection to the newer protocol and refuses it, so the server is
  marked as failed. Measured with a server that never answers, and reproduced by delaying ours by
  6 s. `uvx` from the git tag takes 6 to 18 s with everything cached, so the published plugin
  fails in Copilot CLI most of the time. From a package index the same start took about 9 s on a
  busy machine and the repository's environment 2.3 to 4.5 s: hence 3.7.1.
- When the server was not connected, Copilot's model followed the skill and told the user what to
  do. Claude without the skill wrote a script to draw the diagram by hand: the reason for the
  warning in every skill and in the Gemini context file.
- Gemini CLI: the extension validates and installs from a folder, the server connects and its
  skills are listed once they have `name`. From GitHub it only looks for the manifest at the root
  of the repository. No prompt was run: the owner has to sign in first.
- Claude Code, with the portable skills: no change (8 files without AutoCAD, 4 turns).
- **Gemini CLI no longer serves personal accounts** (2026-10-13). The owner's sign-in was refused:
  since 2026-06-18 "Login with Google" is closed for the individual tiers, and Google sends them
  to Antigravity CLI. Gemini CLI remains for organizations and API keys, so its extension stays,
  untested with a prompt.
- Antigravity CLI 1.3.4 (installed with winget, `Google.AntigravityCLI`): it validates and
  installs the plugin folder by its root `plugin.json` with the 3 skills, but it reads the servers
  from `mcp_config.json` ("mcpServers: skipped (not found)" with `mcp.json` alone), so the plugin
  carries that file too. `agy plugin import` of the whole repository copies everything in it
  (350 MB with the development environment): the plugin folder is what to install.
- **Antigravity CLI with a real prompt (2026-10-13, signed in by the owner, its default model):**
  a plain request reads the skill `diagrama`, reads the tool descriptions, calls
  `list_components` and `design_and_draw`, and delivers the 9 files with the DWG, in Spanish,
  with the draft warning (about 90 s). It was run headless with `--sandbox` and
  `--dangerously-skip-permissions`, because headless mode refuses every tool it cannot ask about.
  It connected to the published server (v0.5.0 from the git tag) without the handshake problem.
- **The projects landed inside the installed plugin** (`~/.gemini/config/plugins/pv-unifilar/out`):
  these clients start a plugin's server in the plugin's folder (Agent Plugins 1.0: "cwd defaults
  to the plugin root"), and the server's default is `./out`. The portable manifests now set
  `PVSLD_OUTPUT_DIR` to `~/PvUnifilar`; checked again in Antigravity:
  `<home>/PvUnifilar/sfv_2p85kWp`. Claude Code keeps `./out` of the project, where it
  starts the server.

**Status:** In Progress (3.7.1 to 3.7.3 merged, PRs #43 to #45; released as v0.6.0 on 2026-10-13, the first version on PyPI; 3.7.4: real prompts with the published package done in Copilot CLI and Antigravity CLI, the section per client in the user guide in review; open: the MCP registry)

### Stage 3.8: Engine API for the PvUnifilar editor (approved by the owner 2026-10-30)

**Goal:** `pvsld` serves as the engine of the interactive editor (repository
`edu3250/PvUnifilar-modular`): the editor places, connects and evaluates components through public
engine APIs, while the plugin keeps working unchanged.

**Requests:** the editor session sent the P0 requirements ENG-001 to ENG-005 (source:
`docs/engineering/PVSLD_REQUIREMENTS.md` in that repository). Order approved: 001 -> 002 -> 005 ->
004 -> 003. ENG-014 (lightweight core for Pyodide) is P1; its first part (sizing imports without
ezdxf) shipped in PR #51.

**Owner decisions (2026-10-30):**
- The palette holds only the types the engine calculates today; other symbols join when the
  engine does.
- The junction dot gets four anchors at the centre of each edge, in the descriptor only.
- Adding catalogue equipment is a plugin feature; app users cannot, only the owner as administrator.
- Inverter backfeed towards the array is assumed 0 A (see `docs/research/proy-nom-001-sede-2018-art-690.md`).

#### Stage 3.8.1: ENG-001 palette descriptors
**Goal**: `pvsld.palette` builds a versioned document (`pvsld.palette/1`): 15 types with full symbol
geometry, bounds, ports and attributes, the JSON Schema of their spec section, and the reviewed
catalogue equipment with its provenance. Engine-defined types (SPD, interconnection point, load
centre, meter, grid, electrode, junction, terminal, PV connector) carry no manufacturer.
**Success Criteria**: the editor draws each type from the descriptor, and the DXF from the same
`SymbolSpec`; `pvsld palette [--json | --schema]` prints it.
**Tests**: `tests/test_palette.py` (round trip descriptor -> `SymbolSpec` for every type, ports,
anchors, provenance, determinism, schema, CLI); `tests/test_import_footprint.py` covers the module.
**Status**: Complete
**Out of scope**: the variable combiner box (ENG-004) and typed DC+/DC- ports (ENG-012, not approved).

#### Stage 3.8.2: ENG-002 draft evaluation (two PRs, owner 2026-10-30)
**Goal**: `pvsld.core.draft.evaluate_draft` tells an editor what is wrong with a draft and which
rules it could not run. `validate_pv_design` stays strict for the plugin.
- **3.8.2a, classification:** every issue keeps rule id `GEN-001` and gets a category (`missing`,
  `incompatible`, `invalid_structure`, `unsupported`), its path and the component ids it touches.
  Rules not run are listed with the blocking sections. A missing value is never filled in. The
  initial "unsupported" list (more than one inverter, inverter qty > 1, micro/central/optimizer
  inverters, more than two strings, MPPT other than A/B, storage, MT service, transformer) is
  resolved step by step (owner 2026-10-30).
- **3.8.2b, partial evaluation:** each rule declares the sections it reads and runs as soon as they
  are valid; string and voltage rules first (VOLT-001, STR-001, STR-004, STR-007).
**Tests**: `tests/test_draft.py`; the import-footprint test covers the module.
**Status**: Complete (3.8.2a merged in PR #53; 3.8.2b adds `RuleDef.sections`, `calc.derive_strings`, `run_rules(only=...)` and the draft-side string reference check)

#### Stage 3.8.3: ENG-005 manually fixed values
**Goal**: `pvsld.sizing.choices.evaluate_choices(spec, locks)` checks every drawn equipment value
the engine can select (string fuses/breakers, the box switch, ITM-1, ITM-P, conductor sizes,
raceways) without reselecting it, and returns checks, updates (unlocked values) and
recommendations (locked values that pass, informational) as separate lists. A locked value that
fails is a finding with the engine's value as `suggested`, never a replacement. Locks name a
component (`FUS-S1`) or one value (`FUS-S1.ie_a`). The per-device checks of `sizing/bos.py` are
split from the selection, so the plugin and the editor judge with the same code. Owner answers
2026-10-30: both lock levels, recommendations informational, the sizing rule ids.
**Success Criteria**: the engine's own design gives no update; a bad unlocked value fails and gets
an update; a bad locked value fails with `suggested` and is not updated; a locked value that
passes gets a recommendation; no ezdxf at import.
**Tests**: tests/test_choices.py; sizing tests unchanged after the split.
**Status**: Complete (PR #55)

#### Stage 3.8.4: ENG-004 DC protection box contents
**Goal**: `pvsld.sizing.dcbox.dc_box(spec)` sizes and assembles the DC protection box from the
strings alone, so the editor places it ready-made and its parts can be replaced: per string the
connector `CX-`, entry terminal `XE-`, gPV fuse `FUS-` (or DC breaker `DCB-` on request) and exit
terminal `XS-`; shared, the DC SPD `DPS-CD1` and the switch `DCD-CD1` (4 poles in series for one
string, 2 per string). Each part has its palette type, block, ports, poles, values,
requirements, string membership and reason; the box also gives the links between ports, its
protection-schedule rows and its `dc_bos` entries (`apply_dc_box`). Locked drawn values are kept.
Owner answers 2026-10-30: the engine sizes the SPD (Type 2, U_CPV >= Voc max SPD-002, In >= 5 kA
SPD-003, I_SCPV >= 1.25 x Isc SPD-004; no SPD in the catalogue yet, so U_CPV takes the usual
commercial class and In the template's 20 kA, stated as assumptions); fuse by default, breaker
on request; connectors and terminals included.
**Success Criteria**: 1x5 -> 2x5 adds S2's parts and switches DCD-CD1 to 2 poles per string;
removing the string gives back the 1x5 box; the applied spec validates; evaluate_choices finds
nothing to update in a fresh box.
**Tests**: tests/test_dcbox.py.
**Status**: In Progress (PR open)

#### Stage 3.8.5: ENG-003 topology mapping (1 inverter, up to 2 strings first)
**Status**: Not Started

---

**Phase 3 Status Summary:** Paused (owner, 2026-10-07). Open items:
- PR #12 (catalogue batch: Jinko 60HL4, pending owner review, CI intentionally red due to missing staged implementation)
- PR #13 (STR-007 + sizing engine v1, pending owner decisions on scope)
- Stages 3.1–3.6 remain Not Started; do not merge or rebase these branches until Phase 4 is kicked off.

---

## Phase 4: Symbol Library Versioning & Deterministic DXF Regeneration

**Goal:** Create a versioned CFE-compliant symbol library in DXF master file format, replace code-defined symbols with library imports, and establish a deterministic regeneration pipeline. This forms the foundation for diagram generation and layout templates in Phase 5.

**Target Release:** `v0.4.0-symbols`

**Key Decision:** The symbol library is a versioned DXF master file (`symbols/pvsld-symbols.dxf`) that is the single source of truth. The generator imports blocks from it instead of code-defined geometry. A legend sheet documents each symbol's CFE G0100-04 source. A regeneration script ensures the library is deterministic and CI-testable.

---

### Stage 4.1: Symbol Library Specification & ADR-0005

**Goal:** Document the design of the CFE-compliant symbol library and the code-to-file transition. Record the decision, library format, block naming, attributes, metadata, and regeneration strategy in ADR-0005.

**Success Criteria:**

- ADR-0005 (proposed) documents:
  - **Library as DXF master:** `symbols/pvsld-symbols.dxf` is the single source of truth; blocks are imported by the generator, not code-defined
  - **Block naming and attributes:** Comply with ADR-0003 (PVSLD_<FUNCTION>, standard attributes TAG/DESC/COMP_ID/IEC_REF/NMX_REF, function-specific attributes with units)
  - **Symbol sourcing:** Each block records its source in XDATA or hidden attributes:
    - **CFE G0100-04 Appendix C symbols (13 source symbols):** Módulo fotovoltaico, Varistor, Interruptor termomagnético, Inversor, Medidor de energía, Red eléctrica de distribución, Gabinete, Diodo de paso, Cargas de iluminación, Sensor de corriente, Interruptor manual, Transformador de aislamiento, Carga de contactos
    - **Supplementary (CFE diagrams D1/D2 and NMX-J-136-ANCE / IEC 60617):** Earth/ground symbol, conductor-count marks (//), polarity (+/−), Detector de falla a tierra, fuse (IEC 60617-2), DC disconnect (IEC), battery (IEC), SPD (IEC or varistor variant)
  - **Legend sheet (layout):** Símbolo | Designación | Fuente, listing all blocks with CFE/IEC/NMX reference
  - **Regeneration:** A deterministic Python script (`scripts/regenerate_symbol_library.py`) that can rebuild the DXF from a canonical definition (YAML or Python), with bit-for-bit reproducibility across runs and platforms
  - **DWG export:** Core Console finishes the library DXF to DWG 2018 (produced once, committed, CI validates it exists and audits 0)
  - **Golden tests:** How the golden DXF test changes when the library regenerates (validation hook in CI, reviewed by owner)

- ADR-0005 is written in vault and mirrored to `docs/decisions/ADR-0005-cfe-symbol-library.md`
- Appendix C source crop images placed in `datasheets/cache/cfe_appendix_c/` with attribution notes (git-ignored)
- A decision summary page in the vault notes the 13 CFE symbols and which IEC/NMX standards back the gaps

**Tests:**

- ADR document is well-formed markdown with clear rationale for the library-as-file approach
- Comparison: code-defined (current) vs. file-based (proposed) shows the trade-offs (single source of truth, version control, visual review)
- References to CFE G0100-04 Appendix C and IEC 60617 are correct
- Owner approves the decision before Stage 4.2 begins

**Status:** Complete (ADR-0005 accepted)

---

### Stage 4.2: Build CFE Symbol Set as Blocks in DXF + Legend Sheet

**Goal:** Create the master DXF file with all required symbols as blocks, a legend layout, and metadata. All symbols are drawn from CFE G0100-04 Appendix C first, then NMX-J-136-ANCE and IEC 60617 for gaps. The file is deterministic and passes `doc.audit()` with 0 errors.

**Success Criteria:**

- **Master DXF created:** `symbols/pvsld-symbols.dxf` (R2018 format, UTF-8)
- **Blocks implemented (≥17):**
  - **CFE G0100-04 Appendix C (13):** PVSLD_PV_STRING, PVSLD_VARISTOR, PVSLD_CB (inverter-side and string-side variants if needed), PVSLD_INV (box + diagonal, "=" upper-left, "~" lower-right per Appendix C figure), PVSLD_METER (box with ↔ over kWh), PVSLD_GRID (circle with ~), PVSLD_STRUCTURE (dash-dot rectangle), PVSLD_DIODE_BYPASS, PVSLD_LOAD_LIGHT, PVSLD_CT (current sensor), PVSLD_SW_MANUAL, PVSLD_TRAFO_ISO, PVSLD_LOAD_CONTACT
  - **Supplementary (≥4):** PVSLD_GND (earth/ground symbol, IEC 60617), PVSLD_SPD (surge protector / varistor variant, IEC 60617 or CFE if defined), PVSLD_DC_DISCONNECT (IEC 60617), PVSLD_FUSE (IEC 60617)
  - **Existing (redrawn to match CFE aesthetics):** PVSLD_TTLB (title block, Mexican fields), PVSLD_PANEL (conductor or panel placeholder), others as required
- **Block attributes:**
  - Visible: TAG, DESC (Spanish)
  - Hidden: COMP_ID, IEC_REF, NMX_REF
  - Function-specific with units: RATING_A, VOLT_V, PMAX_W, etc. (per device type)
  - Hidden attribute `SOURCE_STANDARD` (e.g., "CFE G0100-04 Appendix C", "IEC 60617-2", "NMX-J-136-ANCE") or XDATA app `pvsld` record format 3 with source metadata
- **XDATA per ADR-0003:** Block record, INSERT, and conductor formats; library version semantic version (e.g., `0.4.0`)
- **Legend layout:** Paper-space layout named `Legend` with:
  - Table / structured list: Símbolo (block thumbnail or INSERT), Designación (DESC), Fuente (SOURCE_STANDARD)
  - Title block or header: "pvsld Symbol Library v0.4.0 – CFE G0100-04 Appendix C & IEC 60617"
  - Suitable for A3 or A4 printout
- **Audit result:** `doc.audit()` returns (0 errors, 0 fixes)
- **Regeneration script:** `scripts/regenerate_symbol_library.py`
  - Input: canonical definition (YAML file `symbols/cfe_symbols_0.4.0.yaml` or inline Python dataclass)
  - Output: deterministic DXF (same bytes across runs, Windows/Linux/macOS)
  - Validation: script is idempotent; regenerating the library produces the same file hash
  - Test: CI runs the regeneration, compares file hash, and confirms 0 changes (or detects intentional edits)

**Tests:**

- `test_library_audit_clean`: `doc.audit()` returns (0, 0)
- `test_all_blocks_have_required_attributes`: every block has TAG, DESC, COMP_ID, IEC_REF, NMX_REF (hidden), and at least one function-specific attribute (if applicable)
- `test_block_ports_defined`: each block has ports in XDATA format per ADR-0003
- `test_legend_layout_renders`: legend layout exists and all block references are valid
- `test_regeneration_idempotent`: running the regeneration script twice produces identical file hashes
- `test_library_cross_platform`: CI runs regeneration on Windows and Linux; hashes match
- Manual inspection (owner): Open the DXF in AutoCAD, visually verify each symbol matches Appendix C or IEC standard, audit 0/0

**Status:** Complete (released as `v0.4.0-symbols`, 2026-10-08)

**As built (deviations from the plan above):**

- The canonical definition is a Python module (`src/pvsld/symbols/cfe/definitions.py`), not YAML; the builder is `pvsld symbols build` (`scripts/build_symbol_library.py` wraps it). The library DXF is `symbols/pvsld-symbols.dxf`, version 0.4.0.
- 22 blocks in v0.4.0 and 56 in v0.5.0 (round 2: the 28 components of the symbol map plus the IEC thermomagnetic breaker and the RCD; three Legend layouts by family). v0.4.0 blocks: the 13 Appendix C symbols (names follow ADR-0003: `PVSLD_PV_MODULE`, `PVSLD_SPD`, `PVSLD_CB`, `PVSLD_INV`, `PVSLD_METER`, `PVSLD_GRID`, `PVSLD_ENCLOSURE`, `PVSLD_DIODE`, `PVSLD_LOAD_LIGHT`, `PVSLD_CT`, `PVSLD_SWITCH`, `PVSLD_XFMR_ISO`, `PVSLD_LOAD_RECEPT`), `PVSLD_GND`, `PVSLD_COND_MARK`, `PVSLD_POL_POS`, `PVSLD_POL_NEG`, `PVSLD_JUNCTION`, and the Phase 2 blocks `PVSLD_PV_STRING`, `PVSLD_PANEL`, `PVSLD_PI`, `PVSLD_TTLB`.
- Source priority CFE G0100-04 > NMX-J-136-ANCE-2019 > IEC 60617 (through the DGE norm and the UNE-EN 60617 sheet); every block records the clause, figure or code. `PVSLD_DC_DISCONNECT` and `PVSLD_FUSE` were added in v0.5.0. The inverter attribute `CERT` is dropped.
- ezdxf's `Importer` does not copy block-record XDATA (the ports); `pvsld.symbols.cfe.loader.import_blocks` does, and Stage 4.3 should use it instead of `blocks.import_blocks`.
- v0.6.0 (57 blocks), owner decisions of the v0.5.0 review (2026-10-08): the generator uses the CFE breaker `PVSLD_CB`; `PVSLD_FUSE_AC` added; the combiner box follows the string count, so Stage 4.3 calls `loader.ensure_combiner(doc, n)` (block `PVSLD_COMBINER_<n>S`, inputs `IN1`..`INn`) instead of inserting the two-string `PVSLD_COMBINER`.


---

### Stage 4.3: Generator Transition – Import from Library DXF

**Goal:** Modify the diagram generator to import blocks from the library DXF instead of code-defined symbols. Remove code-defined symbol geometry. Validate that diagrams render identically. Regenerate and review the golden test DXF.

**Success Criteria:**

- **Generator code changes:**
  - `src/pvsld/backends/ezdxf_backend.py` refactored to:
    - Load the library DXF (`symbols/pvsld-symbols.dxf`)
    - Import block definitions via `dwg.blocks.import_blocks(library_dwg)` or equivalent
    - Insert blocks by reference, no code-defined geometry
  - Remove `src/pvsld/symbols/catalogue.py` or refactor to a library loader; remove old block definitions
  - Remove the CERT hidden attribute from PVSLD_INV (owner requirement; certification scope is out of Phase 4)
- **Golden test regeneration:**
  - Run `tests/test_spike_s1.py` with the new generator
  - The output DXF will differ from the old golden (block definitions now imported, possibly slight geometric differences from CFE-based redraw)
  - Owner visually reviews the new DXF in AutoCAD; confirms it renders correctly and passes AUDIT 0/0
  - Commit the new golden DXF with a reviewed flag in the commit message (e.g., "Approved by owner, 2026-10-07")
  - Update the test to use the new golden file
- **Rendering parity:** 
  - `test_diagram_renders_same`: the same parametric spec (`examples/residential_7p7kwp.yaml`) rendered with the new library produces a valid, audit-clean DXF
  - Attribute round-trip: all COMP_ID and function-specific attributes match the model
  - Port connectivity: 0 dangling ports, all conductors reach their intended ports
- **CI integration:** Golden test passes on Windows and Linux; artifact upload of the new golden for owner review (optional)

**Tests:**

- `test_ezdxf_backend_imports_library`: library DXF is successfully loaded and blocks are imported
- `test_golden_regeneration_audit_clean`: regenerated golden passes AUDIT 0/0
- `test_golden_attribute_round_trip`: all 147+ attributes in the example round-trip correctly
- `test_no_dangling_ports`: 0 dangling ports in regenerated golden
- `test_no_code_defined_symbols`: grep confirms no references to old symbol definitions in the backend code
- Manual (owner): Open regenerated golden in AutoCAD, visually confirm it looks correct, run AUDIT

**Status:** Complete (released as `v0.4.0-symbols`, 2026-10-08)

**As built (deviations from the plan above):**

- The backend is `src/pvsld/backends/dxf.py`; `define_symbol_blocks` imports the used blocks, in sorted order, from `loader.library_document()` with `loader.import_blocks` (port XDATA included). `pvsld.symbols` is now the interface over the CFE library (`SYMBOLS`, `get_symbol`, `symbol_catalogue`); `src/pvsld/symbols/catalogue.py` and `render_symbol_library()` are gone. The nine Phase 2 blocks keep their tags and port ids (frozen as `PHASE2_CONTRACT` in `tests/test_cfe_definitions.py`).
- `get_symbol("PVSLD_COMBINER_<n>S")` resolves the combiner for `n` strings and the backend defines it with `ensure_combiner`; the residential template has no combiner yet, so this is covered by unit tests.
- Every instance also carries `SOURCE_STANDARD`; `CERT` is gone (157 attributes in the sample instead of 147). The symbology table cites the short source (`CFE G0100-04 Ap. C`).
- Layout: the CFE inverter is 50 mm wide with its DC ports at ±10 mm, so the inverter moved 5 mm left and the string wires bend 7.5 mm before it (clear of the string callouts); the meter description is `Medidor MF` (the legend spells it out). The golden `tests/golden/residential_7p7kwp.dxf` was regenerated.

---

### Stage 4.3b: Owner's sheet template (`a3_plantilla_v1`)

**Goal:** Draw every design on the owner's A3 sheet template, its content adapted to the design, with no personal data.

**Status:** Complete (released as `v0.4.0-symbols`, 2026-10-08)

- `pvsld sheet import` turns the owner's DWG into a neutral template DXF. `sheet_templates/` is git-ignored until the owner approves publishing it.
- `layout_sheet.build_sheet_diagram` fills the 76 fields. Location, address, installer, owner and licence fields stay blank; the title block reads "COMPAÑÍA INSTALADORA".
- The schematic placement is shared with v1 through `layout.Placement` and `build_schematic`; the v1 golden is unchanged.
- Not in the model yet: altitude, inverter apparent power (VA), module type/dimensions, inverter certifications, estimated energy (blank or "—").

---

### Stage 4.4: DWG Export & Library Finalization via Core Console

**Goal:** Convert the library DXF to a DWG 2018 using Core Console, finalize the library, and prepare for diagram generation in Phase 5.

**Success Criteria:**

- **Core Console finisher output:**
  - `symbols/pvsld-symbols.dwg` (TrustedDWG 2018, produced via `pvsld finish` on owner's licensed workstation)
  - Audit result (automated headless check): 0 errors, 0 fixes
  - File metadata: "last saved by" field cleared (privacy per owner request)
- **Library committed:**
  - `symbols/pvsld-symbols.dxf` (source, R2018)
  - `symbols/pvsld-symbols.dwg` (finalized, TrustedDWG 2018)
  - Both files committed to the repository with README pointing to each
- **CI validation:**
  - Script checks that the DWG exists and is TrustedDWG 2018 format
  - Automated headless AUDIT against the DWG (if Core Console is available in CI; if not, manual check by owner)
  - Warning if DWG is stale (commits to DXF without updating DWG)
- **Phase 4 version & release:**
  - `CHANGELOG.md` updated with Phase 4 summary (library sourced from CFE, blocks imported, deterministic regeneration)
  - Tag `v0.4.0-symbols` on main (via GitFlow release branch)

**Tests:**

- `test_library_dwg_exists`: file `symbols/pvsld-symbols.dwg` exists
- `test_library_dwg_is_trustedacad`: DWG header confirms TrustedDWG and AC1032 (2018)
- `test_library_dwg_audit_clean`: if headless AUDIT available, (0, 0); otherwise manual check recorded
- Manual (owner): Open DWG in AutoCAD, AUDIT, confirm 0/0; review metadata privacy

**Status:** Complete (released as `v0.4.0-symbols`, 2026-10-08)

**As built:** the DWG is `symbols/pvsld-symbols.dwg` (owner request: a plain library name), exported by `pvsld symbols dwg` through the finisher. Staleness is checked offline with `symbols/pvsld-symbols.dwg.json` (DXF SHA-256, library version) and `pvsld symbols dwg --check`. AutoCAD writes the Windows login name as "last saved by" and it cannot be removed without breaking TrustedDWG. No local paths are stored.

---

### Stage 4.5: Extend Symbol Library – Battery, Transformer, Fuse, SPD, DC Disconnect

**Goal:** Extend the CFE symbol set with additional symbols needed for diverse topologies (hybrid/battery systems, transformer configurations, advanced protection). Maintain the CFE-first approach and deterministic regeneration.

**Success Criteria:**

- **New symbols added (≥5):**
  - PVSLD_BATTERY (NMX or IEC, if CFE does not define)
  - PVSLD_TRAFO (isolation transformer, distinct from PVSLD_TRAFO_ISO if both are needed; determine from topologies in Phase 5)
  - PVSLD_FUSE (DC-rated fuse, class gPV per NMX-J-136-ANCE; PVSLD_FUSE_DC or variant)
  - PVSLD_SPD (surge protection device, distinct from varistor; determine from Phase 3 rule PROT-NNN requirements)
  - PVSLD_DC_DISCONNECT (if not complete in Stage 4.2)
  - Conductor markers (// for multi-conductor, +/− for polarity) as block symbols or documented as text/line styles
- **Library regeneration updated:** `scripts/regenerate_symbol_library.py` includes new symbols; canonical definition (`symbols/cfe_symbols_0.4.0.yaml`) is extended
- **Audit and test:** New blocks pass the same AUDIT, attribute, and port tests as Stage 4.2
- **Owner review:** Each new symbol visually verified in AutoCAD and traced to its source (NMX, IEC, or CFE)

**Tests:**

- `test_library_audit_clean` (rerun): extended library still audits 0/0
- `test_new_symbol_attributes`: each new block has required attributes
- `test_new_symbol_ports`: new blocks have sensible port definitions (if applicable)
- Manual (owner): Inspect each new symbol in AutoCAD

**Status:** Not Started

---

## Phase 5: Layout Templates & Diagram Rendering for Diverse Topologies

**Goal:** Implement layout templates for common PV configurations (single string, multi-string, multi-inverter, three-phase, battery), integrate with Phase 3's parametric sizing engine, and produce PDF/DWG exports via Core Console.

**Proposed scope (Phase 4 completion triggers Phase 5 planning):**

- **Layout templates:** Beyond `bt_string_residential_v1`, add templates for 2–4 strings, multiple MPPTs, several inverters, three-phase, hybrid/battery, and later microinverters
- **Deterministic diagram generation:** Generator selects layout template based on parametric spec; Claude elicits trade-offs and approves
- **DWG & PDF finalization:** Core Console export with batching, non-ASCII path support, and metadata privacy
- **Diagram annotations:** Integration of Phase 3's `get_diagram_summary` for system kWp, inverter, string configuration labels

**Status:** To be defined (depends on Phase 4 completion; owner confirmation pending)

**Estimated Stages:** 5.1–5.4

---

## Phase 5: Validation, Plugin Packaging & Production Release

**Tentative Intent:** Validate diagram output against Mexican electrical standards and professional reviewers. Harden and distribute the Claude Code plugin started in Stage 3.6 (signed .NET plug-in if needed, public marketplace or GitHub Releases). Release `v1.0.0`.

**Proposed scope (Phase 2 review, tentative, pending Phase 3 and Phase 4 completion):**

- **Regulatory validation (trigger T3, open item Z1):** Gather feedback from UVIE inspection units and CFE on whether DXF and ezdxf PDFs are accepted, or if native DWG and AutoCAD PDFs are required. This feeds the plugin packaging decision.
- **Package validator:** Implement TOP and DRW rules (post-drawing checks), validator for MX-I01…I07, and end-to-end test suite that exercises all diagram types.
- **Claude Code plugin packaging:** Bundle the MCP server (`pvsld-mcp` executable), the configuration, and optional frontend skill/slash command into a `.claude-plugin` package compatible with the Claude Code plugin system. Decide whether to include the .NET plug-in (Phase 4 B2) in the distribution: if regulatory feedback requires native DWG, include it with a signed `.bundle` and installer; otherwise, distribute B1 (ezdxf) only. Host the plugin on a public registry or GitHub Releases.
- **Distribution variants:** 
  - **v1.0.0-core**: MCP server + ezdxf B1, no AutoCAD plug-in, works on any OS with Python 3.11+
  - **v1.0.0-plugin** (conditional): includes the signed .NET 10 plug-in + B2 backend (Windows only), requires AutoCAD 2027
  - Documentation in Spanish and English
- **Claude Desktop support (optional):** Test a full end-to-end run on Claude Desktop (150k character limit, 240 s timeout).

**Status:** To be defined (depends on Phase 4 and regulatory feedback; owner confirmation pending)

**Estimated Stages:** 5.1–5.4

---

**Last updated:** 2026-10-07 (Phase 4 kickoff: symbol library versioning & deterministic DXF regeneration)
**Repository:** https://github.com/edu3250/PvUnifilar
