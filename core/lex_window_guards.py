"""Guardy warunkowe G1–G3/G5 warstwy P-1 lex-committed-window (WB2, CZASY 492).

Kontrakt: `docs/WB2_CONDITIONAL_GUARDS.md`. Podstawa: decyzje ownera D1+D2 z
2026-07-27 (`memory/owner-decision-czasy-d1d2d3-2026-07-27`) + plan v4 sekcja 13
diagnozy + jednoznaczna specyfikacja Sola RUN3-b sekcja 3.

Co ten moduł naprawia u źródła
------------------------------
Warstwa P-1 miała mandat „ratuj okno odbioru", a przestawiała trasę także przy
`viol 0→0`, dla kilku minut jazdy, kosztem świeżości niesionego jedzenia
(incydent 492: świeżość 24,3 → 28,5 min za 2,7 min jazdy). Trzy luki:

  1. „żadna dostawa nie później" iterowało WYŁĄCZNIE zlecenia `assigned` —
     niesione nie miały żadnej ochrony przed opóźnieniem;
  2. `carry_cap = max(35.0, bcarry)` był sufitem ABSOLUTNYM — pozwalał
     pogorszyć świeżość 8 → 34,8 min, bo mieści się „pod 35";
  3. brak progu materialności zysku jazdy — 0,1 min wystarczyło, by przestawić.

Dlaczego guardy są WARUNKOWE (D1)
---------------------------------
Płaskie guardy zabijają dokładnie to, czego warstwa ma bronić. Symulacja Sola
na zamrożonym ledgerze (561 wierszy z 27.07): bezwarunkowe G3 wycina 102/313
przestawień ze ŚCISŁĄ poprawą okna, a G2 z tolerancją 3 zabija ≥257/313.
Dlatego przy `dviol < bviol` (kandydat ściśle zmniejsza naruszenia okna)
zawieszone są: wspólny guard delty (G1 + G2-delta) oraz G3. NIE jest zawieszony
absolutny cap świeżości ani zakaz pogarszania okna — to zostaje zawsze.

Jedna metryka, jeden predykat
-----------------------------
Cała arytmetyka handoff/carry idzie przez `core.carry_freshness` — ten sam
moduł, którego używa `route_simulator_v2._capz_reseq_plan`. G1 i G2-delta to
JEDEN predykat delty raportowany w dwóch kohortach (assigned / carried), bo dla
tego samego zlecenia kotwica possession jest stała, więc delta świeżości ≡
delta handoffu (dowód w docstringu `carry_freshness`).

Moduł jest CZYSTY: żadnego I/O poza odczytem progów, żadnych efektów ubocznych,
`evaluate()` nie rzuca dla danych kompletnych i zwraca fail-closed dla
niekompletnych.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from dispatch_v2 import common as _C
from dispatch_v2.core import carry_freshness as _cf

#: Werdykty pojedynczego guardu w ledgerze v2 (`guards.G*.verdict`).
PASS = "pass"
FAIL = "fail"
EXEMPT = "exempt"          # zawieszony wyjątkiem D1 (ścisła poprawa okna)
UNEVALUABLE = "unevaluable"  # brak danych ⇒ kandydat odrzucony (fail-closed)


@dataclass(frozen=True)
class Thresholds:
    """Progi EFEKTYWNE tej ewaluacji (trafiają do ledgera per guard)."""

    delay_tol_min: float      # G1 + G2-delta — JEDEN wspólny budżet delty
    carry_cap_min: float      # G2 absolutny cap trybu (35 / 40 w Alarmie)
    min_gain_min: float       # G3
    alarm: bool = False       # czy cap pochodzi z kanonicznego Alarmu


def load_thresholds(*, alarm_certificate: Optional[Dict[str, Any]] = None) -> Thresholds:
    """Progi z `flags.json` (hot-reload), fallback = stałe `common.py`.

    Świadomie NIE przez `os.environ`: wymóg 13.2 p.8 („progi decyzyjne przez
    flags.json, nie env"). Wartości startowe = decyzja ownera D2:
    wspólna delta 3 min, cap 35/40, minimalny zysk jazdy 1,0 min.
    """
    from dispatch_v2.core import loadgov_snapshot as _lg
    fl = {}
    try:
        fl = _C.load_flags()
    except Exception:
        fl = {}
    alarm = _lg.alarm_certified(alarm_certificate)
    cap_key = ("LEX_WINDOW_CARRY_CAP_ALARM_MIN" if alarm
               else "LEX_WINDOW_CARRY_CAP_MIN")
    cap_default = float(getattr(_C, cap_key, 40.0 if alarm else 35.0))
    return Thresholds(
        delay_tol_min=float(fl.get("LEX_WINDOW_DELAY_TOL_MIN",
                                   getattr(_C, "LEX_WINDOW_DELAY_TOL_MIN", 3.0))),
        carry_cap_min=float(fl.get(cap_key, cap_default)),
        min_gain_min=float(fl.get("LEX_WINDOW_MIN_GAIN_MIN",
                                  getattr(_C, "LEX_WINDOW_MIN_GAIN_MIN", 1.0))),
        alarm=alarm,
    )


@dataclass
class Facts:
    """Fakty JEDNEJ permutacji — baseline albo kandydata.

    `handoff_by_order` obejmuje WSZYSTKIE dostawy (assigned i carried);
    `carry_by_order` tylko niesione. Obie mapy liczone przez
    `core.carry_freshness`, więc na tym samym zegarze (handoff = przyjazd + dwell).
    """

    window_viol: int
    drive_min: float
    handoff_by_order: Dict[str, Optional[float]] = field(default_factory=dict)
    carry_by_order: Dict[str, Optional[float]] = field(default_factory=dict)


@dataclass
class GuardResult:
    admissible: bool
    exemption: bool
    verdicts: Dict[str, Dict[str, Any]]
    reason: Optional[str] = None

    def ledger_guards(self) -> Dict[str, Any]:
        """Sekcja `guards` rekordu v2 — klucze G1..G5 dokładnie jak w schemacie."""
        return {g: self.verdicts.get(g) for g in ("G1", "G2", "G3", "G4", "G5")}


def _v(verdict: str, threshold: Optional[float], margin: Optional[float],
       reason: str) -> Dict[str, Any]:
    return {"verdict": verdict, "threshold_effective": threshold,
            "margin": margin, "reason": reason}


def evaluate(baseline: Facts, candidate: Facts, *,
             assigned_ids: Iterable[str], carried_ids: Iterable[str],
             thresholds: Thresholds) -> GuardResult:
    """Oceń JEDNĄ nie-identity permutację. Filtr, nie ranking.

    Wołane dla KAŻDEGO kandydata PRZED wyborem minimum (Sol RUN3-b: „wybierz
    zwycięzcę, potem odrzuć" jest błędne, bo pomija bezpiecznego drugiego
    kandydata). Kolejność klucza lex pozostaje nietknięta — to jest wyłącznie
    zawężenie zbioru dopuszczalnych.
    """
    assigned_ids = list(assigned_ids)
    carried_ids = list(carried_ids)
    verdicts: Dict[str, Dict[str, Any]] = {}

    # ── Warunek NADRZĘDNY (D1, zawsze): kandydat nie może pogorszyć okna. ──
    # Dziś wynikał tylko z tego, że identity jest w puli i wygrywa remis; teraz
    # jest JAWNY, bo wyjątek D1 mówi o „ścisłej poprawie" i musi mieć parę.
    w_margin = float(baseline.window_viol - candidate.window_viol)
    if candidate.window_viol > baseline.window_viol:
        verdicts["W"] = _v(FAIL, 0.0, w_margin, "window_regression")
        return GuardResult(False, False, verdicts, "window_regression")
    verdicts["W"] = _v(PASS, 0.0, w_margin, "window_not_worse")

    exemption = candidate.window_viol < baseline.window_viol

    # ── G1 + G2-delta: JEDEN wspólny guard delty, dwie kohorty raportowania ──
    tol = thresholds.delay_tol_min
    g1_oid, g1_delta = _cf.worst_delta(baseline.handoff_by_order,
                                       candidate.handoff_by_order, assigned_ids)
    g2_oid, g2_delta = _cf.worst_delta(baseline.carry_by_order,
                                       candidate.carry_by_order, carried_ids)

    # Brak danych = kandydat NIEOCENIALNY = odrzucony. Fail-closed obowiązuje
    # także pod wyjątkiem D1: wyjątek zdejmuje próg, nie obowiązek pomiaru.
    if assigned_ids and g1_delta is None:
        verdicts["G1"] = _v(UNEVALUABLE, tol, None, f"brak handoff dla {g1_oid}")
        return GuardResult(False, exemption, verdicts, "g1_unevaluable")
    if carried_ids and g2_delta is None:
        verdicts["G2"] = _v(UNEVALUABLE, tol, None, f"brak carry dla {g2_oid}")
        return GuardResult(False, exemption, verdicts, "g2_unevaluable")

    if exemption:
        verdicts["G1"] = _v(EXEMPT, tol, g1_delta, "d1_strict_window_improvement")
    else:
        ok = (g1_delta is None) or (g1_delta <= tol)
        verdicts["G1"] = _v(PASS if ok else FAIL, tol, g1_delta,
                            "delay_within_tol" if ok else f"opóźnienie {g1_oid}")
        if not ok:
            return GuardResult(False, exemption, verdicts, "g1_delay")

    # ── G2: świeżość per sztuka = delta (jak wyżej) ORAZ absolutny cap trybu ──
    cap = thresholds.carry_cap_min
    cap_reason = None
    for oid in carried_ids:
        cand_carry = candidate.carry_by_order.get(oid)
        base_carry = baseline.carry_by_order.get(oid)
        if cand_carry is None:
            verdicts["G2"] = _v(UNEVALUABLE, cap, None, f"brak carry dla {oid}")
            return GuardResult(False, exemption, verdicts, "g2_unevaluable")
        if cand_carry <= cap:
            continue
        # Baseline już ponad capem: brak wyjątku ABSOLUTNEGO, ale kandydatowi
        # wolno co najwyżej nie pogorszyć (D2 „relative"). Inaczej regresja
        # świeżości byłaby na zawsze zablokowana, gdy raz przekroczy 35.
        if base_carry is not None and base_carry > cap and cand_carry <= base_carry:
            cap_reason = "over_cap_not_worsened"
            continue
        verdicts["G2"] = _v(FAIL, cap, cand_carry - cap, f"cap świeżości {oid}")
        return GuardResult(False, exemption, verdicts, "g2_cap")

    if exemption:
        verdicts["G2"] = _v(EXEMPT, cap, g2_delta,
                            cap_reason or "d1_strict_window_improvement")
    else:
        ok = (g2_delta is None) or (g2_delta <= tol)
        verdicts["G2"] = _v(PASS if ok else FAIL, tol, g2_delta,
                            cap_reason or ("carry_within_tol" if ok
                                           else f"świeżość {g2_oid}"))
        if not ok:
            return GuardResult(False, exemption, verdicts, "g2_delta")

    # ── G3: minimalny materialny zysk jazdy (surowe minuty, inclusive) ──
    gain = float(baseline.drive_min) - float(candidate.drive_min)
    if exemption:
        verdicts["G3"] = _v(EXEMPT, thresholds.min_gain_min, gain,
                            "d1_strict_window_improvement")
    else:
        ok = gain >= thresholds.min_gain_min
        verdicts["G3"] = _v(PASS if ok else FAIL, thresholds.min_gain_min, gain,
                            "gain_material" if ok else "gain_below_min")
        if not ok:
            return GuardResult(False, exemption, verdicts, "g3_gain")

    return GuardResult(True, exemption, verdicts, None)


def g5_verdict(tol_min: float, source: str) -> Dict[str, Any]:
    """G5 jako STRICT-STUB: raportuje próg okna i jego proweniencję.

    Nie ma tu decyzji do podjęcia, dopóki nie istnieje kanoniczny producent
    snapshotu (patrz `core/loadgov_snapshot.py`). Wpis w ledgerze jest po to,
    żeby po wpięciu producenta dało się UDOWODNIĆ, na jakiej tolerancji
    liczono każdą historyczną decyzję.
    """
    return _v(PASS, tol_min, None, source)


# Uwaga o `guards.G4` w ledgerze: slot zostaje `null` i jest to WŁAŚCIWY zapis.
# Rekord `decision` powstaje w warstwie reorderu, a G4 orzeka DWIE warstwy wyżej,
# po floorze i pinie — w chwili emisji rekordu decyzji jego werdykt jeszcze nie
# istnieje. Dopisanie go tam wymagałoby albo drugiego zapisu do tego samego
# wiersza (append-only tego zabrania), albo trzymania rekordu w pamięci do końca
# commitu (i tracenia go przy każdym wyjątku po drodze).
# Fakt G4 jest więc zapisany tam, gdzie faktycznie powstaje — w `write_receipt`:
#   outcome=written                      ⇒ walidator przepuścił,
#   outcome=not_attempted + error=g4_*   ⇒ walidator odrzucił (z powodem).
# Rekonstrukcja jest jednoznaczna i nie wymaga zmiany schematu na v3.


def rejection_counter_key(reason: Optional[str]) -> Optional[str]:
    """Mapowanie powodu odrzucenia na licznik `candidates.rejected` w ledgerze."""
    return {
        "window_regression": "guard_window",
        "g1_delay": "guard_g1_delay",
        "g1_unevaluable": "guard_unevaluable",
        "g2_delta": "guard_g2_delta",
        "g2_cap": "guard_g2_cap",
        "g2_unevaluable": "guard_unevaluable",
        "g3_gain": "guard_g3_gain",
    }.get(reason or "")


def empty_rejection_counters() -> Dict[str, int]:
    return {k: 0 for k in ("guard_window", "guard_g1_delay", "guard_g2_delta",
                           "guard_g2_cap", "guard_g3_gain", "guard_unevaluable")}


def summarize(results: List[GuardResult]) -> Dict[str, int]:
    """Zbiorczy licznik werdyktów (diagnostyka cienia)."""
    out = empty_rejection_counters()
    for r in results:
        key = rejection_counter_key(r.reason)
        if key:
            out[key] += 1
    return out
