"""INV-FEAS-R6-ONE-SOURCE (ZIOMEK_INVARIANTS.md kontrakt ②, l.28) — strażnik SPÓJNOŚCI
progu termicznego 35-min: cała RODZINA twardych dźwigni termicznych R6/SLA trzyma
JEDEN dial, a mechanizmy z INNYCH osi (eskalacja-3 = 40, bag-size caps = liczba
zleceń) są od niego ODDZIELONE.

Kontekst (dług nazwany w inwariantach): próg 35 min żyje w kilku stałych naraz —
`BAG_TIME_HARD_MAX_MIN` (R6 termik), `DEFAULT_SLA_MINUTES` (bramka SLA),
`C2_PER_ORDER_THRESHOLD_MIN` (per-order 35-gate), `O2_OVERAGE_CAP_MIN` (instrument
bundle_calib). Doktryna Adriana 2026-05-10: „35 min jest JEDYNĄ twardą regułą"
(feasibility_v2 płaska termika). Jeśli ktoś podbije JEDEN z tych dialów, a zapomni
o bliźniakach → bramka ≠ scoring/instrument = cichy rozjazd (dokładnie klasa bugów,
którą inwariant ma pilnować). Referowany w ZIOMEK_INVARIANTS.md „🟢 test_overage_cap_
equals_engine_dial" NIE ISTNIEJE w repo (zweryfikowane) — slot był realnie pusty.

⚠ Świadome ROZRÓŻNIENIE (inwariant to podkreśla, byśmy NIE zlali osi):
  • `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN` = 40 → cap SELEKCJI kuriera w eskalacji-3
    (ratunek przy 0 feasible), INNY mechanizm niż termika. MUSI być > dial termiczny,
    NIGDY z nim zrównany (zrównanie = pomylenie „35 normalnie / 40 tylko alarm").
  • `HARD_TIER_BAG_CAP` (gold6/std5/slow4) = oś LICZBY zleceń, nie minut — nie może
    przypadkiem trafić na dial 35.

Co złamie test (mutation-probe):
  - podbicie któregokolwiek z 4 dialów termicznych bez bliźniaków → RED (rozjazd),
  - zrównanie eskalacyjnego 40 z termicznym 35 → RED (pomieszanie osi),
  - wpisanie 35 do bag-size cap (oś liczby zleceń) → RED.

Test czyta STAŁE MODUŁÓW (źródło defaultów, env nieustawiony w teście = wartości
kanoniczne). To strażnik konfiguracji, nie zachowania — dial-drift jest defektem
konfiguracji, więc pinujemy właśnie konfigurację (spójność rodziny).
"""
from __future__ import annotations

from dispatch_v2 import common as C
from dispatch_v2 import feasibility_v2 as F

# Kanoniczny dial R6 (Adrian 2026-05-10: „35 min jedyną twardą regułą").
_R6_DOCTRINE_MIN = 35.0

# Rodzina TERMICZNA (minuty) — musi trzymać JEDEN dial. (nazwa → wartość, moduł)
_THERMAL_FAMILY = {
    "common.BAG_TIME_HARD_MAX_MIN": float(C.BAG_TIME_HARD_MAX_MIN),
    "feasibility_v2.DEFAULT_SLA_MINUTES": float(F.DEFAULT_SLA_MINUTES),
    "feasibility_v2.C2_PER_ORDER_THRESHOLD_MIN": float(F.C2_PER_ORDER_THRESHOLD_MIN),
    "common.O2_OVERAGE_CAP_MIN": float(C.O2_OVERAGE_CAP_MIN),
}


def test_thermal_family_shares_single_dial():
    """WSZYSTKIE twarde progi termiczne R6/SLA/C2/O2 = ten sam dial (jedno źródło).

    Regresja łapana: podbicie/zmiana jednego progu bez bliźniaków (bramka≠scoring).
    """
    vals = set(_THERMAL_FAMILY.values())
    assert len(vals) == 1, (
        "INV-FEAS-R6-ONE-SOURCE: rodzina termiczna 35-min ROZJECHANA (nie jedno "
        f"źródło): {_THERMAL_FAMILY!r}. Podbito jeden dial bez bliźniaków? "
        "Wyrównaj wszystkie do JEDNEGO źródła (docelowo sla_anchor)."
    )


def test_thermal_dial_equals_doctrine_35():
    """Dial termiczny = 35 min (doktryna Adriana 2026-05-10 „płaska R6").

    Jeśli 35 zmieni się ŚWIADOMIE (nowa kalibracja + ACK) → zaktualizuj tę kotwicę
    RAZEM z rodziną. Test istnieje, by zmiana dialu była WIDOCZNA (nie po cichu).
    """
    assert float(C.BAG_TIME_HARD_MAX_MIN) == _R6_DOCTRINE_MIN, (
        f"dial termiczny R6 = {C.BAG_TIME_HARD_MAX_MIN}, oczekiwano 35 "
        "(doktryna Adriana: 35 min jedyna twarda regula). Swiadoma zmiana => "
        "zaktualizuj kotwice RAZEM z rodzina + ACK."
    )


def test_escalation_cap_is_distinct_and_looser():
    """Cap eskalacji-3 (=40) to INNY mechanizm — MUSI być > dial termiczny, nigdy zrównany.

    Chroni przed pomyleniem „35 normalnie / 40 TYLKO alarm" (inwariant to explicytnie
    wyróżnia). Mutation-probe: zrównanie 40→35 albo termika→40 → RED.
    """
    esc = float(C.BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN)
    dial = float(C.BAG_TIME_HARD_MAX_MIN)
    assert esc > dial, (
        f"BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN ({esc}) NIE jest luźniejszy od dialu "
        f"termicznego ({dial}) — eskalacja-3 zlana z termiką (pomieszanie osi 35/40)."
    )


def test_bag_size_caps_are_counts_not_the_minute_dial():
    """`HARD_TIER_BAG_CAP` to oś LICZBY zleceń (gold6/std5/slow4), nie minut.

    Żadna wartość nie może przypadkiem wylądować na dialu 35 (co znaczyłoby zlanie
    osi „ile zleceń" z osią „ile minut"). Mutation-probe: cap=35 → RED.
    """
    caps = C.HARD_TIER_BAG_CAP
    assert isinstance(caps, dict) and caps, "HARD_TIER_BAG_CAP zniknął/zmienił typ?"
    for tier, cap in caps.items():
        assert 0 < float(cap) < 20, (
            f"tier {tier!r} cap={cap} — oś liczby zleceń wygląda jak minuty "
            "(zlana z dialem termicznym?)"
        )
        assert float(cap) != _R6_DOCTRINE_MIN, (
            f"tier {tier!r} bag-cap == 35 = pomylenie osi liczby zleceń z minutami"
        )
