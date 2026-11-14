<!-- Catalogue pull request. Guide: docs/catalogue/contributing-records.md. Fill every section. -->

## Component

- Queue issue: Closes #<N>
- Type: `pv_module`
- Family: `<FAMILY-ID>` — <Manufacturer> <series as printed>
- Record: `datasheets/records/modules/<fam>.yaml`
- Variants: <count> (`<first component_id>` … `<last component_id>`)

## Datasheet

- Official URL: <https://…>
- Local file: `datasheets/inbox/catalogue/<fam>/<file>.pdf`
- SHA-256: `<64 hex>`
- Title / revision: <as printed>
- Pages used: <n, n>
- Rendered pages: `datasheets/cache/review/<fam>/page-<n>.png`

## Who drafted it

- Model: `<model>` (same spelling as the `model:<model>` label)
- Tool: `<copilot-cli | opencode | antigravity | …>`

## Verifier report

<!-- Paste datasheets/cache/review/<fam>/verify.md here, unchanged. It must say "Result: PASS". -->

## Values checked on the page image

<!-- Every value the verifier did not find in the text layer, checked by eye on the rendered page.
     Write "none" when the report is fully confirmed. -->

| Field | Value | Page | As printed |
|---|---|---|---|
| `variants[<ID>].voc_v` | 49.26 | 2 | `49.26` |

## Deductions and choices

<!-- The same text as source.notes, one line each: which voltage you kept and why, NMOT instead of
     NOCT, suffixes with identical values… Write "none" if there were none. -->

## Checklist

- [ ] Exactly one file changed: the record.
- [ ] The URL is on the manufacturer's own website; the PDF is not committed anywhere.
- [ ] Every power level printed in the table is a variant; no extra variants.
- [ ] Values copied exactly as printed (STC row; NOCT/NMOT and BNPI when printed).
- [ ] `reviewed_by` and `review_date` are `null`.
- [ ] `pvsld catalogue verify` says PASS; warnings fixed or explained above.
- [ ] Labels `catalogue` and `model:<model>` added.
