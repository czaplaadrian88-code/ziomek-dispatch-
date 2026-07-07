"""INV carried-first (silnik) — `route_simulator_v2._sticky_sequence_plan` z niepustym
workiem NIGDY nie ustawia ODBIORU nowego zlecenia na czoło trasy.

Reguła Adriana (Z-RULE, „kryminał" 2026-06-13): kurier który wiezie już jedzenie
NIE zawraca do NOWEJ restauracji przed dowiezieniem/kontynuacją niesionego.
W silniku egzekwuje to `lock_first` w ścieżce sticky (V3.19d saved-plans): gdy
`sticky_bag_idxs` niepuste, enumeracja pozycji nowego odbioru POMIJA pozycję 0
(oraz pozycję 0 dostawy, gdy odbiór new już wykonany). Efekt: KAŻDA wyliczona
sekwencja zaczyna się od NIESIONEGO stopu, nie od nowego odbioru.

To bliźniacza ścieżka do `plan_recheck._coalesce_same_pickup_nodes`/`_relax_carried_first`
(pokryte osobno) — TU pilnujemy SILNIKOWEJ enumeracji sticky, która była pusta.

Metoda (deterministyczna, bez OSRM/coords): podmieniamy `_plan_from_sequence` na
przechwytywacz sekwencji i `_select_best_with_tie_breaker` na trywialny wybór —
asertujemy INWARIANT na WSZYSTKICH enumerowanych sekwencjach (nie na 1 wybranej).

Co złamie test (mutation-probe):
  - usunięcie `if lock_first and p_pos == 0: continue` → pojawia się sekwencja
    [new_pickup, ...] (zawrót do nowej restauracji) → RED,
  - usunięcie `if lock_first and p_pos is None and d_pos == 0: continue` → dostawa
    new na czele przy już-odebranym → RED,
  - zahardkodowanie lock_first=True (zawsze) → test pustego worka (lock warunkowy) RED.
"""
from __future__ import annotations

import pytest

from dispatch_v2 import route_simulator_v2 as RS

# indeksy węzłów (arbitralne, byle rozłączne): niesione dropy vs nowy pickup/dostawa
_STICKY = [10, 11]     # 2 niesione dropoffy (worek)
_NEW_PICKUP = 20
_NEW_DELIVERY = 21


def _capture(monkeypatch):
    """Przechwyć KAŻDĄ sekwencję trafiającą do _plan_from_sequence (bez realnego planu)."""
    seqs: list[list[int]] = []

    def _fake_plan_from_sequence(candidate, nodes, leg_min, new_order, bag, now, sla_minutes):
        seqs.append(list(candidate))
        return {"seq": list(candidate)}  # placeholder — treść nieistotna dla inwariantu

    def _fake_select(plans, now, nodes=None):
        return plans[0] if plans else None

    monkeypatch.setattr(RS, "_plan_from_sequence", _fake_plan_from_sequence)
    monkeypatch.setattr(RS, "_select_best_with_tie_breaker", _fake_select)
    return seqs


def _run(monkeypatch, *, sticky, new_pickup_idx, new_delivery_idx):
    seqs = _capture(monkeypatch)
    RS._sticky_sequence_plan(
        nodes=[{} for _ in range(30)], leg_min=None,
        sticky_bag_idxs=list(sticky),
        new_pickup_idx=new_pickup_idx, new_delivery_idx=new_delivery_idx,
        new_order=None, bag=[object() for _ in sticky],
        now=None, sla_minutes=35.0,
    )
    return seqs


def test_new_pickup_never_at_front_with_nonempty_bag(monkeypatch):
    """Niepusty worek: ŻADNA enumerowana sekwencja nie zaczyna się od nowego odbioru
    ani dostawy — czoło zawsze należy do NIESIONEGO stopu."""
    seqs = _run(monkeypatch, sticky=_STICKY,
                new_pickup_idx=_NEW_PICKUP, new_delivery_idx=_NEW_DELIVERY)
    assert seqs, "sanity: enumeracja sticky nie wyprodukowała żadnej sekwencji"
    for s in seqs:
        assert s[0] in _STICKY, (
            f"carried-first złamane: sekwencja {s} zaczyna się od nie-niesionego stopu "
            "(kurier z jedzeniem zawraca do nowej restauracji)"
        )
    assert all(s[0] != _NEW_PICKUP and s[0] != _NEW_DELIVERY for s in seqs)


def test_new_delivery_never_at_front_when_new_already_picked(monkeypatch):
    """Gdy nowe zlecenie już odebrane (new_pickup_idx=None): jego DOSTAWA też nie może
    być na czole przy niepustym worku (drugi warunek lock_first)."""
    seqs = _run(monkeypatch, sticky=_STICKY,
                new_pickup_idx=None, new_delivery_idx=_NEW_DELIVERY)
    assert seqs
    for s in seqs:
        assert s[0] != _NEW_DELIVERY, (
            f"sekwencja {s} zaczyna się od dostawy nowego (przy niesionym worku)")
        assert s[0] in _STICKY


def test_pickup_precedes_its_delivery_in_every_sequence(monkeypatch):
    """Sanity enumeracji: w każdej sekwencji odbiór nowego poprzedza jego dostawę
    (nigdy dostawa przed odbiorem)."""
    seqs = _run(monkeypatch, sticky=_STICKY,
                new_pickup_idx=_NEW_PICKUP, new_delivery_idx=_NEW_DELIVERY)
    for s in seqs:
        assert s.index(_NEW_PICKUP) < s.index(_NEW_DELIVERY), (
            f"dostawa przed odbiorem w {s}")


def test_lock_is_conditional_empty_bag_allows_pickup_first(monkeypatch):
    """Kontrola warunkowości: PUSTY worek (sticky=[]) → lock_first NIE działa →
    dozwolona sekwencja z nowym odbiorem na czole.

    Mutation-probe dla „lock_first=True na sztywno": gdyby lock był bezwarunkowy,
    tu też nie byłoby czoła-odbioru → test RED. Dowodzi, że lock zależy od worka."""
    seqs = _run(monkeypatch, sticky=[],
                new_pickup_idx=_NEW_PICKUP, new_delivery_idx=_NEW_DELIVERY)
    assert seqs
    assert any(s[0] == _NEW_PICKUP for s in seqs), (
        "pusty worek powinien dopuszczać nowy odbiór na czole (lock warunkowy)")


if __name__ == "__main__":  # standalone convenience (nie-script-style: ma def test_*)
    import sys
    raise SystemExit(pytest.main([__file__, "-q"]))
