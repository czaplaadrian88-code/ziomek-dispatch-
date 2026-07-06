"""core.gates — bramki wejściowe decyzji (K10, PRZENOSINY 1:1 z `_assess_order_impl`).

Dwie bramki HARD sprzed budowy puli kandydatów (kolejność w impl bez zmian:
LOADGOV → geokod-defense → parsowanie pickup_at → early-bird):

- `geocode_defense` — brak/sentinel `pickup_coords` → SKIP/no_pickup_geocode
  (L2 defense, fail-loud zamiast śmieciowego feasibility loopu; historia:
  firmowe konto address_id=161, sentinel haversine 6285 km).
- `early_bird` — deklaracja odbioru ≥ progu naprzód → KOORD; próg liczony z RAW
  `pickup_at_warsaw` (fix 2026-05-07), NIE z extended czas_kuriera. Opcjonalny
  rekurencyjny kontrfaktyk EARLYBIRD-01 (max głębokość 1 przez bypass) do shadow.

Przenosiny mechaniczne: treść warunków, komunikaty logów, kształt PipelineResult
i kolejność efektów IDENTYCZNE z wersją inline. Import dispatch_pipeline LAZY
w funkcjach — bramki są wołane z wnętrza `_assess_order_impl`, więc moduł-macierz
jest już w pełni zainicjalizowany (top-level import w obie strony zrobiłby cykl,
bo dispatch_pipeline importuje gates na górze). Logger i helpery early-bird
(`_early_bird_threshold_min`/`_earlybird_t30_shadow_enabled`/
`_append_earlybird_t30_shadow`) ZOSTAJĄ w dispatch_pipeline (zewnętrzny konsument:
shadow_dispatcher importuje `_early_bird_threshold_min`) — gates używa ich przez
`_dp.*`, dzięki czemu monkeypatch w istniejących testach działa bez zmian.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dispatch_v2.common import WARSAW, parse_panel_timestamp


def geocode_defense(order_event: dict, *, order_id: str,
                    restaurant, delivery_address):
    """Defense gate (L2): brak pickup_coords = order bez geokodacji → SKIP.

    Zwraca PipelineResult(SKIP/no_pickup_geocode) albo None (przepuść dalej).
    Treść 1:1 z dispatch_pipeline (pre-K10 ~3791-3817).
    """
    from dispatch_v2 import dispatch_pipeline as _dp

    _raw_pickup_coords = order_event.get("pickup_coords")
    if _raw_pickup_coords is None or _raw_pickup_coords == [0.0, 0.0] or _raw_pickup_coords == (0.0, 0.0):
        _dp.log.warning(
            f"assess_order SKIP {order_id} aid={order_event.get('address_id')!r}: "
            f"pickup_coords missing — defense gate (no geocode); "
            f"uwagi_parsed={order_event.get('uwagi_pickup_parsed')!r}"
        )
        return _dp.PipelineResult(
            order_id=order_id,
            verdict="SKIP",
            reason="no_pickup_geocode",
            best=None,
            candidates=[],
            pickup_ready_at=None,
            restaurant=restaurant,
            delivery_address=delivery_address,
            pool_total_count=0,
            pool_feasible_count=0,
        )
    return None


def early_bird(order_event: dict, fleet_snapshot: Dict[str, Any],
               restaurant_meta: Optional[dict], now: datetime, *,
               pickup_at: Optional[datetime], order_id: str,
               restaurant, delivery_address,
               pending_queue: Optional[list],
               demand_context: Optional[dict],
               bypass: bool):
    """Early bird → KOORD (+ kontrfaktyk EARLYBIRD-01 do shadow, głębokość 1).

    `pickup_at` = już sparsowany fallback (V3.19f) — używany TYLKO gdy brak
    surowego pickup_at_warsaw (jak w wersji inline). Pełny kontekst assess
    w sygnaturze, bo kontrfaktyk woła `_dp._assess_order_impl` BEZPOŚREDNIO
    (nie wrapper — observability hook liczyłby zlecenie podwójnie).
    Zwraca PipelineResult(KOORD/early_bird...) albo None (przepuść dalej).
    Treść 1:1 z dispatch_pipeline (pre-K10 ~3841-3898).
    """
    from dispatch_v2 import dispatch_pipeline as _dp

    # Fix 2026-05-07: early_bird threshold patrzy na RAW pickup_at_warsaw (deklaracja
    # restauracji), NIE extended czas_kuriera_warsaw. Bug strukturalny od V3.19f deploy:
    # czasowka_scheduler liczy mtp z raw, assess_order early_bird patrzył na extended →
    # czasówki przedłużone Ziomkiem o 20-30min były KOORD'owane jako pool=0 mimo że
    # czasowka_scheduler był w T-40 trigger window. Eliminuje 49% KOORD czasówek
    # (`zero MAYBE` 19× / 39 całych w 5-day eval_log obs).
    pickup_at_for_early_bird_raw = order_event.get("pickup_at_warsaw") or order_event.get("pickup_at")
    pickup_at_for_early_bird = (
        parse_panel_timestamp(pickup_at_for_early_bird_raw)
        if pickup_at_for_early_bird_raw else pickup_at
    )

    # Early bird → KOORD
    if pickup_at_for_early_bird is not None and not bypass:
        pu = pickup_at_for_early_bird if pickup_at_for_early_bird.tzinfo else pickup_at_for_early_bird.replace(tzinfo=WARSAW)
        minutes_ahead = (pu.astimezone(timezone.utc) - now).total_seconds() / 60.0
        if minutes_ahead >= _dp._early_bird_threshold_min():  # SCALE-01: flags.json hot
            # EARLYBIRD-01 forward-shadow: kontrfaktyk „co gdyby przepuścić do feasibility
            # teraz" (bez early_bird short-circuit). LOG-ONLY, flaga OFF default, fail-soft
            # (defense-in-depth — błąd shadow NIGDY nie psuje live KOORD). _bypass_early_bird=True
            # zapobiega rekurencji (max głębokość 1).
            if _dp._earlybird_t30_shadow_enabled():
                try:
                    # Kontrfaktyk woła _assess_order_impl BEZPOŚREDNIO (nie wrapper) —
                    # inaczej observability hook podwójnie zalogowałby zlecenie do
                    # candidate_decisions.jsonl (= strumień, który czyta pomiar EARLYBIRD).
                    _cf = _dp._assess_order_impl(
                        order_event, fleet_snapshot, restaurant_meta, now,
                        pending_queue=pending_queue, demand_context=demand_context,
                        _bypass_early_bird=True,
                    )
                    _dp._append_earlybird_t30_shadow({
                        "ts": now.isoformat(),
                        "order_id": order_id,
                        "restaurant": restaurant,
                        "minutes_ahead": round(minutes_ahead, 1),
                        "cf_verdict": _cf.verdict,
                        "cf_reason": _cf.reason,
                        "cf_pool_total": _cf.pool_total_count,
                        "cf_pool_feasible": _cf.pool_feasible_count,
                        "cf_best_cid": (_cf.best.courier_id if _cf.best else None),
                        "cf_best_score": (round(_cf.best.score, 2) if _cf.best else None),
                        # would_resolve = przepuszczenie dałoby realną PROPOZYCJĘ (nie kolejny
                        # KOORD/SKIP/NO) → kandydat do auto-resolve w T-30 zamiast eskalacji.
                        "would_resolve": (_cf.verdict == "PROPOSE"),
                    })
                except Exception as _eb_e:
                    _dp.log.warning(f"earlybird_t30_shadow failed oid={order_id}: {_eb_e}")
            return _dp.PipelineResult(
                order_id=order_id,
                verdict="KOORD",
                reason=f"early_bird ({minutes_ahead:.0f} min ahead)",
                best=None,
                candidates=[],
                pickup_ready_at=None,
                restaurant=restaurant,
                delivery_address=delivery_address,
            )
    return None
