"""AUTON-01 — bramka auto-assign (czysta funkcja, telemetria compute-zawsze).

Projekt: eod_drafts/2026-06-13/AUTON01_DESIGN.md (sekcja 3 = tabela bramek).

Kontrakt:
  evaluate_auto_assign(result, order_event, informed_pos_sources, flags=None)
    -> (would_auto_assign: bool, auto_block_reasons: list[str])

- CZYSTA funkcja: zero I/O, zero logowania, deterministyczna dla identycznych
  wejść (wzorzec auto_proximity_classifier / czasowka_proactive.score_selector).
- Liczona na KAŻDEJ decyzji niezależnie od flagi ENABLE_AUTO_ASSIGN
  (lekcja #186: shadow gate'owany flagą aplikacji nigdy nie zbiera danych).
- Zbiera WSZYSTKIE powody blokady (nie first-fail) — kalibracja E7 widzi
  pełny rozkład auto_block_reasons.
- Warstwa 1 = klasyfikator Fazy 7 (auto_route musi być "AUTO": progi
  margin E2-Z10 / score / tier / pool / C7 best_is_score_top / edge-cases).
- Warstwa 2 = twarde bramki AUTON-01 (G1, G3-G11 z designu).
- Bramki STANOWE (rate-cap, cooldown po PANEL_OVERRIDE, killswitch) NIE są tu
  liczone — nakłada je auto_assign_executor w chwili wykonania (design §5).

Wymaga: result PO przejściu przez dispatch_pipeline._classify_and_set_auto_route
(auto_route + auto_route_context wypełnione). Brak kontekstu = fail-closed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from dispatch_v2 import common as C


def _gate_numeric(flags: Optional[Dict[str, Any]], name: str) -> float:
    """Stała bramki: flags.json (hot override) → stała modułu common."""
    src = flags if isinstance(flags, dict) else {}
    try:
        return float(src.get(name, getattr(C, name)))
    except (TypeError, ValueError):
        return float(getattr(C, name))


def evaluate_auto_assign(
    result: Any,
    order_event: Optional[Dict[str, Any]],
    informed_pos_sources: Sequence[str],
    flags: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Bramka AUTON-01. Zwraca (would_auto_assign, auto_block_reasons)."""
    blocks: List[str] = []
    ev = order_event or {}

    # G1: KOORD/SKIP nigdy auto.
    verdict = getattr(result, "verdict", None)
    if verdict != "PROPOSE":
        blocks.append(f"verdict_not_propose:{verdict}")

    best = getattr(result, "best", None)
    if best is None:
        blocks.append("no_best")
        return False, blocks
    m: Dict[str, Any] = (getattr(best, "metrics", None) or {})

    # G2: klasyfikator Fazy 7 (margin Z-10, score floor, tier whitelist,
    # min pool, C7 best_is_score_top, HIGH_RISK 14-17, edge-cases).
    route = getattr(result, "auto_route", "ACK")
    if route != "AUTO":
        reason = (getattr(result, "auto_route_reason", "") or "")[:80]
        blocks.append(f"classifier_not_auto:{route}:{reason}")

    # G3: kontekst klasyfikatora — bez niego fail-closed (brak dowodu jakości).
    ctx = getattr(result, "auto_route_context", None) or {}
    if not ctx:
        blocks.append("no_auto_route_context")

    # G4: czasówka — pas i szelki do edge'a klasyfikatora (czas_odbioru >= 60).
    prep = ev.get("prep_minutes") or ev.get("czas_odbioru") or 0
    try:
        is_czasowka = float(prep) >= 60.0
    except (TypeError, ValueError):
        is_czasowka = False
    if is_czasowka or ctx.get("auto_route_czasowka"):
        blocks.append("czasowka")

    # G5: paczki / firmowe (catch-all rid=161 + konta paczkowe 232-236).
    aid = ev.get("address_id")
    try:
        aid_int = int(aid) if aid is not None else None
    except (TypeError, ValueError):
        aid_int = None
    if aid_int is not None and (
        aid_int in C.PACZKA_ADDRESS_IDS or aid_int in C.FIRMOWE_KONTO_ADDRESS_IDS
    ):
        blocks.append("paczka_firmowe")

    # G6: kurier w RAMPIE nowych (tier z kontekstu klasyfikatora).
    if ctx.get("auto_route_tier_best") == "new":
        blocks.append("new_courier_ramp")

    # G7: pozycja musi być informed (gps/bag-pochodne), nigdy blind/center;
    # store-replay nie jest żywym fixem (Z-06).
    pos_source = m.get("pos_source") or ctx.get("auto_route_pos_source_best")
    if pos_source not in tuple(informed_pos_sources):
        blocks.append(f"pos_not_informed:{pos_source}")
    if m.get("pos_from_store"):
        blocks.append("pos_from_store")

    # G8: late-pickup — propozycja przedłużenia czasu wymaga człowieka
    # (Adrian 31.05: Ziomek nie nadpisuje ustalonego czasu sam).
    if getattr(result, "pickup_extension_redirect", None) is not None:
        blocks.append("late_pickup_redirect")
    if m.get("late_pickup_committed_breach"):
        blocks.append("late_pickup_committed")
    if m.get("new_pickup_needs_extension"):
        blocks.append("late_pickup_extension")

    # G9: ryzyko R6 / commit-divergence — dwie nienaruszalne reguły
    # egzekwowane na finalnym zwycięzcy.
    if getattr(result, "best_effort_r6_redirect", None) is not None:
        blocks.append("r6_redirect")
    if getattr(result, "commit_divergence_redirect", None) is not None:
        blocks.append("commit_divergence")
    if getattr(best, "best_effort", False):
        blocks.append("best_effort")
    plan = getattr(best, "plan", None)
    if plan is not None and int(getattr(plan, "sla_violations", 0) or 0) > 0:
        blocks.append("plan_sla_violations")

    # G10: scarcity floty — wybór wymuszony pulą nie jest dowodem jakości
    # (SEL-01/FEAS-02: 57-60% cross-zwycięzców to scarcity).
    min_pool = _gate_numeric(flags, "AUTO_ASSIGN_MIN_POOL_FEASIBLE")
    pool_feasible = ctx.get("auto_route_pool_feasible")
    if pool_feasible is None:
        pool_feasible = getattr(result, "pool_feasible_count", 0) or 0
    if float(pool_feasible) < min_pool:
        blocks.append(f"scarcity_pool:{int(pool_feasible)}")

    # G11: sufit nieufności score (Bartek 2.0 §4.1 — breach 13,5-18% przy
    # score>90, inflacja R4; korelacja score↔wynik odwraca się w górze).
    ceiling = _gate_numeric(flags, "AUTO_ASSIGN_SCORE_DISTRUST_CEILING")
    score = float(getattr(best, "score", 0.0) or 0.0)
    if score > ceiling:
        blocks.append(f"score_distrust_ceiling:{score:.1f}")

    return (len(blocks) == 0), blocks
