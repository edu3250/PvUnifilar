# PROY-NOM-001-SEDE-2018, Article 690: notes for the engine

Read on 2026-10-30 from the owner's copy of *Anteproyecto de Norma Oficial Mexicana
NOM-001-SEDE-2018* (PDF pages 804-818: Article 690 and Article 691). The PDF is not redistributed.

**Status.** The draft was never published as a standard. The legal reference of the engine and the
rule pack stays **NOM-001-SEDE-2012**. These notes record what the draft says, as technical support
and as a list of what would change if a revision along these lines is published.

## String overcurrent protection (690-9)

- **690-9(a).** PV DC circuits are current-limited. The draft's note says they need overcurrent
  protection only when they are connected in parallel to higher-current sources. The exception
  drops the device for modules and PV source circuits sized per 690-8(b) when either condition holds:
  1. there are no external sources (parallel source circuits, batteries, or backfeed from inverters);
  2. the short-circuit current of all sources does not exceed the conductor ampacity or the
     module's maximum overcurrent device rating on its nameplate.
- **Consequence for 1 or 2 strings per MPPT.**
  - One string per input: condition 1 applies.
  - Two strings in parallel on one input: condition 2 applies, because `1 x 1.25 x Isc` stays
    below the module's maximum series fuse.
  - This is the engine's test `(N - 1) x 1.25 x Isc > max_series_fuse` (`sizing/bos.py`).
  - In these cases the gPV fuse is a design decision (owner, 2026-10-08), not a requirement.
- **Inverter backfeed (owner decision 2026-10-30).** The engine assumes that the inverter declares
  0 A of backfeed current towards the array, on the ground of its anti-islanding protection, so
  condition 1 holds with one string per input. The catalogue has no backfeed field.
  - Technical caveat recorded with the decision: anti-islanding acts on the AC side. Backfeed into
    the array depends on the inverter's DC input stage; transformerless string inverters
    generally declare 0 A.
  - If a datasheet gives a non-zero value, the assumption must be revisited.
- **690-9(b).** A device, when used, is rated at least 125 % of the 690-8(a) current (1.56 x Isc in
  total) and approved for PV use (gPV).
- **690-9(c).** One device per circuit is permitted, with every device in the same polarity. The
  draft's note relies on the ground-fault protection of 690-41(b). The engine places one fuse per
  conductor (two per string), which is compliant; one per string would also be.

## Disconnects (690-13, 690-15)

- **690-15(d).** An equipment disconnect opens all current-carrying conductors that are not
  solidly grounded at the same time. With a transformerless (ungrounded) array that means both
  poles of every string: the engine's "two poles per string".
- **Four poles in series for one string.** This is the manufacturer's wiring to reach the switch's
  voltage or current rating, not a requirement of the standard. The rating rule is 690-13(e) and
  690-15(b): the switch must be rated for the short-circuit current and voltage at its terminals.
- **690-15, other points.**
  - A disconnect integrated in the inverter counts as the isolating means ("within the equipment").
  - One means may isolate all inputs of an inverter.
  - The disconnect is within sight and within 3 m of the equipment.
- **690-13.**
  - The PV system disconnect is marked "DESCONECTADOR DEL SISTEMA FOTOVOLTAICO", with the caution
    legend when its terminals can be energized in the open position.
  - At most six devices.
  - The dedicated breaker of 705-12(b)(1) is an example of a single PV system disconnect.

## Other changes relevant to the engine

| Section | Draft content | Engine today |
|---|---|---|
| 690-7 | Maximum 600 V DC on or in dwellings, 1000 V in other buildings; Table 690-7(a) temperature factors (e.g. 1.10 for 4 to 0 degC) | Check whether a 600 V dwelling rule exists |
| 690-8 | Source current 1.25 x Isc; conductors >= 125 % before adjustment (156 % total); simulation method only for >= 100 kW | Covered (CON-001/002) |
| 690-11 | DC arc-fault protection from 80 V DC | Could come from the inverter record (`afci`) |
| 690-12 | Rapid shutdown: cited by 690-13 and 691-9, but its text is missing from this draft (690-11 jumps to 690-13) | Open |
| 690-31(g) | DC inside a building in metallic raceway up to the first disconnect; "FUENTE DE ENERGÍA FOTOVOLTAICA" labels, white on red, >= 0.95 cm letters, <= 3 m apart | EMT complies; label notes missing |
| 690-45 | EGC per 250-122, minimum 14 AWG, no increase for voltage drop | Same size as the circuit conductor complies |
| 690-47(b) | Auxiliary grounding electrode at the array location (roof and ground mounted), bonded to the structure | Not covered (draft only) |
| 690-53, 690-54 | Labels: maximum voltage and circuit current at the DC disconnect; output current and voltage at the interconnection point | Values already computed |
| Art. 691 | Plants of 5000 kW or more | Out of scope |

## Candidate engine work (not scheduled)

1. 600 V DC limit for dwellings (690-7), if NOM-001-SEDE-2012 supports it too; otherwise mark it as
   draft only.
2. Catalogue field for the inverter's backfeed current, defaulting to the owner's 0 A assumption.
3. Option of one gPV fuse per string (690-9(c)) and its effect on the bill of materials.
4. Array grounding electrode (690-47(b)), marked as draft only.
5. Label notes of 690-13, 690-31(g), 690-53 and 690-54 on the drawing.
