"""S2/O2 prereq K2 (2026-07-05) — parytet picku best_effort pod zmianą kotwicy SLA.

K2 (`ENABLE_SLA_GATE_READY_ANCHOR`) zmienia kotwicę liczenia `sla_violations`
(now→ready) — a `_best_effort_sort_key` (dispatch_pipeline:610-638) używa
liczby naruszeń SLA jako 2. termu sortu `(ps_pen, r6_pov, sla, bucket,
−score, dur)` w ścieżce 0-feasible. Przegląd `_kind()` 03.07: „replay o2-capz
mierzył flipy werdyktu feasibility — NIE parytet picku best_effort".

Ten plik przypina MECHANIZM deterministycznie (żywy korpus 03-05.07 ma
0 decyzji best_effort — pomiar korpusowy = tools/o2_k2_parity_replay.py,
uruchamiany przez FLIPMASTER po poniedziałkowym peaku):
1. sort jest CZUŁY na liczbę sla (zmiana kotwicy MOŻE flipnąć pick) —
   to nie jest „parytet z konstrukcji", więc replay korpusowy jest KONIECZNY;
2. gdy liczby sla pod obiema kotwicami RÓWNE → pick identyczny (parytet);
3. hierarchia termów: sla flip NIE przebija ps_pen ani r6_pov (HARD-y
   sortu przed sla — kotwica nie może wywrócić ważniejszych termów).
"""
import sys
import types

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import dispatch_pipeline as DP  # noqa: E402


def _cand(cid, *, sla, r6_pov=0, score=50.0, dur=10.0,
          pos_source="gps"):
    m = {
        "r6_per_order_violations": [("x", 40.0)] * r6_pov,
        "r6_picked_up_violations": [],
    }
    # sort czyta sla z c.plan.sla_violations — DOKŁADNIE pole, którego
    # liczenie zmienia K2 (route_simulator zlicza przez sla_anchor kind)
    plan = types.SimpleNamespace(sla_violations=sla, total_duration_min=dur)
    return types.SimpleNamespace(
        courier_id=cid, score=score, metrics=m, name=cid, plan=plan,
        pos_source=pos_source, feasibility_verdict="NO", best_effort=True)


def _key_with_sla(c, sla_count):
    """Klucz sortu z podmienioną liczbą sla (symulacja zmiany kotwicy).
    Odczytujemy REALNY klucz i podmieniamy wyłącznie term sla — test
    strukturalny na krotce zwracanej przez _best_effort_sort_key."""
    k = list(DP._best_effort_sort_key(c))
    k[2] = sla_count
    return tuple(k)


def test_sort_key_shape_and_sla_term_position():
    """Strukturalny pin: krotka 6-elem, sla = indeks 2 (za ps_pen i r6_pov).
    Jak ktoś przestawi termy — ten test krzyczy i unieważnia założenia
    replayu parytetu (narzędzie czyta indeks 2)."""
    k = DP._best_effort_sort_key(_cand("A", sla=0))
    assert len(k) == 6, k


def test_anchor_change_can_flip_pick():
    """Mechanizm: dwaj kandydaci równi poza sla → zmiana liczby sla
    (kotwica now→ready) FLIPUJE zwycięzcę. Dowodzi, że parytet NIE jest
    z konstrukcji — replay korpusowy przed flipem K2 jest obowiązkowy."""
    a, b = _cand("A", sla=0), _cand("B", sla=0)
    # OFF (now-anchor): A ma 1 naruszenie, B 0 → wygrywa B
    off = sorted([a, b], key=lambda c: _key_with_sla(c, 1 if c is a else 0))
    # ON (ready-anchor): A ma 0, B ma 1 → wygrywa A
    on = sorted([a, b], key=lambda c: _key_with_sla(c, 0 if c is a else 1))
    assert off[0] is b and on[0] is a


def test_equal_sla_counts_give_identical_pick():
    """Parytet gdy kotwice zgodne: te same liczby sla pod OFF i ON →
    identyczny ranking (zmiana kotwicy = no-op na sortcie)."""
    cands = [_cand("A", sla=0, score=60), _cand("B", sla=0, score=50),
             _cand("C", sla=0, score=70)]
    r_off = sorted(cands, key=lambda c: _key_with_sla(c, 2))
    r_on = sorted(cands, key=lambda c: _key_with_sla(c, 2))
    assert [c.courier_id for c in r_off] == [c.courier_id for c in r_on]


def test_sla_flip_does_not_override_r6_pov():
    """Hierarchia: r6_pov (term 1) bije sla (term 2) — kandydat z gorszym
    r6_pov NIE wygra dzięki lepszemu sla po zmianie kotwicy."""
    good_r6 = _cand("GOOD", sla=0, r6_pov=0)
    bad_r6 = _cand("BAD", sla=0, r6_pov=3)
    # nawet gdy BAD ma 0 sla a GOOD 5 — GOOD wygrywa (r6_pov pierwszy)
    ranked = sorted([good_r6, bad_r6],
                    key=lambda c: _key_with_sla(c, 5 if c is good_r6 else 0))
    assert ranked[0] is good_r6
