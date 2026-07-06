"""A1 STRAŻNIK (VETO) — inwarianty kanonu w CI (advisory Faza 6.2, zad. 2).

Trzy warstwy:
 1. `tools/canon_static_check.py` na czystym repo → 0 naruszeń (0 false-positive).
 2. Mutation-probes (C13/C14): KAŻDE wstrzyknięte naruszenie kanonu (in-memory,
    dysk nietknięty) MUSI zostać wykryte — sonda przeżywająca = strażnik VOID.
 3. Inwarianty mechanizmów:
    - lock-atomowość claim-ledgera: bazowe no-mutation/single-source/ON≠OFF są
      w `test_l6c_geometry_claim.py` (NIE dublujemy — wzorzec #2); tu tylko
      krawędzie (claim-na-claim kumuluje, nieznany cid = no-op bez wyjątku).
    - defer-completion (W1, mechanizm JESZCZE nie istnieje): test PRZYGOTOWANY,
      skip do czasu pojawienia się modułu deferu — W1 MUSI go uzbroić
      (zero zleceń-sierot po deferze).
    - mode-consistency + parytet kopii kanonu trasy: RATCHETY w checkerze
      (druga definicja BAG_TIME_HARD_MAX_MIN / piąta kopia
      _apply_canon_order_invariants = VETO) — sondowane w warstwie 2.
"""
from __future__ import annotations

import importlib.util

import pytest

from dispatch_v2 import claim_ledger
from dispatch_v2.tools import canon_static_check as csc


# ── 1. czyste repo = 0 naruszeń ───────────────────────────────────────

def test_canon_clean_repo_zero_violations():
    viol = csc.run_checks()
    assert viol == [], f"A1 VETO — naruszenia kanonu w repo: {viol}"


# ── 2. mutation-probes: 100% wykrycia ────────────────────────────────

def test_canon_mutation_probes_all_killed():
    r = csc.selftest()
    survived = [lbl for lbl, p in r["probes"].items() if not p["killed"]]
    assert not r["clean_violations"], (
        f"false-positive na czystym repo: {r['clean_violations']}")
    assert not survived, (
        f"sondy PRZEŻYŁY (strażnik VOID, C13/C14): {survived}")
    # kanon sond: pełna klasa naruszeń (7 dialów + 2 ratchety + kasacja definicji)
    assert len(r["probes"]) >= 10, "skurczony zestaw sond — uzupełnij"


# ── 3a. lock-atomowość: krawędzie claim-ledgera ──────────────────────

class _CS:
    def __init__(self, bag=None):
        self.bag = list(bag or [])


def test_claim_on_claim_accumulates_without_input_mutation():
    fleet = {"447": _CS([{"order_id": "1", "status": "picked_up"}])}
    f1 = claim_ledger.tentative_assign(fleet, "447", {"order_id": "2"})
    f2 = claim_ledger.tentative_assign(f1, "447", {"order_id": "3"})
    # kumulacja w kolejnych warstwach…
    assert [b["order_id"] for b in f2["447"].bag] == ["1", "2", "3"]
    # …bez mutacji ŻADNEJ wcześniejszej warstwy (atomowość wirtualnej alokacji)
    assert [b["order_id"] for b in f1["447"].bag] == ["1", "2"]
    assert [b["order_id"] for b in fleet["447"].bag] == ["1"]
    # wpis claim ma status assigned (nie przenosi statusu źródła)
    assert f2["447"].bag[-1]["status"] == "assigned"


def test_claim_unknown_courier_is_noop_not_crash():
    fleet = {"447": _CS()}
    out = claim_ledger.tentative_assign(fleet, "999", {"order_id": "7"})
    assert out["447"].bag == [] and "999" not in out


# ── 3b. defer-completion (PRZYGOTOWANY — uzbroić w W1) ───────────────

_DEFER_MODULES = ("dispatch_v2.defer_ledger", "dispatch_v2.defer_engine",
                  "dispatch_v2.core.defer")


def _find_defer_module():
    for name in _DEFER_MODULES:
        try:
            if importlib.util.find_spec(name) is not None:
                return name
        except (ImportError, ModuleNotFoundError, ValueError):
            continue
    return None


def test_defer_completion_guard_armed_when_defer_exists():
    """INV-DEFER-COMPLETION: każde zlecenie zdeferowane MUSI mieć finał
    (assign/deliver/koord-exception) — zero sierot. Mechanizm deferu (W1)
    jeszcze nie istnieje → skip. Gdy W1 doda moduł deferu, ten test PADNIE
    (moduł znaleziony, brak API) — wykonawca W1 musi go uzbroić, nie skasować."""
    mod = _find_defer_module()
    if mod is None:
        pytest.skip("mechanizm deferu (W1) jeszcze nie istnieje — inwariant czeka")
    raise AssertionError(
        f"moduł deferu {mod} istnieje — UZBRÓJ INV-DEFER-COMPLETION "
        "(iteracja ledgera deferów: każdy wpis ma terminalny finał, zero sierot)")
