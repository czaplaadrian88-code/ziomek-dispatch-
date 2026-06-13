"""Test scoringu na 3 scenariuszach (LEGACY — API check_feasibility WYCOFANE).

De-erozja 2026-06-13 (auton/legacy-test-fixes): ten skrypt woła `check_feasibility(
courier, restaurant, bag_drop_coords=..., bag_size=...)` — krotka-API które ZOSTAŁO
USUNIĘTE. Obecny silnik feasibility to `check_feasibility_v2(courier_pos, bag,
new_order, now, shift_end, ...)` oparty o obiekty OrderSim — kontrakt fundamentalnie
niekompatybilny (nie da się zmapować 1:1 bez przepisania scenariuszy na OrderSim).
Skoring (`score_candidate`) dalej istnieje, ale scenariusze są przeplecione z martwym
`check_feasibility`. Pokrycie tych zachowań przejęły: `test_decision_engine_f21.py`
(feasibility R1/R5/R6/R8 na OrderSim) + `test_scoring*.py` (scoring components).

Zamiast usuwać po cichu: SKIP z jawnym powodem (zgodnie z polityką dla wycofanych
funkcji). Pełny port scenariuszy na check_feasibility_v2 = osobne zadanie (TECH_DEBT).

Historyczny scenariusz (do ewentualnego portu):
  1. IDEALNY: kurier pusty, blisko restauracji, świeża zmiana
  2. GRANICZNY: kurier z 2 paczkami, 2 km do restauracji, kąt ~150°, 18 min w bagu
  3. WASILKÓW: kurier w centrum, restauracja ~12 km, 3 paczki w bagu w centrum
"""
import pytest


def test_scoring_scenarios_legacy_check_feasibility_removed():
    pytest.skip(
        "LEGACY: check_feasibility (krotka-API) usuniete na rzecz "
        "check_feasibility_v2 (OrderSim-API). Scenariusze pokryte przez "
        "test_decision_engine_f21.py + test_scoring*.py. Port = osobne zadanie."
    )
