"""Evaluate the choices of a drawn design without reselecting them (ENG-005, Stage 3.8.3).

The editor's drawing is the source of truth. :func:`evaluate_choices` reads a specification,
checks every equipment value the engine knows how to select, and returns three separate lists:

* **checks**: each value as drawn, whether it meets its requirement, why, and the rule;
* **updates**: for values that are *not* locked, what the engine would put instead;
* **recommendations**: for locked values that pass, what the engine would have chosen
  (informational: a locked value is never replaced).

A locked value that fails stays as drawn: its check is the finding, and ``suggested`` carries
the engine's value. Nothing is applied: the editor decides which updates to take. Updates are
computed on the drawing as it is (the conductors use the drawn ITM-1, the raceways the drawn
conductors), so after applying some, evaluate again.

**Locks** name a whole component (``FUS-S1``) or one value (``FUS-S1.ie_a``,
``C-S1.conductors.size``), always by component id.

**Values covered** (the template ``bt_string_residential_v1``: one inverter, up to two strings):

==================  ==========================================  =========
Component           Values                                      Rule
==================  ==========================================  =========
``FUS-*``/``DCB-*`` ``model``, ``ie_a``, ``ue_v``               OCP-002
``DCD-CD1``         ``model``, ``ie_a``, ``ue_v``, ``poles``    DIS-001
ITM-1 (PCC breaker) ``model``, ``rating_a``, ``kaic_ka``        OCP-005, OCP-003
``main_breakers``   ``model``, ``kaic_ka``                      OCP-003
circuits            ``conductors.size`` (+ ``egc``, ``neutral``) CON-002, VD-001/002
raceways            ``raceway.trade_size_mm``                   CON-006
==================  ==========================================  =========

The rule ids are those the sizing engine already reports, so the plugin and the editor say the
same thing; ITM-1's rating, which sizing computes and never reports, uses OCP-005 of the vault
catalogue (lower bound only: the inverter's maximum OCPD is not checked, owner decision).
The rest of the design (string voltages, the inverter, the AC side rules) is the rule pack's:
:attr:`ChoiceReport.draft` carries its findings.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pvsld.catalogue import (
    AcBreaker,
    ComponentRegistry,
    DcBreaker,
    DcFuse,
    DcSwitch,
    UnknownComponentError,
)
from pvsld.core import calc
from pvsld.core.draft import DraftReport, evaluate_draft
from pvsld.core.model import Circuit, PvSystemSpec, Raceway
from pvsld.core.policy import DEFAULT_DC_AC_POLICY, DcAcPolicy
from pvsld.core.severity import Severity
from pvsld.core.tables import NomTables, get_tables
from pvsld.resources import data_path
from pvsld.sizing import bos
from pvsld.sizing.engine import (
    AC_BREAKER_VOLTAGE_V,
    BOX_SWITCH_ID,
    STRING_BREAKER_PREFIX,
    STRING_FUSE_PREFIX,
)
from pvsld.sizing.models import SUPPORTED_SYSTEMS, Issue


@dataclass(frozen=True)
class ChoiceCheck:
    """One drawn value against its requirement."""

    target: str
    """``<component id>.<field>``, e.g. ``FUS-S1.ie_a``; the component id alone for the device."""
    component_id: str
    value: Any
    ok: bool
    rule_id: str
    message_es: str
    locked: bool
    severity: Severity | None = None
    """``None`` when the value passes; error or warning otherwise."""
    suggested: Any = None
    """The engine's value for a locked value that fails (the editor may offer it)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "component_id": self.component_id,
            "value": self.value,
            "ok": self.ok,
            "rule_id": self.rule_id,
            "severity": self.severity.value if self.severity else None,
            "message_es": self.message_es,
            "locked": self.locked,
            "suggested": self.suggested,
        }


@dataclass(frozen=True)
class ChoiceChange:
    """A value the engine would set: an update (unlocked) or a recommendation (locked)."""

    target: str
    component_id: str
    current: Any
    proposed: Any
    rule_id: str
    reason_es: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "component_id": self.component_id,
            "current": self.current,
            "proposed": self.proposed,
            "rule_id": self.rule_id,
            "reason_es": self.reason_es,
        }


@dataclass(frozen=True)
class ChoiceReport:
    """What :func:`evaluate_choices` returns; ``to_dict`` gives plain JSON types."""

    draft: DraftReport
    checks: tuple[ChoiceCheck, ...] = ()
    updates: tuple[ChoiceChange, ...] = ()
    recommendations: tuple[ChoiceChange, ...] = ()
    locks: tuple[str, ...] = ()
    unknown_locks: tuple[str, ...] = ()
    """Locks naming a component the drawing does not have."""
    skipped_es: str | None = None
    """Why the choices were not evaluated (the draft is incomplete or not supported)."""

    @property
    def evaluated(self) -> bool:
        return self.skipped_es is None

    @property
    def failing(self) -> tuple[ChoiceCheck, ...]:
        return tuple(c for c in self.checks if not c.ok)

    @property
    def ok(self) -> bool:
        """True when the draft is complete and valid and no value fails with an error."""
        return (
            self.evaluated
            and self.draft.ok
            and not any(c.severity == Severity.ERROR for c in self.failing)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "evaluated": self.evaluated,
            "skipped_es": self.skipped_es,
            "locks": list(self.locks),
            "unknown_locks": list(self.unknown_locks),
            "checks": [c.to_dict() for c in self.checks],
            "updates": [c.to_dict() for c in self.updates],
            "recommendations": [c.to_dict() for c in self.recommendations],
            "draft": self.draft.to_dict(),
        }


Problem = tuple[str, Severity, str]
"""(rule id, severity, Spanish message) of a value that fails."""


def _g(value: float) -> str:
    return f"{value:g}"


def _n(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}"


def _component_ids(node: Any) -> set[str]:
    ids: set[str] = set()
    if isinstance(node, Mapping):
        if isinstance(node.get("id"), str):
            ids.add(node["id"])
        for value in node.values():
            ids |= _component_ids(value)
    elif isinstance(node, list):
        for value in node:
            ids |= _component_ids(value)
    return ids


@dataclass
class _Evaluator:
    spec: PvSystemSpec
    derived: calc.Derived
    tables: NomTables
    registry: ComponentRegistry
    locks: frozenset[str]
    checks: list[ChoiceCheck] = field(default_factory=list)
    updates: list[ChoiceChange] = field(default_factory=list)
    recommendations: list[ChoiceChange] = field(default_factory=list)

    # --- recording -------------------------------------------------------------------------

    def locked(self, target: str) -> bool:
        component = target.split(".", maxsplit=1)[0]
        return target in self.locks or component in self.locks

    def record(
        self,
        component_id: str,
        name: str,
        value: Any,
        *,
        rule_id: str,
        problem: Problem | None,
        ok_es: str,
        proposed: Any = None,
        why_es: str = "",
        check: bool = True,
    ) -> None:
        """Check one value and, when the engine would set another, propose it.

        ``check=False`` records no check (an empty value) but still proposes the engine's.
        """
        target = f"{component_id}.{name}" if name else component_id
        locked = self.locked(target)
        differs = proposed is not None and proposed != value
        change = ChoiceChange(target, component_id, value, proposed, rule_id, why_es or ok_es)
        if not check:
            pass
        elif problem is None:
            self.checks.append(
                ChoiceCheck(target, component_id, value, True, rule_id, ok_es, locked)
            )
        else:
            rule, severity, message = problem
            self.checks.append(
                ChoiceCheck(
                    target,
                    component_id,
                    value,
                    False,
                    rule,
                    message,
                    locked,
                    severity,
                    suggested=proposed if locked and differs else None,
                )
            )
        if not differs:
            return
        if not locked:
            self.updates.append(change)
        elif problem is None:
            self.recommendations.append(change)

    def note(self, component_id: str, issue: Issue) -> None:
        """A finding about a component as a whole (e.g. nothing in the catalogue fits)."""
        self.checks.append(
            ChoiceCheck(
                component_id,
                component_id,
                None,
                False,
                issue.rule_id,
                issue.message_es,
                self.locked(component_id),
                issue.severity,
            )
        )

    def lookup(self, model: str, kind: type[Any]) -> tuple[Any, str | None]:
        try:
            component = self.registry.get(model)
        except UnknownComponentError:
            return None, f"«{model}» no está en el catálogo; no se puede verificar"
        if not isinstance(component, kind):
            return (
                None,
                f"«{model}» es un {component.component_type}, no un equipo de esta posición",
            )
        return component, None

    # --- string protections ----------------------------------------------------------------

    def string_protections(self) -> None:
        strings = {s.string_id: s for s in self.derived.strings}
        modules = {m.id: m for m in self.spec.modules}
        for device in self.spec.dc_bos.disconnects:
            if device.id.startswith(STRING_FUSE_PREFIX):
                prefix, kind, label = STRING_FUSE_PREFIX, DcFuse, "fusible gPV"
            elif device.id.startswith(STRING_BREAKER_PREFIX):
                prefix, kind, label = STRING_BREAKER_PREFIX, DcBreaker, "interruptor de CD"
            else:
                continue
            string = strings.get(device.id.removeprefix(prefix))
            if string is None:
                self.note(
                    device.id,
                    Issue(
                        "OCP-002",
                        Severity.WARNING,
                        f"{device.id} no corresponde a ninguna cadena "
                        f"(se espera {prefix}<id de la cadena>); no se revisa.",
                    ),
                )
                continue
            need = bos.string_need(
                max_series_fuse_a=modules[string.module_id].max_series_fuse_a,
                isc_a=string.isc_a,
                strings_per_input=string.n_parallel,
                voc_cold_string_v=string.voc_max_string_v,
            )
            reason = f"Protección de la cadena {string.string_id}."
            choice = (
                bos.fuse_for(need, self.registry.dc_fuses(), reason)
                if kind is DcFuse
                else bos.breaker_for(need, self.registry.dc_breakers(), reason)
            )
            why = (
                f"El {label} más chico del catálogo para {string.string_id}: {choice.device_id} "
                f"({_g(choice.rating_a or 0)} A, {_g(choice.ue_v or 0)} V)."
            )
            if choice.device_id is None:
                self.note(device.id, Issue("OCP-002", Severity.WARNING, choice.reason_es))
            self.record(
                device.id,
                "model",
                device.model,
                rule_id="OCP-002",
                problem=None
                if device.model is None
                else self._string_model_problem(device.model, kind, need, device.ie_a),
                ok_es=f"{device.model} cumple para la cadena {string.string_id}.",
                proposed=choice.device_id,
                why_es=why,
                check=device.model is not None,
            )
            window = (
                f"{_n(need.minimum_a)} A a {_g(need.maximum_a)} A "
                f"(1.25 × 1.25 × Isc y fusible máximo del módulo)"
            )
            rating = bos.string_rating_problem(device.ie_a, need)
            self.record(
                device.id,
                "ie_a",
                device.ie_a,
                rule_id="OCP-002",
                problem=None
                if rating is None
                else (
                    "OCP-002",
                    Severity.ERROR,
                    f"{device.id}: {rating}; debe estar entre {window}.",
                ),
                ok_es=f"{device.id}: {_g(device.ie_a)} A dentro de {window}.",
                proposed=choice.rating_a,
                why_es=why,
            )
            self.record(
                device.id,
                "ue_v",
                device.ue_v,
                rule_id="OCP-002",
                problem=None
                if device.ue_v >= need.voc_cold_v
                else (
                    "OCP-002",
                    Severity.ERROR,
                    f"{device.id}: {_g(device.ue_v)} V es menor que el Voc en frío de la cadena "
                    f"({_n(need.voc_cold_v)} V).",
                ),
                ok_es=f"{device.id}: {_g(device.ue_v)} V >= Voc en frío ({_n(need.voc_cold_v)} V).",
                proposed=choice.ue_v,
                why_es=why,
            )

    def _string_model_problem(
        self, model: str, kind: type[Any], need: bos.StringNeed, ie_a: float
    ) -> Problem | None:
        device, missing = self.lookup(model, kind)
        if device is None:
            return "OCP-002", Severity.WARNING, f"{missing}."
        why = bos.fuse_problem(device, need) if kind is DcFuse else bos.breaker_fit(device, need)[0]
        if why is not None:
            return "OCP-002", Severity.ERROR, f"{model} no protege la cadena: {why}."
        if device.rated_current_a != ie_a:
            return (
                "OCP-002",
                Severity.WARNING,
                f"{model} es de {_g(device.rated_current_a)} A, pero el diagrama dice "
                f"{_g(ie_a)} A.",
            )
        return None

    # --- box disconnect --------------------------------------------------------------------

    def box_switch(self) -> None:
        device = next((d for d in self.spec.dc_bos.disconnects if d.id == BOX_SWITCH_ID), None)
        strings = self.derived.strings
        if device is None or not strings:
            return
        n_strings = len(strings)
        voc = max(s.voc_max_string_v for s in strings)
        i_max = max(calc.ISC_SAFETY_FACTOR * s.isc_a for s in strings)
        poles = bos.box_switch_poles_per_string(n_strings) * n_strings
        choice, found = bos.select_box_switch(
            position=BOX_SWITCH_ID,
            n_strings=n_strings,
            voc_cold_string_v=voc,
            string_i_max_a=i_max,
            switches=self.registry.dc_switches(),
        )
        for issue in found:
            self.note(BOX_SWITCH_ID, issue)
        why = (
            f"El seccionador más chico del catálogo para {n_strings} cadena(s): "
            f"{choice.device_id} ({_g(choice.rating_a)} A a {_g(choice.voltage_v)} V)."
            if choice
            else ""
        )
        if device.model is not None or choice is not None:
            problem: Problem | None = None
            if device.model is not None:
                switch, missing = self.lookup(device.model, DcSwitch)
                why_not = (
                    missing
                    if switch is None
                    else bos.box_switch_problem(
                        switch, n_strings=n_strings, voc_cold_string_v=voc, string_i_max_a=i_max
                    )
                )
                if why_not is not None:
                    severity = Severity.WARNING if switch is None else Severity.ERROR
                    problem = ("DIS-001", severity, f"{BOX_SWITCH_ID}: {why_not}.")
            self.record(
                BOX_SWITCH_ID,
                "model",
                device.model,
                rule_id="DIS-001",
                problem=problem,
                ok_es=f"{BOX_SWITCH_ID}: {device.model} abre las cadenas.",
                proposed=choice.device_id if choice else None,
                why_es=why,
                check=device.model is not None,
            )
        self.record(
            BOX_SWITCH_ID,
            "ie_a",
            device.ie_a,
            rule_id="DIS-001",
            problem=None
            if device.ie_a >= i_max
            else (
                "DIS-001",
                Severity.ERROR,
                f"{BOX_SWITCH_ID}: {_g(device.ie_a)} A es menor que la corriente máxima de la "
                f"cadena ({_n(i_max)} A, 1.25 × Isc).",
            ),
            ok_es=f"{BOX_SWITCH_ID}: {_g(device.ie_a)} A >= {_n(i_max)} A (1.25 × Isc).",
            proposed=choice.rating_a if choice else None,
            why_es=why,
        )
        self.record(
            BOX_SWITCH_ID,
            "ue_v",
            device.ue_v,
            rule_id="DIS-001",
            problem=None
            if device.ue_v >= voc
            else (
                "DIS-001",
                Severity.ERROR,
                f"{BOX_SWITCH_ID}: {_g(device.ue_v)} V es menor que el Voc en frío ({_n(voc)} V).",
            ),
            ok_es=f"{BOX_SWITCH_ID}: {_g(device.ue_v)} V >= Voc en frío ({_n(voc)} V).",
            proposed=choice.voltage_v if choice else None,
            why_es=why,
        )
        self.record(
            BOX_SWITCH_ID,
            "poles",
            device.poles,
            rule_id="DIS-001",
            problem=None
            if device.poles >= poles
            else (
                "DIS-001",
                Severity.ERROR,
                f"{BOX_SWITCH_ID}: {device.poles} polos no abren {n_strings} cadena(s); se "
                f"requieren {poles}.",
            ),
            ok_es=f"{BOX_SWITCH_ID}: {device.poles} polos para {n_strings} cadena(s).",
            proposed=choice.poles if choice else None,
            why_es=why,
        )

    # --- AC breakers -----------------------------------------------------------------------

    def _ac_model_problem(
        self,
        position: str,
        model: str,
        drawn_a: float,
        *,
        rating_a: float,
        poles: int,
        fault_ka: float,
        exact: bool,
    ) -> Problem | None:
        breaker, missing = self.lookup(model, AcBreaker)
        if breaker is None:
            return "OCP-003", Severity.WARNING, f"{position}: {missing}."
        why = bos.ac_breaker_problem(
            breaker,
            rating_a=rating_a,
            poles=poles,
            voltage_v=AC_BREAKER_VOLTAGE_V,
            fault_ka=fault_ka,
            exact=exact,
        )
        if why is not None:
            return "OCP-003", Severity.ERROR, f"{position}: {model} no cumple: {why}."
        if breaker.rated_current_a != drawn_a:
            return (
                "OCP-003",
                Severity.WARNING,
                f"{position}: {model} es de {_g(breaker.rated_current_a)} A, pero el diagrama dice "
                f"{_g(drawn_a)} A.",
            )
        return None

    def itm(self) -> None:
        spec = self.spec
        position = spec.ac_bos.point_of_connection.breaker
        ocpd = next((o for o in spec.ac_bos.ocpds if o.id == position), None)
        if ocpd is None or not spec.inverters:
            return
        inverter = spec.inverters[0]
        fault = spec.utility.available_fault_current_ka
        phases = SUPPORTED_SYSTEMS.get(spec.utility.system, inverter.phases)
        minimum = calc.ocpd_min_a(inverter.iac_max_a)
        standard = bos.ac_ocpd_rating_a(inverter.iac_max_a, self.tables)
        choice, found = bos.select_ac_breaker(
            position=position,
            rating_a=standard,
            poles=phases,
            voltage_v=AC_BREAKER_VOLTAGE_V,
            fault_ka=fault,
            breakers=self.registry.ac_breakers(),
        )
        for issue in found:
            self.note(position, issue)
        why = (
            f"El interruptor del catálogo para la salida del inversor ({_n(minimum)} A = 1.25 × "
            f"{_g(inverter.iac_max_a)} A): "
            + (
                f"{choice.device_id}, {_g(choice.rating_a)} A, {_g(choice.interrupting_ka)} kA."
                if choice
                else f"{_g(standard)} A."
            )
        )
        if ocpd.model is not None or choice is not None:
            self.record(
                position,
                "model",
                ocpd.model,
                rule_id="OCP-003",
                problem=None
                if ocpd.model is None
                else self._ac_model_problem(
                    position,
                    ocpd.model,
                    ocpd.rating_a,
                    rating_a=minimum,
                    poles=phases,
                    fault_ka=fault,
                    exact=False,
                ),
                ok_es=f"{position}: {ocpd.model} cumple.",
                proposed=choice.device_id if choice else None,
                why_es=why,
                check=ocpd.model is not None,
            )
        self.record(
            position,
            "rating_a",
            ocpd.rating_a,
            rule_id="OCP-005",
            problem=None
            if ocpd.rating_a >= minimum
            else (
                "OCP-005",
                Severity.ERROR,
                f"{position}: {_g(ocpd.rating_a)} A es menor que 1.25 × la corriente máxima del "
                f"inversor ({_n(minimum)} A, NOM 690-8(b)(1)).",
            ),
            ok_es=f"{position}: {_g(ocpd.rating_a)} A >= {_n(minimum)} A (1.25 × Iac máx).",
            proposed=choice.rating_a if choice else standard,
            why_es=why,
        )
        self.record(
            position,
            "kaic_ka",
            ocpd.kaic_ka,
            rule_id="OCP-003",
            problem=self._kaic_problem(position, ocpd.kaic_ka, fault),
            ok_es=f"{position}: {_g(ocpd.kaic_ka)} kA >= {_g(fault)} kA de falla.",
            proposed=choice.interrupting_ka if choice else None,
            why_es=why,
        )

    @staticmethod
    def _kaic_problem(position: str, kaic_ka: float, fault_ka: float) -> Problem | None:
        if kaic_ka >= fault_ka:
            return None
        return (
            "OCP-003",
            Severity.ERROR,
            f"{position}: {_g(kaic_ka)} kA es menor que la corriente de falla disponible "
            f"({_g(fault_ka)} kA, NOM 110-9).",
        )

    def mains(self) -> None:
        fault = self.spec.utility.available_fault_current_ka
        for main in self.spec.ac_bos.main_breakers:
            choice, found = bos.select_ac_breaker(
                position=main.id,
                rating_a=main.rating_a,
                poles=main.poles,
                voltage_v=AC_BREAKER_VOLTAGE_V,
                fault_ka=fault,
                breakers=self.registry.ac_breakers(),
                exact=True,
            )
            for issue in found:
                self.note(main.id, issue)
            why = (
                f"El interruptor del catálogo de {_g(main.rating_a)} A del servicio: "
                f"{choice.device_id}, {_g(choice.interrupting_ka)} kA."
                if choice
                else ""
            )
            if main.model is not None or choice is not None:
                self.record(
                    main.id,
                    "model",
                    main.model,
                    rule_id="OCP-003",
                    problem=None
                    if main.model is None
                    else self._ac_model_problem(
                        main.id,
                        main.model,
                        main.rating_a,
                        rating_a=main.rating_a,
                        poles=main.poles,
                        fault_ka=fault,
                        exact=True,
                    ),
                    ok_es=f"{main.id}: {main.model} cumple.",
                    proposed=choice.device_id if choice else None,
                    why_es=why,
                    check=main.model is not None,
                )
            if main.kaic_ka is not None or choice is not None:
                self.record(
                    main.id,
                    "kaic_ka",
                    main.kaic_ka,
                    rule_id="OCP-003",
                    problem=None
                    if main.kaic_ka is None
                    else self._kaic_problem(main.id, main.kaic_ka, fault),
                    ok_es=f"{main.id}: {_g(main.kaic_ka or 0)} kA >= {_g(fault)} kA de falla.",
                    proposed=choice.interrupting_ka if choice else None,
                    why_es=why,
                    check=main.kaic_ka is not None,
                )

    # --- conductors and raceways -----------------------------------------------------------

    def _raceway(self, circuit: Circuit) -> Raceway:
        if circuit.raceway.ref is None:
            return circuit.raceway
        owner = next(c for c in self.spec.circuits if c.id == circuit.raceway.ref)
        return owner.raceway

    def conductors(self) -> None:
        spec = self.spec
        site = spec.project.site
        limits = spec.standards.vd_limits_pct
        strings = {s.string_id: s for s in self.derived.strings}
        values = {v.circuit_id: v for v in self.derived.circuits}
        inverters = {i.id: i for i in spec.inverters}
        for circuit in spec.circuits:
            cv = values[circuit.id]
            source = circuit.from_.split(".", maxsplit=1)[0]
            if cv.i_max_a is None:
                continue
            length = circuit.length_m
            if circuit.kind == "pv_source":
                string = strings[source]
                drop = lambda size, s=string, m=length: calc.voltage_drop_dc_pct(  # noqa: E731
                    size, m, s.imp_a, s.vmp_stc_string_v, self.tables
                )
                limit, drop_rule, ocpd_a = limits.dc, "VD-001", None
            else:
                inverter = inverters[source]
                drop = lambda size, i=inverter, m=length: calc.voltage_drop_ac_pct(  # noqa: E731
                    size, m, i.iac_max_a, i.vac_v, i.phases, self.tables
                )
                limit, drop_rule, ocpd_a = limits.ac, "VD-002", cv.ocpd_rating_a
            raceway = self._raceway(circuit)
            common = {
                "circuit_id": circuit.id,
                "kind": circuit.kind,
                "i_max_a": cv.i_max_a,
                "ocpd_a": ocpd_a,
                "current_carrying": raceway.ccc_count or circuit.conductors.qty,
                "ambient_c": circuit.ambient_c
                if circuit.ambient_c is not None
                else site.t_amb_max_c,
                "clearance_mm": raceway.rooftop_clearance_mm,
                "length_m": length,
                "drop_pct": drop,
                "drop_limit_pct": limit,
                "drop_rule": drop_rule,
                "tables": self.tables,
            }
            size = circuit.conductors.size
            _checked, problems = bos.size_conductor(**common, given_size=size)
            best, _issues = bos.size_conductor(**common)
            proposed = best.size if best else None
            problem: Problem | None = None
            if problems:
                first = problems[0]
                problem = (first.rule_id, first.severity, first.message_es)
            why = (
                f"El calibre más chico que soporta {_n(cv.i_max_a)} A con caída de tensión dentro "
                f"de {_n(limit, 2)} %: {proposed}."
            )
            self.record(
                circuit.id,
                "conductors.size",
                size,
                rule_id="CON-002",
                problem=problem,
                ok_es=f"{circuit.id}: {size} soporta {_n(cv.i_max_a)} A y su caída de tensión "
                f"({_n(drop(size), 2)} %) está dentro de {_n(limit, 2)} %.",
                proposed=proposed,
                why_es=why,
            )
            final = size if self.locked(f"{circuit.id}.conductors.size") else (proposed or size)
            same = "Tierra y neutro del mismo calibre que el conductor (decisión del propietario)."
            for name, current in (
                ("egc.size", circuit.egc.size),
                ("neutral.size", circuit.neutral.size if circuit.neutral else None),
            ):
                if current is not None and current != final:
                    target = f"{circuit.id}.{name}"
                    change = ChoiceChange(target, circuit.id, current, final, "CON-002", same)
                    (self.recommendations if self.locked(target) else self.updates).append(change)

    def raceways(self) -> None:
        for circuit in self.spec.circuits:
            raceway = circuit.raceway
            if raceway.ref is not None or raceway.type not in (None, "EMT"):
                continue
            fill = calc.raceway_fill(self.spec, circuit, self.tables)
            if fill.missing:
                continue  # CON-006 of the rule pack reports the unknown dimensions
            limit = calc.fill_limit_pct(fill.conductors, self.tables)
            smallest = next(
                (row for row in self.tables.emt if fill.area_mm2 / row[3] * 100 <= limit), None
            )
            proposed = float(smallest[0]) if smallest else None
            why = (
                f"El EMT más chico para {fill.conductors} conductores ({_n(fill.area_mm2)} mm², "
                f"máximo {_g(limit)} %): {_g(proposed)} mm."
                if proposed
                else ""
            )
            size = raceway.trade_size_mm
            row = next((r for r in self.tables.emt if size is not None and r[0] == size), None)
            problem: Problem | None = None
            if size is None:
                ok_es = f"{circuit.id}: sin designación de canalización."
            elif row is None:
                problem = (
                    "CON-006",
                    Severity.ERROR,
                    f"EMT {_g(size)} mm no es una designación de la Tabla 4.",
                )
                ok_es = ""
            else:
                pct = fill.area_mm2 / row[3] * 100
                ok_es = f"{circuit.id}: EMT {_g(size)} mm al {_n(pct)} % (máximo {_g(limit)} %)."
                if pct > limit:
                    problem = (
                        "CON-006",
                        Severity.ERROR,
                        f"La canalización EMT {_g(size)} mm de {circuit.id} queda llena al "
                        f"{_n(pct)} % (máximo {_g(limit)} %, Capítulo 10, Tabla 1).",
                    )
            self.record(
                circuit.id,
                "raceway.trade_size_mm",
                size,
                rule_id="CON-006",
                problem=problem,
                ok_es=ok_es,
                proposed=proposed,
                why_es=why,
            )


def _default_registry() -> ComponentRegistry:
    return ComponentRegistry.load(data_path("datasheets/records"))


def evaluate_choices(
    data: Any,
    locks: Iterable[str] = (),
    *,
    registry: ComponentRegistry | None = None,
    dc_ac_policy: DcAcPolicy = DEFAULT_DC_AC_POLICY,
) -> ChoiceReport:
    """Check the drawn values of ``data`` and propose the engine's (see the module docstring).

    ``data`` is a specification as plain data (what :func:`pvsld.core.draft.evaluate_draft`
    takes). The choices are evaluated only when it parses as a whole and is supported; otherwise
    ``skipped_es`` says why and ``draft`` lists what to fix. ``registry`` defaults to the bundled
    catalogue (reviewed records).
    """
    lock_set = tuple(dict.fromkeys(str(lock).strip() for lock in locks if str(lock).strip()))
    draft = evaluate_draft(data, dc_ac_policy=dc_ac_policy)
    known = _component_ids(data)
    unknown = tuple(lock for lock in lock_set if lock.split(".", maxsplit=1)[0] not in known)
    if draft.spec is None or draft.derived is None:
        return ChoiceReport(
            draft,
            locks=lock_set,
            unknown_locks=unknown,
            skipped_es="No se revisaron los equipos: primero hay que completar o corregir la "
            "especificación (ver «draft.issues»).",
        )
    if draft.issues_of("unsupported"):
        return ChoiceReport(
            draft,
            locks=lock_set,
            unknown_locks=unknown,
            skipped_es="No se revisaron los equipos: el diseño usa algo que el motor aún no "
            "dimensiona (ver los problemas «unsupported»).",
        )
    evaluator = _Evaluator(
        spec=draft.spec,
        derived=draft.derived,
        tables=get_tables(draft.spec.standards.nom_edition),
        registry=registry if registry is not None else _default_registry(),
        locks=frozenset(lock_set),
    )
    evaluator.string_protections()
    evaluator.box_switch()
    evaluator.itm()
    evaluator.mains()
    evaluator.conductors()
    evaluator.raceways()
    return ChoiceReport(
        draft,
        checks=tuple(evaluator.checks),
        updates=tuple(evaluator.updates),
        recommendations=tuple(evaluator.recommendations),
        locks=lock_set,
        unknown_locks=unknown,
    )


__all__ = ["ChoiceChange", "ChoiceCheck", "ChoiceReport", "evaluate_choices"]
