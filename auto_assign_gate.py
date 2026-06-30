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


def _gate_bool(flags: Optional[Dict[str, Any]], name: str, default: bool = True) -> bool:
    """Flaga bramki (AUTON-02 profil): flags dict (hot) → stała modułu → default.
    Czytana z przekazanego `flags` (nie C.flag()) — bramka jest czysta, profil
    wstrzykuje wołający (dispatch_pipeline: strict live + plaster D w shadow)."""
    src = flags if isinstance(flags, dict) else {}
    val = src.get(name, getattr(C, name, default))
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def _quality_score(cand: Any) -> Optional[float]:
    """Score kandydata BEZ delt rankingowych (lekcja #188, fix 30a01d2).

    Reuse kanonicznego `dispatch_pipeline._gate_score_excluding_ranking_deltas`
    (lazy import — pipeline importuje ten moduł lazy w hooku, brak cyklu w
    czasie wywołania). Fail-soft: gdy helper niedostępny/wyjątek → surowy score
    (zachowanie sprzed fixa, bez nowych ścieżek błędu w hot-path).
    """
    try:
        from dispatch_v2.dispatch_pipeline import (
            _gate_score_excluding_ranking_deltas as _ex,
        )
        v = _ex(cand)
        if isinstance(v, (int, float)):
            return float(v)
    except Exception:
        pass
    sc = getattr(cand, "score", None)
    return float(sc) if isinstance(sc, (int, float)) else None


def _min_margin_threshold(flags: Optional[Dict[str, Any]]) -> float:
    """Próg marginu poziomu klasyfikatora (T1/T2/T3) — bez bumpa HIGH_RISK.

    Bump HIGH_RISK zaostrza wyłącznie klasyfikator (G2 egzekwuje go na marginie
    z deltami); G12 sprawdza próg BAZOWY na marginie ex-delta. Fail-soft → T1.
    """
    src = flags if isinstance(flags, dict) else {}
    try:
        from dispatch_v2.auto_proximity_classifier import DEFAULT_THRESHOLDS
        level = str(src.get("AUTO_PROXIMITY_THRESHOLD", "T1"))
        table = src.get("AUTO_PROXIMITY_THRESHOLDS") or DEFAULT_THRESHOLDS
        cfg = table.get(level) or DEFAULT_THRESHOLDS.get("T1", {})
        return float(cfg.get("min_score_margin", 15.0))
    except Exception:
        return 15.0


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
    # AUTON-02: w profilu „plaster D" (REQUIRE_CLASSIFIER_AUTO=False) ZDJĘTE —
    # G2 przepuszczał tylko 7% (would_auto≈0). Edge'e które G2 niósł ukryte
    # (shift_end_edge, parser_degraded) egzekwują JAWNIE G13/G14 niżej.
    route = getattr(result, "auto_route", "ACK")
    if _gate_bool(flags, "AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO") and route != "AUTO":
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

    # G13 (AUTON-02): kurier KOŃCZY ZMIANĘ (shift_end_edge) — klasyfikator
    # routował te do ACK/ALERT; przy zdjętym G2 (plaster D) egzekwujemy JAWNIE,
    # żeby autonomia nie przypisała kurierowi tuż przed końcem zmiany. ZAWSZE.
    if ctx.get("auto_route_shift_end_edge"):
        blocks.append("shift_end_edge")

    # G14 (AUTON-02): degradacja parsera/zdrowia systemu — gdy parser_degraded,
    # dane wejściowe niepewne → nigdy auto (klasyfikator też to wykluczał). ZAWSZE.
    if _gate_bool(flags, "PARSER_DEGRADED", default=False) or ctx.get("auto_route_parser_degraded"):
        blocks.append("parser_degraded")

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
    # Lekcja #188 (wymóg promptu AUTON-01): kryterium na score BEZ delt
    # rankingowych (sync/loadgov) — kara sync −150 na surowym score potrafiła
    # OTWORZYĆ sufit (jakość 200 → surowy 50 → pass). Delty są ujemne, więc
    # score jakościowy ≥ surowy — test na jakościowym domyka oba kierunki.
    ceiling = _gate_numeric(flags, "AUTO_ASSIGN_SCORE_DISTRUST_CEILING")
    q_best = _quality_score(best)
    score = q_best if q_best is not None else float(getattr(best, "score", 0.0) or 0.0)
    if score > ceiling:
        blocks.append(f"score_distrust_ceiling:{score:.1f}")

    # G12: margin na score BEZ delt rankingowych (lekcja #188, semantyka Z-10:
    # quality(best) − max(quality reszty feasible)). Kara sync −150 na runner-upie
    # sztucznie ROZDYMA margin klasyfikatora (G2 widzi margin z deltami) — kara
    # nie może otwierać AUTO. Kierunek odwrotny (kara na best ZAMYKA AUTO przez
    # niższy margin w G2) = fail-closed, świadomie zostaje — recompute klasyfikatora
    # ex-delta to temat E7. Bez listy kandydatów (stare rekordy/testy) fallback:
    # margin z kontekstu klasyfikatora vs ten sam próg bazowy.
    # AUTON-02: w profilu „plaster D" (REQUIRE_MARGIN=False) G12 ZDJĘTE —
    # analiza fizyczna 14d: ZGODA≈OVERRIDE w wyniku dostawy (breach 8,6%≈9,0%),
    # kurierzy w puli feasible wymienni → margin #1-vs-#2 nie jest warunkiem
    # bezpieczeństwa. Scarcity łapie G10, świeżość pozycji G7. Sufit G11 zostaje.
    if _gate_bool(flags, "AUTO_ASSIGN_REQUIRE_MARGIN"):
        min_margin = _min_margin_threshold(flags)
        cands = getattr(result, "candidates", None) or []
        others_q: List[float] = []
        for c in cands:
            if c is best or getattr(c, "courier_id", None) == getattr(best, "courier_id", None):
                continue
            if getattr(c, "feasibility_verdict", "MAYBE") != "MAYBE":
                continue
            qc = _quality_score(c)
            if qc is not None:
                others_q.append(qc)
        if others_q and q_best is not None:
            margin_ex = q_best - max(others_q)
            if margin_ex < min_margin:
                blocks.append(f"margin_ex_delta:{margin_ex:.1f}<{min_margin:.0f}")
        elif not cands:
            ctx_margin = ctx.get("auto_route_score_margin")
            if isinstance(ctx_margin, (int, float)) and float(ctx_margin) < min_margin:
                blocks.append(f"margin_ex_delta_ctx:{float(ctx_margin):.1f}<{min_margin:.0f}")
        # (kandydaci są, ale solo-feasible → margin niezdefiniowany; scarcity łapie G10)

    return (len(blocks) == 0), blocks
