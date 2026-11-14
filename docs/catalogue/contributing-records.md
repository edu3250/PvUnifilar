# Adding components to the catalogue (guide for contributing models)

This guide tells an AI model, or a person, how to add one equipment family to the PvUnifilar
catalogue with one pull request. Follow it **literally**: every step exists so the reviewer can check
your work in minutes. A pull request that skips a step is corrected or closed.

The second half ("Reviewer procedure") is for the reviewer. Read it too: it tells you exactly what
will be checked.

## 0. What you are building

- One **record** = one YAML file = one datasheet family, in
  `datasheets/records/<folder>/<family-id-lowercase>.yaml`.
- A family has shared values (dimensions, coefficients, certifications…) and a list of
  **variants**, one per model or power level, each with its own `component_id`.
- The design engine sizes real electrical installations from these numbers. **A wrong number is a
  safety problem.** Copy values exactly as printed; never estimate, round or "fix" a value.
- The datasheet PDF is copyrighted: it is **never committed**. It stays on this machine, in folders
  that git ignores, so the reviewer can open it.

### Equipment types open for contributions

| Stage | Type (`component_type`) | Folder | Status |
|---|---|---|---|
| 1 | `pv_module` | `modules` | **open** (pilot: the first 10 pull requests) |
| 2 | `string_inverter` | `inverters` | closed until this guide has its section |
| 3 | `conductor` | `conductors` | closed |
| 4 | `dc_fuse`, `dc_breaker`, `ac_breaker`, `dc_switch`, `dc_spd` | `protection` | closed |
| 5 | `microinverter` | `inverters` | closed |

Only take issues of an **open** type.

## 1. Pick a family from the queue

Every family to add has an issue labelled `catalogue-queue`. Work only on those issues.

```bash
gh issue list --repo edu3250/PvUnifilar --label catalogue-queue --search "-label:claimed" --limit 20
```

Choose one issue, then claim it so no other model takes it (replace `<N>` and `<model>`):

```bash
gh issue edit <N> --repo edu3250/PvUnifilar --add-label claimed
gh issue comment <N> --repo edu3250/PvUnifilar --body "Claimed by <model> (<tool>)."
```

`<model>` is the exact model name you run as, for example `gemini-2.5-flash` or `gpt-5-mini`, and
`<tool>` the program, for example `copilot-cli` or `opencode`. Use the same spelling every time:
your performance is measured by it.

If you give up, release the issue: `gh issue edit <N> --repo edu3250/PvUnifilar --remove-label claimed`
and comment why.

## 2. Prepare your working copy

All commands run in **Git Bash**. Start from the main checkout of the repository
(`D:\DataScience\Diagrama-unifilarAutoCAD`) and create your own worktree. `<fam>` is the family id
in lowercase, for example `longi-lr5-54hth`.

```bash
cd /d/DataScience/Diagrama-unifilarAutoCAD
git fetch origin
git worktree add .worktrees/cat-<fam> -b catalogue/<fam> origin/develop
cd .worktrees/cat-<fam>
MAIN=/d/DataScience/Diagrama-unifilarAutoCAD
pvsld() { PYTHONPATH=src "$MAIN/.venv/Scripts/python.exe" -c "import sys; from pvsld.cli import main; sys.exit(main(sys.argv[1:]))" "$@"; }
```

Define the `pvsld` function again in every new shell. Never run `pip install` in the main `.venv`.

## 3. Get the official datasheet

1. Find the datasheet on the **manufacturer's own website** (for example `jinkosolar.com`,
   `longi.com`, `trinasolar.com`). If the issue gives a URL, use it.
2. Not accepted: distributors, shops, marketplaces, PDF mirrors, Google Drive or Dropbox links,
   or any page that is not the manufacturer's. If you cannot find an official PDF, comment on the
   issue, remove the `claimed` label and stop.
3. Prefer the most recent datasheet that covers **IEC** values. If there are regional versions,
   prefer the one for Mexico or Latin America, then the global one.
4. Download it into the shared inbox of the main checkout, in a folder named after the family:

```bash
mkdir -p "$MAIN/datasheets/inbox/catalogue/<fam>"
curl -L --fail -o "$MAIN/datasheets/inbox/catalogue/<fam>/<file>.pdf" "<official url>"
sha256sum "$MAIN/datasheets/inbox/catalogue/<fam>/<file>.pdf"
```

Keep the file name the manufacturer used. `datasheets/inbox/` is ignored by git: **never** add the
PDF or images of it to a commit, a pull request, a comment or an issue.

## 4. Read the datasheet

```bash
pvsld catalogue render "$MAIN/datasheets/inbox/catalogue/<fam>/<file>.pdf" --pages 1 2 \
  --out "$MAIN/datasheets/cache/review/<fam>"
```

This writes `page-1.png`, `page-2.png`… Look at the pages that hold the electrical and mechanical
tables; these page numbers go into `source.pages`. If you can, also read the PDF text layer; when the
two disagree, the rendered page wins.

## 5. Write the record

Create `datasheets/records/modules/<fam>.yaml`. Start from this complete example (a real record,
shortened to two variants) and replace **every** value:

```yaml
# Facts from the manufacturer datasheet (the PDF is not redistributed). Family + one variant per power level.
component_type: pv_module
family_id: JINKO-JKM-60HL4
manufacturer: Jinko Solar
model_family: JKM460-480N-60HL4-(V) Tiger Neo N-type
cell_type: N-type mono-crystalline
cells: 120
bifacial: false
dimensions_mm: [1903, 1134, 30]
weight_kg: 24.2
frame: anodized aluminium alloy
max_system_voltage_v: 1000
max_series_fuse_a: 25
operating_temp_c: [-40, 85]
noct_c: 45
power_tolerance_pct: [0, 3]
temp_coef_pmax_pct_per_c: -0.30
temp_coef_voc_pct_per_c: -0.25
temp_coef_isc_pct_per_c: 0.046
certifications: [IEC 61215 (2016), IEC 61730 (2016)]
variants:
  - {component_id: JINKO-JKM460N-60HL4, pmax_w: 460, vmp_v: 34.72, imp_a: 13.25, voc_v: 42.05, isc_a: 13.99, efficiency_pct: 21.32, noct: {pmax_w: 346, vmp_v: 32.60, imp_a: 10.61, voc_v: 39.94, isc_a: 11.29}}
  - {component_id: JINKO-JKM465N-60HL4, pmax_w: 465, vmp_v: 34.89, imp_a: 13.33, voc_v: 42.22, isc_a: 14.07, efficiency_pct: 21.55, noct: {pmax_w: 350, vmp_v: 32.77, imp_a: 10.67, voc_v: 40.10, isc_a: 11.36}}
source:
  filename: JKM460-480N-60HL4-(V)-F1-EN.pdf
  url: https://<manufacturer-domain>/<path>/JKM460-480N-60HL4-(V)-F1-EN.pdf  # where you downloaded it
  sha256: ac1ace5685a715abd734ae1e331481d6b82656f785507ba2e805d638cff63efd
  title: JKM460-480N-60HL4-(V)-F1-EN (IEC 2016), © 2021 Jinko Solar
  pages: [2]
  extraction_method: rendered_page
  extraction_date: 2026-11-14
  reviewed_by: null
  review_date: null
  notes: >-
    The datasheet states "1000/1500VDC"; the record keeps the conservative 1000 V.
```

### Rules for every field

**Identifiers**

- `family_id`: `MANUFACTURER-SERIES` in capitals, letters, digits and `-` only, for example
  `LONGI-LR5-54HTH`, `TRINA-TSM-NEG9R-28`. The file name is `family_id` in lowercase plus `.yaml`.
- `component_id`: the family prefix plus the exact model code of that power level, in capitals, for
  example `LONGI-LR5-54HTH-430M`. One variant per power level printed in the table; leave none out
  and add none that is not printed.
- `manufacturer`: the brand as commonly written (`LONGi`, `Trina Solar`, `JA Solar`). Check the
  existing records (`ls datasheets/records/modules`) and reuse the same spelling for a brand that is
  already there.
- `model_family`: the series name as printed on the datasheet.

**Electrical values (variants)**

- `pmax_w`, `vmp_v`, `imp_a`, `voc_v`, `isc_a`, `efficiency_pct`: the **STC** row (1000 W/m²,
  25 °C, AM 1.5), exactly as printed, all decimals.
- `noct`: the NOCT (or NMOT) row when printed; then `noct_c` is required. If the sheet prints NMOT
  instead of NOCT, use it and say so in `source.notes`.
- Bifacial modules: `bifacial: true`, `bifaciality_pct` (`pmax`, `voc`, `isc` keys when printed, or
  only `pmax`) and `bnpi` with the values at bifacial nameplate irradiance when printed. The STC
  row is still the front-side value.
- List variants from the lowest to the highest `pmax_w`.

**Family values**

- `dimensions_mm`: `[length, width, thickness]` in millimetres.
- `max_system_voltage_v`: the IEC value. If the sheet prints two (`1000/1500 V`), use the one that
  applies to all variants; if unsure, the lower one, and explain in `source.notes`.
- `max_series_fuse_a`: "maximum series fuse rating".
- `operating_temp_c`: `[min, max]`.
- `power_tolerance_pct` **or** `power_tolerance_w`, matching the unit printed (`0~+3%` →
  `power_tolerance_pct: [0, 3]`; `0~+5W` → `power_tolerance_w: [0, 5]`).
- Temperature coefficients in %/°C with their sign: Pmax and Voc negative, Isc positive.
- `certifications`: as printed; `[]` when the sheet lists none.
- Optional text fields (`frame`, `front_cover`, `junction_box`, `connector`, `cable_mm2`): only when
  printed. Omit a field rather than guessing.

**Provenance (`source`)**

- `filename`: the PDF file name exactly. `url`: the official address you downloaded it from.
- `sha256`: the output of `sha256sum`.
- `title`: the document code, revision and date as printed (footer or header).
- `pages`: the pages that hold the values you copied.
- `extraction_method`: `pdf_text_layer` if you copied from the text, `rendered_page` if you read
  the page images.
- `extraction_date`: today. `reviewed_by` and `review_date`: **always `null`**; only the reviewer
  sets them.
- `notes`: every deduction or choice you made (which voltage, NMOT instead of NOCT, a suffix that
  does not change the values…). Write "none" only if there were none.

**Never**

- Never invent a value, copy it from another datasheet, a shop page or your memory.
- Never change any file other than your one record.
- Never edit tests, code, other records or this guide.

## 6. Check your record

```bash
pvsld catalogue verify datasheets/records/modules/<fam>.yaml \
  --pdf "$MAIN/datasheets/inbox/catalogue/<fam>/<file>.pdf" \
  --markdown "$MAIN/datasheets/cache/review/<fam>/verify.md"
```

- **FAIL**: fix every problem and run it again. Do not open a pull request with a FAIL.
- **Warnings**: fix them or explain them in the pull request.
- **Values not found in the text layer**: normal for scanned or outlined datasheets. Look at each one
  again on the rendered page; if a value is right, list it in the pull request table as checked on
  the page image.

The verifier also runs the schema rules: `vmp_v × imp_a` within 2 % of `pmax_w`, Vmp below Voc, Imp
below Isc, efficiency within 0.3 points of `pmax_w / area`, Voc below the maximum system voltage and
Isc below the series fuse. A failure of these rules almost always means a copied digit is wrong.

## 7. Commit, push and open the pull request

```bash
git add datasheets/records/modules/<fam>.yaml
git status --short   # must show exactly one file
git commit -m "feat(catalogue): add <Manufacturer> <series> (<N> variants)" \
  -m "Datasheet <title>. Drafted by <model> via <tool>; pending review."
git push -u origin catalogue/<fam>
```

Write the pull request body from `.github/PULL_REQUEST_TEMPLATE/catalogue.md` into a file outside
the repository, fill every section, then:

```bash
gh pr create --repo edu3250/PvUnifilar --base develop --head catalogue/<fam> \
  --title "feat(catalogue): add <Manufacturer> <series>" \
  --body-file "$MAIN/datasheets/cache/review/<fam>/pr.md" \
  --label catalogue --label "model:<model>"
```

If the label `model:<model>` does not exist yet, create it first:
`gh label create "model:<model>" --repo edu3250/PvUnifilar --color BFD4F2`.

The continuous integration check `test_catalogue_review_gate` **fails on purpose** while
`reviewed_by` is null. That is expected; do not try to fix it.

Your work ends here. Do not merge, do not approve, do not set `reviewed_by`. Comment on the issue
with the pull request link.

## 8. Trusted models (automatic approval)

A model becomes **trusted** when it has at least 20 merged pull requests with label
`corrections:0` and none rejected. The reviewer lists trusted models in
`docs/catalogue/trusted-models.md`. A trusted model, and only for a pull request whose verifier
report says **"Fully confirmed by the text layer: yes"**, may finish its own pull request:

1. set `reviewed_by: auto:<model>` and `review_date` to today, commit and push;
2. add the label `auto-approved`;
3. when every CI check is green: `gh pr merge <PR> --repo edu3250/PvUnifilar --merge --delete-branch`.

Any other case (a value not found in the text layer, a warning, a page without text) goes through
the normal review. The reviewer audits one in five automatically approved pull requests; one error
removes the model from the trusted list and its count starts again from zero.

---

## Reviewer procedure

This part is for the reviewer (Claude, delegated by the repository owner on 2026-11-14 to review and
merge catalogue pull requests without the owner's per-PR approval).

1. **Scope.** The pull request changes exactly one file in `datasheets/records/<folder>/`, of an open
   type. Anything else: ask for it to be split or close it.
2. **Verifier.** Run `pvsld catalogue verify` on the PR branch with the PDF in
   `datasheets/inbox/catalogue/<fam>/`. Compare with the report in the pull request.
3. **Source.** The URL is on the manufacturer's own domain and downloads the same file (same
   SHA-256). Download only from manufacturer domains.
4. **Values.** Open the rendered pages (`datasheets/cache/review/<fam>/page-*.png`; render them if
   missing) and check every value the verifier did not find, plus a spot check of the found ones
   (small integers can match by chance). Check the variant list against the table: none missing,
   none extra. Check the deductions in `source.notes`.
5. **Close without merging** (label `rejected`) when: the URL is not official, the SHA-256 does not
   match, a model is not in the datasheet, or there are more than 3 wrong values.
6. **Otherwise correct** the wrong values in a commit on the PR branch and list each correction in
   a review comment (`field: was X, datasheet prints Y (page N)`).
7. **Mark reviewed**: `reviewed_by: claude`, `review_date` today, commit `chore(catalogue): review
   <family>`. If a test breaks because the new record changes an "auto" selection, pin the family
   in that test (local guide section 6), in the same pull request.
8. **Label** the result: `corrections:0`, `corrections:1-3` (or `rejected`). Merge with
   `gh pr merge <PR> --merge --delete-branch --match-head-commit <sha>` once CI is green, and close
   the queue issue if the PR did not.
9. **Measure** on request: count merged/rejected pull requests per `model:*` label and their
   `corrections:*` labels with `gh pr list --label catalogue --state all --json number,labels`.
