"""pending_global_resweep — co-minutowy GLOBALNY re-ranking WISZĄCYCH propozycji.

PROBLEM (diagnoza 2026-06-24, case 483138 Chinatown→Plażowa):
  Ziomek liczy propozycję JEDNORAZOWO przy `NEW_ORDER` (shadow_dispatcher konsumuje
  tylko to zdarzenie) i już jej nie re-rankuje. Skutek 1: gdy w oknie oczekiwania
  świat się zmienia (proponowany kurier się obładuje, inny się zwolni) — stara
  propozycja zostaje nieaktualna. Skutek 2: każde zlecenie oceniane NIEZALEŻNIE
  (greedy per-order) — gdy wisi kilka zleceń naraz, ten sam „najlepszy" kurier
  (np. stojący pod restauracją) bywa proponowany do WSZYSTKICH, choć część jedzie
  w inne strony i powinna trafić do różnych kurierów.

ROZWIĄZANIE (ten plik):
  Co minutę bierze WSZYSTKIE wiszące (nieprzypisane) zlecenia i alokuje je GLOBALNIE
  na dispatchowalną flotę — sekwencyjny greedy z aktualizacją stanu floty: po
  wirtualnym przypisaniu zlecenia kurierowi jego worek rośnie, więc kolejne zlecenia
  „w przeciwną stronę" dostają u niego gorszy score → trafiają do innych kurierów.
  Używa PRAWDZIWEGO `dispatch_pipeline.assess_order` (zero dryftu scoringu).

TRYB: SHADOW (default). Loguje `would_repropose` do jsonl — NIE dotyka Telegrama
  ani pending_proposals.json. Jednocześnie MIERZY ile propozycji by się odwróciło
  i ile „pile-on-jednego-kuriera" by rozbił. Flip na żywe re-proponowanie = osobna
  flaga PENDING_RESWEEP_LIVE (default OFF; K5 05.07: ścieżka live WPIĘTA — podmiana
  decision_record w pending_proposals dla konsoli/1-klik za bramką live_gate_open;
  flip za rekomendacją + osobnym ACK Adriana, wykonuje FLIPMASTER).

Wzorzec: tools/reassignment_forward_shadow.py (read-only assess_order, jsonl, flag-gate).

Uruchomienie ręczne:
  cd /root/.openclaw/workspace/scripts
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.pending_global_resweep
"""
from __future__ import annotations
import sys
import fcntl
import json
import os
import tempfile
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C
from dispatch_v2 import courier_resolver as CR
from dispatch_v2.core.decide import decide as _decide  # K09 fasada
from dispatch_v2.core.world_state import WorldState

_log = logging.getLogger("pending_global_resweep")


def _disable_replay_capture() -> None:
    """Wyłącz obj_replay_capture w TYM procesie. Robimy syntetyczne assess_order z
    wirtualnie zmienionymi workami (alokacja) — te wywołania NIE mogą trafić do
    obj_replay_capture.jsonl (skaziłyby zestaw kalibracyjny). capture() czyta
    getattr(C,...) per-call → override modułowy trzyma się procesu. NIE robimy tego
    na poziomie importu (gdyby ktoś zaimportował moduł do żywego dispatchu → utrata
    capture); robimy w global_allocate (jedyny punkt wołający _assess)."""
    C.ENABLE_OBJ_REPLAY_CAPTURE = False

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
PENDING_PATH = f"{STATE_DIR}/pending_proposals.json"
ORDERS_STATE = f"{STATE_DIR}/orders_state.json"
OUT_JSONL = f"{STATE_DIR}/pending_global_resweep.jsonl"
PINGPONG_STATE_PATH = f"{STATE_DIR}/pending_resweep_pingpong_state.json"

FLAG = "ENABLE_PENDING_RESWEEP"          # master on/off (shadow). default OFF = no-op.
FLAG_LIVE = "PENDING_RESWEEP_LIVE"       # K5: podmiana propozycji dla KONSOLI (pending+1-klik; decyzja Adriana 05.07 — NIE Telegram). default OFF.
MARGIN_KEY = "PENDING_RESWEEP_MARGIN"
PINGPONG_MARGIN_MULTIPLIER_KEY = "PENDING_RESWEEP_PINGPONG_MARGIN_MULTIPLIER"
PINGPONG_COOLDOWN_MIN_KEY = "PENDING_RESWEEP_PINGPONG_COOLDOWN_MIN"
DEFAULT_MARGIN = 15.0                    # pkt — jak DEFAULT_MARGIN reassignment_fwd / auto-proximity
DEFAULT_PINGPONG_MARGIN_MULTIPLIER = 2.0
DEFAULT_PINGPONG_COOLDOWN_MIN = 10.0
MAX_HANGING = 8                          # bezpiecznik: max wiszących zleceń/ tick

_EVENT_FIELDS = (
    "order_id", "restaurant", "delivery_address", "pickup_coords", "delivery_coords",
    "czas_kuriera_warsaw", "pickup_at_warsaw", "pickup_at", "address_id", "order_type",
    "created_at_utc", "created_at", "delivery_city", "uwagi_pickup_parsed", "prep_minutes",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _state_to_order_event(rec: dict) -> dict:
    return {k: rec.get(k) for k in _EVENT_FIELDS if rec.get(k) is not None}


def _append_jsonl(rows: List[dict]) -> None:
    if not rows:
        return
    try:
        with open(OUT_JSONL, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError as e:
        _log.warning(f"jsonl append fail: {e}")


def _positive_float(flags: dict, key: str, default: float) -> float:
    """Fail-safe parser konfiguracji liczbowej resweepa (zero zmiany flags.json)."""
    try:
        value = float(flags.get(key, default))
        if value <= 0:
            raise ValueError("must be positive")
        return value
    except (TypeError, ValueError):
        _log.warning("invalid %s; using default=%s", key, default)
        return default


def _iso_datetime(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _pingpong_state_from_live(proposed: Dict[str, dict]) -> Dict[str, dict]:
    """Stan LIVE pochodzi wyłącznie z audytowalnego provenance pending entry.

    Shadow state nigdy nie jest wejściem do decyzji LIVE. Dzięki temu 48 h
    kontrfaktycznej obserwacji nie może po flipie udawać wykonanych podmian.
    """
    state: Dict[str, dict] = {}
    for oid, prop in proposed.items():
        current = prop.get("cid")
        item = {"current_cid": current, "previous_cid": None,
                "last_swap_ts": None, "flip_count": 0}
        provenance = prop.get("resweep_live") or {}
        if (str(provenance.get("new_cid")) == str(current)
                and provenance.get("old_cid") is not None):
            item.update({
                "previous_cid": str(provenance.get("old_cid")),
                "last_swap_ts": provenance.get("ts"),
                "flip_count": int(provenance.get("flip_count") or 1),
            })
        state[str(oid)] = item
    return state


def _annotate_pingpong_rows(rows: List[dict], score_maps: Dict[str, Dict[str, float]],
                            state: Dict[str, dict], now: datetime, margin: float,
                            margin_multiplier: float, cooldown_min: float) -> None:
    """Oceń i zasymuluj guard A→B→A bez zmiany zwykłego would_repropose.

    `state` jest mutowany tylko dla kontrfaktycznych podmian, które przeszłyby
    zwykły margin i nie zostałyby zablokowane. Nie zawiera nazw/adresów/koordynatów.
    """
    required_margin = margin * margin_multiplier
    for row in rows:
        oid = str(row.get("order_id"))
        entry = state.setdefault(oid, {
            "current_cid": row.get("proposed_cid"), "previous_cid": None,
            "last_swap_ts": None, "flip_count": 0,
        })
        current = entry.get("current_cid")
        if current is None:
            current = row.get("proposed_cid")
            entry["current_cid"] = current
        previous = entry.get("previous_cid")
        target = row.get("new_cid")
        scores = score_maps.get(oid) or {}
        current_score = scores.get(str(current)) if current is not None else None
        target_score = scores.get(str(target)) if target is not None else None
        delta = None
        if target_score is not None and current_score is not None:
            delta = round(float(target_score) - float(current_score), 1)
        normally_eligible = bool(
            target is not None and str(target) != str(current)
            and not row.get("no_courier")
            and (current_score is None or (delta is not None and delta >= margin)
                 or (str(current) == str(row.get("proposed_cid"))
                     and row.get("would_repropose")))
        )
        is_return = bool(
            normally_eligible and previous is not None
            and str(target) == str(previous)
        )
        last_swap = _iso_datetime(entry.get("last_swap_ts"))
        elapsed_min = None
        if last_swap is not None:
            elapsed_min = round(max(0.0, (now - last_swap).total_seconds() / 60.0), 2)
        margin_ok = delta is not None and delta >= required_margin
        cooldown_ok = elapsed_min is not None and elapsed_min >= cooldown_min
        # HARD feasibility przed SOFT hysterezą: jeśli bieżący kurier wypadł z
        # puli, guard nie może uwięzić zlecenia tylko dlatego, że delta jest None.
        hard_escape = bool(is_return and current_score is None)
        blocked = bool(is_return and not hard_escape
                       and not (margin_ok and cooldown_ok))

        row.update({
            "would_pingpong_block": blocked,
            "pingpong_is_return": is_return,
            "pingpong_delta_vs_current": delta,
            "pingpong_required_margin": round(required_margin, 1),
            "pingpong_elapsed_min": elapsed_min,
            "pingpong_cooldown_min": cooldown_min,
            "pingpong_flip_count": int(entry.get("flip_count") or 0),
            "pingpong_hard_escape_current_infeasible": hard_escape,
        })
        if normally_eligible and not blocked:
            entry.update({
                "previous_cid": current,
                "current_cid": target,
                "last_swap_ts": now.isoformat(),
                "flip_count": int(entry.get("flip_count") or 0) + 1,
            })


def _annotate_pingpong_shadow(rows: List[dict], score_maps: Dict[str, Dict[str, float]],
                              now: datetime, margin: float,
                              margin_multiplier: float, cooldown_min: float) -> None:
    """RMW shadow state pod fcntl; zapis temp→fsync→rename→fsync katalogu."""
    state_path = os.path.abspath(PINGPONG_STATE_PATH)
    parent = os.path.dirname(state_path)
    os.makedirs(parent, exist_ok=True)
    lock_path = state_path + ".lock"
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            try:
                with open(state_path, encoding="utf-8") as source:
                    raw = json.load(source)
                state = raw.get("orders") if isinstance(raw, dict) else {}
                if not isinstance(state, dict):
                    state = {}
            except FileNotFoundError:
                state = {}
            except (OSError, ValueError, TypeError) as exc:
                _log.warning("pingpong shadow state load fail; rebuilding: %s", exc)
                state = {}

            active = {str(row.get("order_id")) for row in rows}
            state = {str(oid): value for oid, value in state.items()
                     if str(oid) in active and isinstance(value, dict)}
            _annotate_pingpong_rows(rows, score_maps, state, now, margin,
                                    margin_multiplier, cooldown_min)
            payload = {"schema": 1, "updated_at": now.isoformat(), "orders": state}
            fd, tmp_path = tempfile.mkstemp(prefix=".pending-resweep-pingpong-",
                                            suffix=".tmp", dir=parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as target_file:
                    json.dump(payload, target_file, ensure_ascii=False,
                              separators=(",", ":"), sort_keys=True)
                    target_file.flush()
                    os.fsync(target_file.fileno())
                os.replace(tmp_path, state_path)
                dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _cand_by_cid(res, cid: str):
    """Candidate dla danego cid z wyniku assess_order (lub None)."""
    if res is None or cid is None:
        return None
    for c in (getattr(res, "candidates", None) or []):
        if str(getattr(c, "courier_id", "")) == str(cid):
            return c
    return None


# L6.C3 (2026-07-04): ekstrakcja do modułu SILNIKA — jedno źródło claimu dla
# resweep/przerzutu/_tick (R2 ROOT-8 „wspólny import, nie 2. kopia"). Lokalne
# nazwy zachowane (konsumenci w tym pliku + reassignment_global_select nietknięci).
from dispatch_v2.claim_ledger import (  # noqa: E402
    bag_entry_from_order as _bag_entry_from_order,
    tentative_assign as _tentative_assign,
    check_sweep_trace as _check_claim_sweep_trace,
    check_feral_claim as _check_feral_claim,
)


def _assess(order_event: dict, fleet: Dict[str, Any], now: datetime):
    try:
        return _decide(WorldState(fleet_snapshot=fleet, now=now), order_event, _bypass_early_bird=True)  # K09
    except Exception as e:  # noqa: BLE001 — pojedyncze zlecenie nie wywala sweepu
        _log.warning(f"assess_order fail oid={order_event.get('order_id')}: {type(e).__name__}: {e}")
        return None


def global_allocate(hanging: List[Tuple[str, dict]], fleet0: Dict[str, Any],
                    now: datetime,
                    _results_out: Optional[Dict[str, Any]] = None,
                    _diag_out: Optional[Dict[str, Any]] = None) -> Dict[str, dict]:
    """Sekwencyjny greedy z aktualizacją stanu floty.

    hanging: [(oid, orders_state_rec)]. Zwraca {oid: {cid,name,score,feasibility,
    pool_total,pool_feasible,km,r6,cos,spread}} = globalna alokacja.

    _results_out (opcjonalny, Faza C): gdy podany dict — wypełniany {oid: PipelineResult}
    PEŁNYM wynikiem assess_order użytym do alokacji TEGO zlecenia (liczonym nad flotą
    wirtualnie doładowaną wcześniejszymi alokacjami w tym sweepie). Pozwala konsumentowi
    (shadow_dispatcher Fazy C) zserializować te same rekordy do shadow_decisions.jsonl
    (=lustro konsoli) BEZ 2. kopii reguł — selekcja/feasible-first/best_effort dziedziczone
    z assess_order. Back-compat: gdy None, zachowanie bajt-identyczne (tylko zwrot allocation).

    _diag_out (opcjonalny, Sprint B INV-FEAS-NO-DOUBLE-BOOK): gdy podany dict — wypełniany
    {"claim_trace": [(cid,oid,bag_seen)], "claim_ledger_breaches": [...]} do pomiaru
    spójności claim-ledger (run_once serializuje licznik do jsonl). Ślad budowany ZAWSZE
    (tani), weryfikacja + log-loud TYLKO pod flagą ENABLE_CLAIM_LEDGER_INVARIANT_CHECK.
    Przy dodatkowym HARD=ON feralny claim jest odrzucany przed skutkami ubocznymi,
    a tick kontynuuje; HARD=OFF zachowuje allocation bajt-identycznie.

    Zasada: w każdej rundzie oceniamy WSZYSTKIE jeszcze-niealokowane zlecenia żywym
    assess_order nad BIEŻĄCĄ flotą; przypisujemy to o najwyższym best-score; doklejamy
    je do worka wybranego kuriera; PONOWNIE oceniamy tylko te zlecenia, których
    dotychczasowy best był tym właśnie kurierem (reszta nie mogła się zmienić — zmienił
    się stan tylko jednego kuriera). Tak orderzy w różne strony rozjeżdżają się na
    różnych kurierów.
    """
    _disable_replay_capture()
    events = {oid: _state_to_order_event(rec) for oid, rec in hanging}
    recs = {oid: rec for oid, rec in hanging}
    fleet = dict(fleet0)
    # cache wyników assess per oid (pierwszy pełny przebieg)
    assessed = {oid: _assess(events[oid], fleet, now) for oid in events}
    remaining = set(events.keys())
    allocation: Dict[str, dict] = {}
    # INV-FEAS-NO-DOUBLE-BOOK: ślad claimów [(cid, oid, bag_seen)] w kolejności alokacji
    # (bag_seen = worek kuriera, który ocena zwycięska widziała — PRZED doklejeniem).
    _claim_trace: List[Tuple[str, str, int]] = []
    _accepted_claim_trace: List[Tuple[str, str, int]] = []
    _breaches: List[dict] = []
    _feral_drops: List[dict] = []
    _claim_check_on = C.decision_flag("ENABLE_CLAIM_LEDGER_INVARIANT_CHECK")
    _claim_hard_on = (
        _claim_check_on
        and C.decision_flag("ENABLE_CLAIM_LEDGER_INVARIANT_HARD")
    )

    def _best_tuple(oid):
        res = assessed.get(oid)
        b = getattr(res, "best", None) if res is not None else None
        if b is None:
            return (None, None, res)
        return (str(b.courier_id), float(b.score), res)

    while remaining:
        # wybierz zlecenie o najwyższym best-score; brak best → None (najniższy priorytet)
        ranked = []
        for oid in remaining:
            cid, score, res = _best_tuple(oid)
            ranked.append((oid, cid, score, res))
        # najpierw te z realnym best, najwyższy score pierwszy
        ranked.sort(key=lambda t: (t[2] is None, -(t[2] if t[2] is not None else -1e18)))
        oid, cid, score, res = ranked[0]

        # mapa BIEŻĄCYCH score wszystkich kandydatów dla tego zlecenia (stan floty w
        # chwili alokacji — uwzględnia obciążenie kurierów z wcześniejszych rund).
        # Pozwala run_once porównać proponowanego kuriera po jego AKTUALNYM score,
        # nie po score sprzed obciążenia (klucz do single-rerank case 483138).
        cand_scores = {str(getattr(c, "courier_id", "")): float(c.score)
                       for c in (getattr(res, "candidates", None) or []) if c.score is not None}

        if cid is None:
            # brak feasible kuriera dla tego zlecenia → KOORD, nie alokujemy, nie ruszamy floty
            allocation[oid] = {"cid": None, "name": None, "score": None,
                               "feasibility": None, "no_courier": True,
                               "cand_scores": cand_scores,
                               "pool_total": int(getattr(res, "pool_total_count", 0) or 0),
                               "pool_feasible": int(getattr(res, "pool_feasible_count", 0) or 0)}
            if _results_out is not None and res is not None:
                _results_out[oid] = res
            remaining.discard(oid)
            continue

        b = res.best
        m = getattr(b, "metrics", None) or {}
        allocation[oid] = {
            "cid": cid, "name": getattr(b, "name", None), "score": round(score, 1),
            "feasibility": getattr(b, "feasibility_verdict", None),
            "km": m.get("km_to_pickup"), "r6": m.get("r6_max_bag_time_min"),
            "cos": m.get("r1_new_drop_cosine"), "spread": m.get("deliv_spread_km"),
            "cand_scores": cand_scores,
            "pool_total": int(getattr(res, "pool_total_count", 0) or 0),
            "pool_feasible": int(getattr(res, "pool_feasible_count", 0) or 0),
            "no_courier": False,
        }
        # INV-FEAS-NO-DOUBLE-BOOK: zanotuj rozmiar worka, który ocena zwycięska widziała
        # (bag PRZED doklejeniem tego zlecenia) — kolejny claim tego kuriera MUSI widzieć +1.
        _cs_now = fleet.get(cid)
        _claim = (cid, oid, len(getattr(_cs_now, "bag", None) or []))
        _claim_trace.append(_claim)
        if _claim_hard_on:
            try:
                _claim_viol = _check_feral_claim(
                    _accepted_claim_trace, _claim,
                    log=_log, context="global_allocate")
            except Exception as _ce:  # noqa: BLE001 — checker nie zatrzymuje sweepu
                _log.warning(
                    "claim_ledger HARD checker fail-soft: "
                    f"{type(_ce).__name__}: {_ce}")
                _claim_viol = []
            if _claim_viol:
                _breaches.extend(_claim_viol)
                _feral_drops.append({
                    "cid": cid,
                    "oid": oid,
                    "score": round(score, 1),
                    "violations": _claim_viol,
                })
                # DROP-FERAL-CLAIM: wynik istnieje diagnostycznie, ale nie może
                # trafić do results_out, wirtualnej floty ani ścieżki live.
                allocation[oid] = {
                    "cid": None, "name": None, "score": None,
                    "feasibility": "DROP_FERAL_CLAIM",
                    "feral_claim_dropped": True,
                    "dropped_cid": cid,
                    "dropped_score": round(score, 1),
                    "km": None, "r6": None, "cos": None, "spread": None,
                    "cand_scores": cand_scores,
                    "pool_total": int(getattr(res, "pool_total_count", 0) or 0),
                    "pool_feasible": int(getattr(res, "pool_feasible_count", 0) or 0),
                    "no_courier": False,
                }
                remaining.discard(oid)
                continue

        _accepted_claim_trace.append(_claim)
        if _results_out is not None and res is not None:
            _results_out[oid] = res
        # wirtualnie doklej zlecenie do worka kuriera → kolejne re-oceny widzą obciążenie
        fleet = _tentative_assign(fleet, cid, recs[oid])
        remaining.discard(oid)
        # re-oceń tylko te, których best był tym kurierem (reszta niezmieniona)
        for other in list(remaining):
            ocid, _, _ = _best_tuple(other)
            if ocid == cid:
                assessed[other] = _assess(events[other], fleet, now)

    # CHECK-only pozostaje obserwatorem post-sweep. HARD działał per claim powyżej,
    # więc odrzucił wyłącznie feralne wejścia i nigdy nie zatrzymał de-konflikcji.
    try:
        if _claim_check_on and not _claim_hard_on:
            _breaches = _check_claim_sweep_trace(
                _claim_trace, log=_log, context="global_allocate")
    except Exception as _ce:  # noqa: BLE001 — obserwator nie wywala sweepu
        _log.warning(f"claim_ledger invariant check fail-soft: {type(_ce).__name__}: {_ce}")
    if _diag_out is not None:
        _diag_out["claim_trace"] = _claim_trace
        _diag_out["claim_ledger_breaches"] = _breaches
        if _claim_hard_on:
            _diag_out["claim_ledger_feral_drops"] = _feral_drops
    return allocation


def global_allocate_results(hanging: List[Tuple[str, dict]], fleet0: Dict[str, Any],
                            now: datetime) -> Dict[str, Any]:
    """Faza C: globalna alokacja → {oid: PipelineResult} (pełne wyniki assess_order nad
    wirtualnie doładowaną flotą, w kolejności pewności score). Cienka nakładka na
    `global_allocate` (jedno źródło logiki — zero duplikacji reguł). Konsument
    (shadow_dispatcher, flaga ENABLE_GLOBAL_ALLOCATION) serializuje te wyniki do
    shadow_decisions.jsonl = lustro konsoli koordynatora. Fail-soft: błąd/puste → {}."""
    results: Dict[str, Any] = {}
    try:
        global_allocate(hanging, fleet0, now, _results_out=results)
    except Exception as e:  # noqa: BLE001 — Faza C nie może wywalić tick'a shadow_dispatcher
        _log.warning(f"global_allocate_results fail: {type(e).__name__}: {e}")
        return {}
    return results


def live_gate_open() -> bool:
    """L6.C3c GATE (2026-07-04, bramka nieprzekraczalna C10-oracle 30.06): każde
    przyszłe działanie LIVE de-pile'a wymaga członu geometrii w lex_qual — flip
    PENDING_RESWEEP_LIVE bez ENABLE_LEXQUAL_GEOMETRY_TIEBREAK = 279 propozycji
    spread>8km wypchniętych na żywo (2019 alokacji multi-drop, 35,2% łamie R1 8km).
    Zakodowane, nie tylko w notatkach: False = HOLD + głośny warning. KAŻDY przyszły
    konsument ścieżki LIVE MUSI wołać ten gate przed akcją."""
    if C.decision_flag("ENABLE_LEXQUAL_GEOMETRY_TIEBREAK"):
        return True
    _log.warning("PENDING_RESWEEP_LIVE=ON ale ENABLE_LEXQUAL_GEOMETRY_TIEBREAK=OFF "
                 "→ HOLD (bramka L6.C: de-pile LIVE bez geometrii w selekcji = "
                 "279 propozycji spread>8km; flip geometrii najpierw)")
    return False


# K5 LIVE: max akcji na tick (bezpiecznik PONAD margin/MAX_HANGING — stopniowe
# wdrożenie; koordynator widzi zmiany przyrostowo, nie lawinę podmian).
LIVE_MAX_ACTIONS_PER_TICK = 3


def _live_apply(rows: List[dict], ga_results: Dict[str, Any], now: datetime) -> int:
    """K5 (2026-07-05): AKCJA LIVE resweepa — podmiana propozycji w pending_proposals
    dla KONSOLI (decyzja Adriana 05.07: konsola/1-klik, NIE edit Telegrama; Telegram
    wyciszony 26.06 = nietykalny).

    Dla wierszy would_repropose (margin/spread już rozstrzygnięte w run_once):
    entry.decision_record := pełna serializacja wyniku globalnej alokacji
    (_serialize_result — TEN SAM kształt co shadow_decisions; feed konsoli parsuje
    identycznie, panel_watcher._save_plan_from_pending dostaje plan NOWEGO kuriera,
    a detekcja PANEL_OVERRIDE przestaje fałszywie krzyczeć po akcepcie nowego).

    Współbieżność: kanoniczny state_machine.lifecycle_apply_lock serializuje recheck
    statusu z writerami orders_state, a wewnętrzny pending_proposals_store.locked_mutate
    trzyma fcntl LOCK_EX na cały RMW (kolejność locków: lifecycle → pending). Guardy
    tuż przed podmianą wymagają: wpis istnieje, nadal wskazuje proposed_cid i bieżący
    status zlecenia == planned — inaczej skip. Fail-soft per akcja (tick nie pada).
    Provenance: entry.resweep_live {ts, old, new, delta_vs_now, reason} + marker
    row.live_action w jsonl. Zwraca liczbę wykonanych podmian."""
    from dispatch_v2 import pending_proposals_store as PPS
    from dispatch_v2 import shadow_dispatcher as _sd
    from dispatch_v2 import state_machine as _sm
    acted = 0
    for row in rows:
        if not row.get("would_repropose"):
            continue
        if row.get("would_pingpong_block"):
            row["live_action"] = "skip_pingpong_guard"
            continue
        if acted >= LIVE_MAX_ACTIONS_PER_TICK:
            row["live_action"] = "skip_tick_cap"
            continue
        oid = str(row.get("order_id"))
        res = ga_results.get(oid)
        if res is None:
            row["live_action"] = "skip_no_result"
            continue
        try:
            serialized = _sd._serialize_result(res, f"resweep-live-{oid}", 0.0)
        except Exception as e:  # noqa: BLE001 — pojedyncza akcja nie wywala ticka
            _log.warning(f"K5 live serialize fail oid={oid}: {type(e).__name__}: {e}")
            row["live_action"] = "skip_serialize_fail"
            continue
        outcome = {"v": "skip_gone"}
        provenance = {
            "ts": now.isoformat(), "old_cid": row.get("proposed_cid"),
            "new_cid": row.get("new_cid"), "delta_vs_now": row.get("delta_vs_now"),
            "reason": row.get("reason"),
            "flip_count": int(row.get("pingpong_flip_count") or 0) + 1,
        }

        def _mut(pending: Dict[str, Any]) -> None:
            entry = pending.get(oid)
            if not isinstance(entry, dict):
                outcome["v"] = "skip_gone"        # przypisane/wygasłe między compute a lockiem
                return
            cur = (((entry.get("decision_record") or {}).get("best") or {})
                   .get("courier_id"))
            if str(cur) != str(row.get("proposed_cid")):
                outcome["v"] = "skip_changed"     # inny pisarz podmienił propozycję
                return
            # Ostatni recheck pod OBU lockami. Snapshot z run_once może być już
            # nieaktualny, jeśli panel przypisał zlecenie podczas liczenia sweepu.
            try:
                with open(ORDERS_STATE, encoding="utf-8") as f:
                    orders_now = json.load(f)
            except (OSError, ValueError, TypeError):
                outcome["v"] = "skip_state_read_fail"  # fail-closed: bez podmiany
                return
            order_now = orders_now.get(oid) if isinstance(orders_now, dict) else None
            if not isinstance(order_now, dict) or order_now.get("status") != "planned":
                outcome["v"] = "skip_status_changed"
                return
            entry["decision_record"] = serialized
            entry["resweep_live"] = provenance
            entry["expires_at"] = PPS.build_entry(serialized, now)["expires_at"]
            outcome["v"] = "acted"

        try:
            # ścieżka JAWNIE z modułu w czasie wywołania (NIE default argumentu —
            # ten wiąże się przy definicji; near-miss 05.07: test z monkeypatchem
            # PENDING_PATH pisał w ŻYWY plik przez default; TOCTOU-guard obronił)
            # Każdy kanoniczny writer orders_state bierze lifecycle_apply_lock;
            # utrzymanie go do końca pending-RMW zamyka okno assigned-after-recheck.
            with _sm.lifecycle_apply_lock():
                PPS.locked_mutate(_mut, PPS.PENDING_PATH)
        except Exception as e:  # noqa: BLE001
            _log.warning(f"K5 live mutate fail oid={oid}: {type(e).__name__}: {e}")
            row["live_action"] = "skip_io_fail"
            continue
        row["live_action"] = outcome["v"]
        if outcome["v"] == "acted":
            acted += 1
            _log.info(
                f"RESWEEP_LIVE_ACT oid={oid} {provenance['old_cid']}->"
                f"{provenance['new_cid']} delta={provenance['delta_vs_now']} "
                f"reason={provenance['reason']}")
    return acted


def run_once(now: Optional[datetime] = None, margin: Optional[float] = None) -> dict:
    """Jeden sweep. No-op gdy flaga master OFF."""
    if not C.flag(FLAG, False):
        return {"skipped": "flag_off"}
    now = now or _now_utc()
    _t0 = time.monotonic()
    flags = C.load_flags()
    if margin is None:
        margin = float(flags.get(MARGIN_KEY, DEFAULT_MARGIN))
    pingpong_margin_multiplier = _positive_float(
        flags, PINGPONG_MARGIN_MULTIPLIER_KEY, DEFAULT_PINGPONG_MARGIN_MULTIPLIER)
    pingpong_cooldown_min = _positive_float(
        flags, PINGPONG_COOLDOWN_MIN_KEY, DEFAULT_PINGPONG_COOLDOWN_MIN)

    try:
        with open(PENDING_PATH, encoding="utf-8") as f:
            pending = json.load(f)
    except (OSError, ValueError) as e:
        _log.warning(f"pending load fail: {e}")
        return {"error": "pending_load"}
    try:
        with open(ORDERS_STATE, encoding="utf-8") as f:
            orders = json.load(f)
    except (OSError, ValueError) as e:
        _log.warning(f"orders_state load fail: {e}")
        return {"error": "state_load"}

    # wiszące = w pending_proposals ORAZ wciąż nieprzypisane (status planned)
    hanging: List[Tuple[str, dict]] = []
    proposed: Dict[str, dict] = {}   # oid -> {cid, score} z propozycji Ziomka
    for oid, p in pending.items():
        rec = orders.get(oid)
        if not rec or rec.get("status") != "planned":
            continue
        if not rec.get("pickup_coords") or not rec.get("delivery_coords"):
            continue
        dr = p.get("decision_record") or {}
        best = dr.get("best") or {}
        proposed[oid] = {"cid": str(best.get("courier_id")) if best.get("courier_id") is not None else None,
                         "score": best.get("score"),
                         "sent_at": p.get("sent_at"), "expires_at": p.get("expires_at"),
                         "auto_route": dr.get("auto_route"),
                         "resweep_live": p.get("resweep_live")}
        hanging.append((oid, rec))

    if not hanging:
        return {"hanging": 0, "would_repropose": 0,
                "duration_s": round(time.monotonic() - _t0, 2), "ts": now.isoformat()}
    hanging = hanging[:MAX_HANGING]

    fleet_list = CR.dispatchable_fleet()
    fleet = {str(cs.courier_id): cs for cs in fleet_list}

    # Faza C: gdy zapis dla konsoli ON, zbierz pełne wyniki w TYM SAMYM przebiegu
    # (global_allocate._results_out) — zero podwójnego liczenia assess_order.
    _alloc_write = C.flag("ENABLE_GLOBAL_ALLOC_WRITE", False)
    # K5 LIVE (2026-07-05, Wariant A Adriana: konsola/1-klik, NIE Telegram):
    # bramka live_gate_open() konsultowana RAZ, WCZEŚNIE (loguje HOLD gdy geometria
    # OFF — L6.C, nie do ominięcia). Wyniki pełne (_ga_results) są również źródłem
    # proposed_km w telemetrii shadow oraz serializacji akcji live; samo ich zachowanie
    # nie uruchamia żadnej dodatkowej oceny ani akcji.
    _live_armed = bool(C.flag(FLAG_LIVE, False)) and live_gate_open()
    _ga_results: Dict[str, Any] = {}
    # INV-FEAS-NO-DOUBLE-BOOK: zbierz diagnostykę claim-ledger tego sweepu (licznik do jsonl).
    _ga_diag: Dict[str, Any] = {}
    allocation = global_allocate(hanging, fleet, now,
                                 _results_out=_ga_results, _diag_out=_ga_diag)
    # liczba naruszeń spójności claim-ledger w tym sweepie (0 przy flagi OFF = brak weryfikacji)
    claim_breaches = _ga_diag.get("claim_ledger_breaches") or []
    n_claim_breaches = len(claim_breaches)
    _feral_drop_metric_on = "claim_ledger_feral_drops" in _ga_diag
    claim_feral_drops = _ga_diag.get("claim_ledger_feral_drops") or []
    n_claim_feral_drops = len(claim_feral_drops)

    # metryki rozjazdu (pile-on jednego kuriera) przed/po
    def _pile(d):
        from collections import Counter
        c = Counter(v for v in d.values() if v)
        return (len(c), (max(c.values()) if c else 0))
    # Sam DROP nie jest „poprawą spreadu" i nie może przez globalny nagłówek
    # zmienić decyzji would_repropose dla pozostałych orderów. Porównanie pile
    # obejmuje ten sam zbiór zaakceptowanych claimów po obu stronach.
    _spread_oids = [
        oid for oid, a in allocation.items()
        if not a.get("feral_claim_dropped")
    ]
    before_cids = {oid: proposed[oid]["cid"] for oid in _spread_oids}
    after_cids = {oid: allocation[oid]["cid"] for oid in _spread_oids}
    couriers_before, maxpile_before = _pile(before_cids)
    couriers_after, maxpile_after = _pile(after_cids)
    spread_improved = maxpile_after < maxpile_before

    rows: List[dict] = []
    pingpong_score_maps: Dict[str, Dict[str, float]] = {}
    n_would = 0
    for oid in allocation:
        a = allocation[oid]
        prop = proposed[oid]
        prop_cid = prop["cid"]
        new_cid = a["cid"]
        prop_cand = _cand_by_cid(_ga_results.get(oid), prop_cid)
        prop_metrics = getattr(prop_cand, "metrics", None) or {}
        prop_km = prop_metrics.get("km_to_pickup")
        prop_orig_score = prop["score"]            # score z chwili propozycji (info)
        new_score = a["score"]
        cand_scores = a.get("cand_scores") or {}
        pingpong_score_maps[str(oid)] = cand_scores
        # BIEŻĄCY score proponowanego kuriera dla tego zlecenia (po globalnej alokacji
        # innych) — None gdy wypadł z puli feasible. To jest właściwa baza porównania.
        prop_now_score = cand_scores.get(str(prop_cid)) if prop_cid else None
        changed = (new_cid != prop_cid)
        delta_now = None
        if new_score is not None and prop_now_score is not None:
            delta_now = round(new_score - float(prop_now_score), 1)
        delta_orig = None
        if new_score is not None and prop_orig_score is not None:
            delta_orig = round(new_score - float(prop_orig_score), 1)
        # would_repropose: kurier się zmienił I (rozbicie pile-on LUB proponowany wypadł
        # z puli LUB nowy istotnie lepszy od AKTUALNego score proponowanego).
        better_now = (prop_now_score is None) or (delta_now is not None and delta_now >= margin)
        would = bool(changed and not a.get("no_courier") and (spread_improved or better_now))
        if a.get("feral_claim_dropped"):
            reason = "drop_feral_claim"
            would = False
        elif a.get("no_courier"):
            reason = "brak_feasible_kuriera_KOORD"
            would = False
        elif not changed:
            reason = "bez_zmian"
        elif prop_now_score is None:
            reason = "proponowany_wypadl"   # proponowany kurier zniknął z puli feasible
        elif spread_improved:
            reason = "rozjazd_kierunkow"     # globalny fix: rozbicie pile-on jednego kuriera (nagłówek)
        elif delta_now is not None and delta_now >= margin:
            reason = "lepszy_kurier"         # proponowany się obładował / ktoś bliżej i lepszy
        else:
            reason = "zmiana_marginalna"
            would = False
        if would:
            n_would += 1
        row = {
            "ts": now.isoformat(),
            "order_id": oid,
            "proposed_cid": prop_cid,
            "proposed_orig_score": round(float(prop_orig_score), 1) if prop_orig_score is not None else None,
            "proposed_now_score": round(float(prop_now_score), 1) if prop_now_score is not None else None,
            "new_cid": new_cid,
            "new_score": new_score,
            "delta_vs_now": delta_now,
            "delta_vs_orig": delta_orig,
            "would_repropose": would,
            "reason": reason,
            "no_courier": a.get("no_courier", False),
            "proposed_km": round(float(prop_km), 2) if prop_km is not None else None,
            "new_km_to_pickup": round(a["km"], 2) if a.get("km") is not None else None,
            "delta_km": (round(float(a["km"]) - float(prop_km), 2)
                         if a.get("km") is not None and prop_km is not None else None),
            "new_r6_min": round(a["r6"], 1) if a.get("r6") is not None else None,
            "new_deliv_spread_km": round(a["spread"], 1) if a.get("spread") is not None else None,
            "pool_total": a.get("pool_total"),
            "pool_feasible": a.get("pool_feasible"),
            "auto_route": prop.get("auto_route"),
            "expires_at": prop.get("expires_at"),
            "post_expiry": bool(prop.get("expires_at") and prop["expires_at"] < now.isoformat()),
            # globalny kontekst rozjazdu (te same wartości w każdym wierszu ticku)
            "g_hanging": len(allocation),
            "g_couriers_before": couriers_before,
            "g_couriers_after": couriers_after,
            "g_maxpile_before": maxpile_before,
            "g_maxpile_after": maxpile_after,
            "g_spread_improved": spread_improved,
            # INV-FEAS-NO-DOUBLE-BOOK: liczba naruszeń spójności claim-ledger sweepu
            # (>0 tylko gdy ENABLE_CLAIM_LEDGER_INVARIANT_CHECK ON; 0 = brak/OK).
            "g_claim_ledger_breaches": n_claim_breaches,
        }
        if _feral_drop_metric_on:
            # Pole istnieje wyłącznie przy HARD=ON i jest inkrementem per wiersz;
            # suma JSONL równa się licznikowi summary (bez N× overcountu).
            # HARD=OFF zachowuje dzisiejszy JSON bajtowo.
            row["g_claim_ledger_feral_drops"] = int(
                bool(a.get("feral_claim_dropped")))
            row["feral_claim_dropped"] = bool(a.get("feral_claim_dropped"))
            row["dropped_cid"] = a.get("dropped_cid")
        rows.append(row)

    # G6: shadow zachowuje osobny kontrfaktyczny stan; LIVE ufa wyłącznie
    # provenance faktycznie wykonanych podmian z pending_proposals.
    try:
        if _live_armed:
            live_pingpong_state = _pingpong_state_from_live(proposed)
            _annotate_pingpong_rows(
                rows, pingpong_score_maps, live_pingpong_state, now, margin,
                pingpong_margin_multiplier, pingpong_cooldown_min)
        else:
            _annotate_pingpong_shadow(
                rows, pingpong_score_maps, now, margin,
                pingpong_margin_multiplier, pingpong_cooldown_min)
    except Exception as exc:  # noqa: BLE001 — pomiar nie może wywalić shadow-ticka
        _log.warning("pingpong guard telemetry fail: %s: %s", type(exc).__name__, exc)
        for row in rows:
            # LIVE fail-closed: brak oceny historii nigdy nie przepuszcza podmiany.
            # Shadow tylko ujawnia lukę pomiaru (None), bez zmiany would_repropose.
            row["would_pingpong_block"] = True if _live_armed else None
            row["pingpong_state_error"] = type(exc).__name__
            if _live_armed:
                row["pingpong_guard_fail_closed"] = True

    # K5 LIVE: akcje PRZED zapisem jsonl — wiersze dostają marker live_action
    # (audytowalność per zlecenie: co podmieniono / czemu pominięto).
    live_acted = 0
    if _live_armed:
        try:
            live_acted = _live_apply(rows, _ga_results or {}, now)
        except Exception as e:  # noqa: BLE001 — live nigdy nie wywala shadow-ticka
            _log.warning(f"K5 live apply fail-soft: {type(e).__name__}: {e}")

    # Final allocation results only (not intermediate counterfactual rounds).
    # This keeps one decision-time ETA snapshot per order in the sweep.
    if _ga_results:
        _rows_by_oid = {str(row.get("order_id")): row for row in rows}
        try:
            from dispatch_v2 import decision_eta_log as _dtlog
            for _oid, _res in _ga_results.items():
                _row = _rows_by_oid.get(str(_oid), {})
                _live_action = _row.get("live_action")
                _dtlog.record_pipeline_decision(
                    _res,
                    decision_id=f"pending_global_resweep:{_oid}:{now.isoformat()}",
                    decision_ts=now,
                    decision_kind="global_resweep_allocation",
                    source="pending_global_resweep",
                    outcome=(
                        str(_live_action)
                        if _live_action else "SHADOW_ALLOCATION"
                    ),
                    selected_cid=(
                        str(_row.get("new_cid"))
                        if _row.get("new_cid") not in (None, "") else None
                    ),
                    context={"live_armed": bool(_live_armed)},
                )
        except Exception as exc:  # defense-in-depth: log-only path
            _log.warning("decision ETA resweep hook fail-safe: %s", exc)

    _append_jsonl(rows)

    # Faza C (2026-06-27): dedykowany kanał globalnej alokacji DLA KONSOLI.
    # resweep (proces POZA gorącą ścieżką, co 1 min) nadpisuje global_alloc.json PEŁNYM
    # bieżącym podziałem wiszących; feed.py overlay pokazuje to na tablicy. NIE dotyka
    # shadow_decisions.jsonl (audyt 27.06 — zostaje czysty). Serializacja przez
    # shadow_dispatcher._serialize_result = ten sam kształt co shadow_decisions →
    # feed._proposal_from_decision parsuje identycznie. Flaga OFF=no-op. Fail-soft.
    if _alloc_write and _ga_results:
        try:
            from dispatch_v2 import shadow_dispatcher as _sd
            from dispatch_v2 import global_alloc_store as _gas
            _props: Dict[str, Any] = {}
            for _oid, _res in _ga_results.items():
                try:
                    _props[str(_oid)] = _sd._serialize_result(_res, f"globalloc-{_oid}", 0.0)
                except Exception as _se:
                    _log.warning(f"global_alloc serialize fail oid={_oid}: {_se}")
            _w = _gas.write(_props, now)
            _log.info(f"GLOBAL_ALLOC_WRITE proposals={_w}")
        except Exception as _gae:
            _log.warning(f"global_alloc write fail: {_gae}")

    # K5 LIVE wpięte wyżej (_live_apply przed _append_jsonl); locking = kanon
    # pending_proposals_store (L7.5). Telegram NIETYKALNY (wyciszony 26.06).

    summary = {
        "hanging": len(hanging),
        "would_repropose": n_would,
        "couriers_before": couriers_before, "couriers_after": couriers_after,
        "maxpile_before": maxpile_before, "maxpile_after": maxpile_after,
        "spread_improved": spread_improved,
        "claim_ledger_breaches": n_claim_breaches,
        "live_acted": live_acted,
        "margin": margin,
        "pingpong_margin_multiplier": pingpong_margin_multiplier,
        "pingpong_cooldown_min": pingpong_cooldown_min,
        "would_pingpong_block": sum(
            1 for row in rows if row.get("would_pingpong_block") is True),
        "duration_s": round(time.monotonic() - _t0, 2),
        "ts": now.isoformat(),
    }
    if _feral_drop_metric_on:
        summary["claim_ledger_feral_drops"] = n_claim_feral_drops
    _log.info(f"PENDING_RESWEEP sweep {summary}")
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_once()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
