"""INV-SRC-LEXQUAL / K2 — geometria (SOFT tie-break) NIE przeskakuje twardej struktury
grup w selektorze kanonicznym `objm_lexr6.pick()`.

K2 (geometria w lex_qual, żywa od 2026-07-05 za ENABLE_LEXQUAL_GEOMETRY_TIEBREAK)
dodaje `deliv_spread_km` jako OSTATNI człon klucza jakości. Docstring lex_qual:
„SOFT tie-break wewnątrz puli, którą HARD już przepuścił; INV-LAYER-5". Istniejące
testy (`test_l6c_geometry_claim`) pilnują SAMEGO klucza `lex_qual` (kolejność w obrębie
listy). NIEpokryty był kontrakt SELEKTORA `pick()`: geometria rozstrzyga wyłącznie
W GRUPIE (tier × bucket) zwycięzcy score — NIGDY nie promuje kandydata z GORSZEGO
tieru/bucketu (twarda bramka), choćby miał idealną geometrię i lepszy czas R6.

To jest bezpośrednio „SOFT nie osłabia HARD" na warstwie selekcji: bramka tier
(late_pickup: nie psuj umówionego odbioru) i bucket (V3.16 informed>other>blind) są
TWARDE względem geometrii. Gdyby ktoś zmienił `pick()` tak, że min(lex_qual) liczy
po CAŁEJ puli zamiast po grupie zwycięzcy — geometria/lex „przeskoczyłyby" bramkę
tier/bucket. Ten test to łapie.

Co złamie test (mutation-probe):
  - `pick()` liczące min(lex_qual) po całym `feasible` zamiast po `group_of(winner)`
    → kandydat z gorszego tieru z lepszą geometrią wygrywa → RED (2 niezależne testy),
  - usunięcie/odwrócenie członu geometrii → within-group tie-break ON≠OFF pada.

Determinizm: czyste `_Cand` z metrykami; flaga geometrii pinowana monkeypatchem
(NIE flags.json), klasyfikatory tier/bucket wstrzyknięte jako lambdy z metryk.
"""
from __future__ import annotations

from dispatch_v2 import objm_lexr6


class _Cand:
    """Lekki kandydat: tier/bucket sterują GRUPOWANIEM, r6/spread — kluczem lex_qual."""

    def __init__(self, cid, *, tier=0, bucket=0, r6=5.0, spread=0.0,
                 committed=0.0, new_late=0.0):
        self.courier_id = cid
        self.score = 0.0
        self.metrics = {
            "tier": tier,
            "bucket": bucket,
            "objm_r6_breach_max_min": r6,
            "late_pickup_committed_max": committed,
            "new_pickup_late_min": new_late,
            "deliv_spread_km": spread,
        }


# klasyfikatory wstrzykiwane do pick(): tier/bucket WPROST z metryk (jedno źródło grupy)
_TIER = lambda c: c.metrics["tier"]           # noqa: E731
_BUCKET_FN = lambda c: c.metrics["bucket"]    # noqa: E731
_FALSE = lambda c: False                      # noqa: E731  (is_informed/blind/pre — nieużywane gdy bucket_fn)


def _pick(feasible):
    return objm_lexr6.pick(
        feasible,
        late_pickup_tier=_TIER,
        is_informed=_FALSE, is_blind_empty=_FALSE, is_pre_shift=_FALSE,
        bucket_fn=_BUCKET_FN,
    )


def _geom_on(monkeypatch):
    monkeypatch.setattr(
        objm_lexr6.C, "decision_flag",
        lambda name, default=False: name == "ENABLE_LEXQUAL_GEOMETRY_TIEBREAK",
    )
    monkeypatch.setattr(
        objm_lexr6.C, "flag",
        lambda name, default=False: 0.0 if name == "LEXQUAL_TIME_QUANT_MIN" else default,
    )


def test_geometry_breaks_tie_within_group_on_not_off(monkeypatch):
    """W obrębie JEDNEJ grupy (tier0×bucket0), przy równej osi czasowej: geometria
    rozstrzyga TYLKO przy fladze ON (ciaśniejszy worek), OFF → stabilny pierwszy.

    Chroni: człon geometrii faktycznie działa w selektorze (ON≠OFF na decyzji).
    """
    wide = _Cand("WIDE", tier=0, bucket=0, r6=5.0, spread=18.0)
    tight = _Cand("TIGHT", tier=0, bucket=0, r6=5.0, spread=1.0)
    feasible = [wide, tight]  # wide = feasible[0] (kotwica grupy/score)
    # OFF (default): klucz 3-elem., remis czasowy → stabilny min = pierwszy (WIDE)
    assert _pick(feasible).courier_id == "WIDE"
    # ON: geometria rozstrzyga → TIGHT
    _geom_on(monkeypatch)
    assert _pick(feasible).courier_id == "TIGHT"


def test_geometry_cannot_promote_from_worse_tier(monkeypatch):
    """HARD > SOFT: kandydat z GORSZEGO tieru (psuje umówiony odbiór) z idealną
    geometrią I lepszym R6 NIE wygrywa — bo jest POZA grupą zwycięzcy score.

    Mutation-probe: gdyby pick() liczyło min(lex_qual) po całej puli (bez group_of),
    B (r6=1, spread=0.1) wygrałby A → RED. Grupa tier×bucket to twarda bramka.
    """
    _geom_on(monkeypatch)
    a = _Cand("A", tier=0, bucket=0, r6=20.0, spread=30.0)   # zwycięzca score (feasible[0])
    b = _Cand("B", tier=2, bucket=0, r6=1.0, spread=0.1)     # lepszy czas+geometria, ale tier 2
    assert _pick([a, b]).courier_id == "A", (
        "geometria/lex przeskoczyły bramkę TIER (SOFT osłabił HARD) — pick() musi "
        "wybierać w GRUPIE tier×bucket zwycięzcy score"
    )


def test_geometry_cannot_promote_from_worse_bucket(monkeypatch):
    """Analogicznie dla bucketu (V3.16 informed>other>blind): geometria nie promuje
    kandydata z gorszego bucketu ponad zwycięzcę score jego grupy."""
    _geom_on(monkeypatch)
    a = _Cand("A", tier=0, bucket=0, r6=20.0, spread=30.0)   # feasible[0]
    b = _Cand("B", tier=0, bucket=2, r6=1.0, spread=0.1)     # inny bucket
    assert _pick([a, b]).courier_id == "A", (
        "geometria przeskoczyła bramkę BUCKET — pick() musi trzymać się grupy zwycięzcy"
    )


def test_off_selector_ignores_spread_within_group(monkeypatch):
    """Bajt-parytet OFF na poziomie SELEKTORA: przy fladze OFF spread jest ignorowany
    (klucz 3-elem.), więc remis czasowy rozstrzyga stabilna kolejność, nie geometria.

    Mutation-probe: gdyby geometria wyciekła do klucza przy OFF, pierwszy≠TIGHT-first."""
    monkeypatch.setattr(objm_lexr6.C, "decision_flag", lambda name, default=False: False)
    a = _Cand("A", tier=0, bucket=0, r6=7.0, spread=25.0)  # feasible[0], szeroki
    b = _Cand("B", tier=0, bucket=0, r6=7.0, spread=0.2)   # ciaśniejszy, ale OFF ignoruje
    assert _pick([a, b]).courier_id == "A", "OFF nie może rozstrzygać geometrią"
