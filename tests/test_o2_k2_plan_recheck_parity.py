"""S2/O2 prereq K2 cz.2 (2026-07-05) — parytet plan_recheck compare-and-keep.

Przegląd `_kind()` 03.07 wskazał `plan_recheck.py:761/800/1931/1981` jako
K2-wrażliwe (inne liczniki sla → inne akceptacje regenów). Analiza ETAP 1:
- `_o2_key` (sweep + committed-tiebreak ~754-800): pod **K1=ON**
  (`ENABLE_O2_CAPZ_RESEQ`) klucz = (over_z, o2_score, dur) — **NIE czyta
  sla_violations** → K2 jest tam no-opem Z KONSTRUKCJI. Pod K1=OFF klucz
  = (sla, dur) — K2-wrażliwy. ⇒ sekwencja flipów **K1 PRZED K2** nie jest
  kosmetyką: to ona daje parytet plan_recheck.
- linia ~1972 = `_bug4_reseq_shadow` (LOG-ONLY, zero decyzji) — K2 przesuwa
  wyłącznie telemetrię shadow, odnotowane w werdykcie.

Testy przypinają obie własności (mutation-style: mutujemy sla na planie
i sprawdzamy wpływ na klucz) — jak ktoś doda sla z powrotem do gałęzi ON
albo wytnie z OFF, test krzyczy i unieważnia rekomendację sekwencji.
"""
import sys
import types

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import plan_recheck as PR  # noqa: E402


def _plan(sla=0, dur=30.0, o2=5.0, max_age=10.0):
    return types.SimpleNamespace(
        sla_violations=sla, total_duration_min=dur,
        o2_score=o2, max_carried_age=max_age, sequence=["a"], pickup_at={})


def _mk_o2_key(o2_on, z=20.0):
    """Zbuduj _o2_key identycznie jak _gen_one_bag_plan (closure na _o2_on/_o2_z)
    — funkcja jest zagnieżdżona, więc odtwarzamy ją przez exec ciała? NIE:
    odtwarzamy semantykę przez wywołanie na żywym kodzie — patrz
    test_source_pins_key_shape (pin tekstowy) + testy semantyczne niżej na
    lokalnej kopii 1:1. Kopia zsynchronizowana pinem tekstowym."""
    def _o2_key(p):
        if o2_on:
            _over_z = 1 if (p.max_carried_age or 0.0) > z else 0
            _o2 = p.o2_score if p.o2_score is not None else float("inf")
            return (_over_z, _o2, round(p.total_duration_min, 3))
        return (p.sla_violations, round(p.total_duration_min, 3))
    return _o2_key


def test_source_pins_key_shape():
    """Pin tekstowy 1:1 z plan_recheck._gen_one_bag_plan._o2_key — jak źródło
    się zmieni, kopia w testach semantycznych przestaje być wiarygodna i ten
    test to zgłasza (aktualizuj OBA razem)."""
    import inspect
    src = inspect.getsource(PR)
    assert "return (_over_z, _o2, round(p.total_duration_min, 3))" in src
    assert "return (p.sla_violations, round(p.total_duration_min, 3))" in src


def test_k1_on_key_insensitive_to_sla_anchor():
    """K1=ON: mutacja sla_violations (efekt K2) NIE zmienia klucza porównań
    → parytet compare-and-keep Z KONSTRUKCJI pod sekwencją K1→K2."""
    key = _mk_o2_key(o2_on=True)
    assert key(_plan(sla=0)) == key(_plan(sla=7)), (
        "klucz O2 zaczął czytać sla — parytet K2-pod-K1 przestał być "
        "z konstrukcji, replay plan_recheck znów obowiązkowy")


def test_k1_off_key_sensitive_to_sla_anchor():
    """K1=OFF: mutacja sla ZMIENIA klucz → dowód, że kolejność K1 przed K2
    jest WYMAGANA (bez K1, K2 zmienia akceptacje regenów w plan_recheck)."""
    key = _mk_o2_key(o2_on=False)
    assert key(_plan(sla=0)) != key(_plan(sla=7))


def test_k1_on_key_reads_o2_axes():
    """Sanity klucza ON: cap-Z prymarny (over_z bije o2), potem o2, potem dur."""
    key = _mk_o2_key(o2_on=True, z=20.0)
    under = _plan(o2=50.0, max_age=15.0)   # pod capem, gorszy o2
    over = _plan(o2=1.0, max_age=25.0)     # nad capem, lepszy o2
    assert key(under) < key(over)
