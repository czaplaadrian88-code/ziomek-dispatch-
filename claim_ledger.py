"""Claim ledger — JEDNO źródło wirtualnej alokacji zlecenia do worka kuriera (L6.C3, 2026-07-04).

Wyekstrahowane z `tools/pending_global_resweep._tentative_assign` (R2 ROOT-8: „nowe zlecenie
i przerzut dzielą JEDNĄ de-konflikcję globalną — TA SAMA global_allocate, wspólny import,
nie 2. kopia"). Konsumenci:
- `tools/pending_global_resweep.global_allocate` (resweep wiszących, shadow + overlay konsoli),
- pośrednio `tools/reassignment_global_select` (de-pile przerzutu; przez global_allocate),
- `shadow_dispatcher._tick` za flagą `ENABLE_ENGINE_CLAIM_LEDGER` (INV-LAYER-4: kolejne eventy
  TEGO SAMEGO ticku widzą claim zwycięzcy poprzedniego, zamiast oceniać niemutowaną flotę —
  korzeń pile-onu: jeden kurier proponowany 127×/32 zlecenia, g_maxpile=7).

Kierunek importu: silnik ← tools (nigdy odwrotnie). Moduł jest LIŚCIEM (zero importów
silnika) — bezpieczny dla każdego procesu.
"""
from __future__ import annotations

import copy as _copy
from typing import Any, Dict


def bag_entry_from_order(rec: dict) -> dict:
    """Wirtualny wpis do worka kuriera (kopia rekordu zlecenia, status=assigned)."""
    e = dict(rec)
    e["status"] = "assigned"
    e["commitment_level"] = "assigned"
    return e


def tentative_assign(fleet: Dict[str, Any], cid: str, order_rec: dict) -> Dict[str, Any]:
    """Płytka kopia floty z `order_rec` DOklejonym do worka kuriera `cid`
    (kontrfaktyk „gdyby ten kurier dostał to zlecenie"). NIE mutuje wejścia."""
    out = dict(fleet)
    cs = out.get(cid)
    if cs is None:
        return out
    cs2 = _copy.copy(cs)
    cs2.bag = list(cs.bag or []) + [bag_entry_from_order(order_rec)]
    out[cid] = cs2
    return out


# ─────────────────────────────────────────────────────────────────────────────
# INV-FEAS-NO-DOUBLE-BOOK — tripwier spójności claim-ledger (Sprint B, 2026-07-08)
# ─────────────────────────────────────────────────────────────────────────────
# Kontrakt ② ZIOMEK_INVARIANTS.md l.36: „kurier nie zaproponowany do 2 SPRZECZNYCH
# zleceń w 1 ticku (greedy pile-on, K6 — global de-konflikcja)". Mechanizm de-konflikcji
# = `tentative_assign` doklejający zwycięzcę do worka między eventami sweepu/ticku, aby
# KOLEJNA ocena widziała obciążenie (a nie w kółko proponowała temu samemu pustemu
# kurierowi — pomiar korzenia: 447 proponowany 127×/32 zlecenia, g_maxpile=7).
#
# INWARIANT SPRAWDZALNY: w jednym przebiegu de-konflikcji KOLEJNE claimy TEGO SAMEGO
# kuriera muszą widzieć worek rosnący DOKŁADNIE o 1 (poprzedni claim doklejony przez
# tentative_assign). Ślad = [(cid, oid, bag_seen)] w kolejności alokacji, gdzie
# `bag_seen` = rozmiar worka kuriera, który OCENA użyta do tego przypisania widziała
# (tj. `len(fleet[cid].bag)` w chwili claimu, PRZED doklejeniem).
#   • Poprawne zachowanie: bag_seen[k] == bag_seen[k-1] + 1 per kurier → ZERO naruszeń.
#     (Legalny bundling — 2-3 zlecenia jednemu kurierowi — ZAWSZE rośnie o 1: nie fałszuje.)
#   • Regres (flota niemutowana / pile-on): bag_seen[k] <= bag_seen[k-1] → NARUSZENIE
#     („stale" — kolejny claim widzi ten sam / mniejszy worek = de-konflikcja padła).
# To jest STRAŻNIK (obserwator), NIE nowa reguła selekcji — NIE zmienia allocation.


def verify_no_stale_claim(trace):
    """Zweryfikuj ślad claimów sweepu/ticku wg INV-FEAS-NO-DOUBLE-BOOK.

    trace: iterowalne [(cid, oid, bag_seen)] w KOLEJNOŚCI alokacji (bag_seen = rozmiar
    worka, który ocena zwycięska widziała, PRZED doklejeniem tego zlecenia).
    Zwraca listę naruszeń (pusta = OK). Każde naruszenie:
        {"cid", "oid_prev", "oid", "seen_prev", "seen", "expected", "kind"}
    gdzie kind ∈ {"stale", "gap"}:
        • "stale" = seen <= seen_prev (REALNY korzeń: worek nie urósł → pile-on),
        • "gap"   = seen >  seen_prev+1 (strukturalnie niemożliwe przy 1 tentative/claim;
                    flagowane dla kompletności — sygnalizuje policzenie worka podwójnie).
    Pure, zero I/O, zero importów silnika (moduł-liść)."""
    last = {}  # cid -> (oid, bag_seen)
    violations = []
    for entry in trace:
        cid, oid, seen = entry[0], entry[1], entry[2]
        prev = last.get(cid)
        if prev is not None:
            p_oid, p_seen = prev
            try:
                expected = p_seen + 1
            except TypeError:
                expected = None
            if seen != expected:
                kind = "stale" if (expected is None or seen <= p_seen) else "gap"
                violations.append({
                    "cid": cid, "oid_prev": p_oid, "oid": oid,
                    "seen_prev": p_seen, "seen": seen,
                    "expected": expected, "kind": kind,
                })
        last[cid] = (oid, seen)
    return violations


def check_sweep_trace(trace, log=None, context=""):
    """Uruchom `verify_no_stale_claim` i (przy naruszeniu) zaloguj GŁOŚNO. Zwraca listę
    naruszeń. Log-loud: naruszenie = `log.error` z kontekstem; sam nie rzuca (twardą
    blokadę robi caller przez osobną flagę). Fail-soft: log=None → tylko zwrot listy."""
    viol = verify_no_stale_claim(trace)
    if viol and log is not None:
        log.error(
            "CLAIM_LEDGER_INVARIANT breach [%s]: %d naruszen(ia) INV-FEAS-NO-DOUBLE-BOOK "
            "(kurier widzi worek nierosnacy o +1 miedzy claimami = pile-on/stale): %r",
            context or "?", len(viol), viol[:8],
        )
    return viol
