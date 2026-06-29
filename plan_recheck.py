"""plan_recheck — V3.19c sub C periodic consistency checker.

Standalone script. Reads courier_plans.json + orders_state.json. For each
non-invalidated plan, verifies invariants:
  1. Every stop.order_id exists in orders_state.
  2. Status of each order is 'assigned' or 'picked_up' (not delivered/
     cancelled/returned).
  3. Plan age (now - last_modified_at) under threshold.

Rozbieżności → structured log to plan_recheck_log.jsonl. Auto-invalidate
(AUTO_INVALIDATE_STALE=True env) gdy znaleziony delivered/cancelled order
w plan.

NIE re-optymalizuje TSP (deferred V3.19d — wymaga read integration).
NIE modyfikuje scoring path — read-only + optional invalidate.

Invocation: python3 -m dispatch_v2.plan_recheck (stdlib only, no deps).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dispatch_v2 import plan_manager

_log = logging.getLogger("plan_recheck")
if not _log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)

RECHECK_LOG_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/plan_recheck_log.jsonl"
)
ORDERS_STATE_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/orders_state.json"
)
GPS_PWA_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/gps_positions_pwa.json"
)

AUTO_INVALIDATE_STALE = os.environ.get("AUTO_INVALIDATE_STALE", "0") == "1"

# V3.19c sub D: GPS drift check.
# True → gdy kurier GPS > GPS_DRIFT_THRESHOLD_M od plan.start_pos i flag
# ENABLE_GPS_DRIFT_INVALIDATION → plan_manager.mark_stale(cid, "GPS_DRIFT").
# Default OFF — shadow observation tylko.
ENABLE_GPS_DRIFT_INVALIDATION = os.environ.get(
    "ENABLE_GPS_DRIFT_INVALIDATION", "0"
) == "1"
GPS_DRIFT_THRESHOLD_M = int(os.environ.get("GPS_DRIFT_THRESHOLD_M", "500"))
GPS_DRIFT_FRESHNESS_MIN = int(os.environ.get("GPS_DRIFT_FRESHNESS_MIN", "5"))

MAX_PLAN_AGE_MIN = int(os.environ.get("MAX_PLAN_AGE_MIN", "120"))

# KROK 2 (źródłowy fix bugu "apka pokazuje czas restauracji zamiast ustalonego"):
# dla każdego żywego pickupu w aktywnym planie, jeśli order ma ustalony
# czas_kuriera_warsaw (obietnica po odpowiedzi do restauracji) a predicted_at
# pickupu jest WCZEŚNIEJSZY (plan policzony zanim czas wpłynął) → podnieś plan do
# obietnicy i przesuń kolejne stopy. Monotoniczne, idempotentne. Default ON.
ENABLE_PICKUP_REFLOOR = os.environ.get("ENABLE_PICKUP_REFLOOR", "1") == "1"

# 2026-06-24 (A — „chcę czasy na bieżąco, te same w apce", Adrian): cache świeżych
# ETA `live_order_eta.json` (czytany przez konsolę koordynatora i apkę kuriera)
# był pisany TYLKO przez shadow_dispatcher przy DECYZJI/propozycji → między
# decyzjami zamarzał (do TTL 20 min) i potrafił pokazywać trasę z dawnej, już
# nieaktualnej propozycji (case Jakub Olchowik: Chicago→Kręta 22:05 = 34 min,
# zamrożone z decyzji 20:57, choć plan miał poprawne 21:38). Fix: po każdym ticku
# (timer co 5 min) dosyłamy do cache'a ŚWIEŻE czasy odbioru+dostawy z AKTUALNEGO
# planu każdego aktywnego kuriera → cache nie zamarza, obie powierzchnie czytają
# to samo i aktualne. Pisze tylko do live_order_eta.json (jak shadow), NIE dotyka
# Telegrama ani decyzji. Default OFF — flip env=1 dropin po testach.
ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH = os.environ.get(
    "ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH", "0"
) == "1"

# 2026-06-01 (apka pokazuje fallback_nn zamiast trasy Ziomka):
# gdy kurier MA realny worek (≥1 zlecenie assigned/picked_up w orders_state) ale
# NIE ma aktywnego planu w courier_plans.json (np. PANEL_OVERRIDE — koordynator
# przypisał innego kuriera niż Ziomek proponował, więc panel_watcher nie zapisał
# planu) → apka liczy własne geo-NN (fallback_nn). Ten pass gap-fill uruchamia
# realny planner Ziomka (route_simulator_v2) na FAKTYCZNYM worku kuriera i zapisuje
# plan, dzięki czemu apka pokazuje route_source=ziomek_plan z tą samą kolejnością
# i czasami. Tylko gap-fill (brak aktywnego planu) — istniejących planów NIE rusza,
# więc po zapisie kolejny tick pomija kuriera (zero churn). NIE dotyka Telegrama
# (zapis tylko do courier_plans.json czytanego przez apkę). Default ON.
ENABLE_PLAN_FOR_ACTUAL_BAG = os.environ.get(
    "ENABLE_PLAN_FOR_ACTUAL_BAG", "1"
) == "1"
# Powyżej tylu zleceń w worku → skip (za dużo wywołań OSRM × sweep designacji w
# oknie oneshot 120s); apka degraduje do fallback_nn jak dotychczas.
PLAN_FOR_ACTUAL_BAG_MAX = int(os.environ.get("PLAN_FOR_ACTUAL_BAG_MAX", "5"))
# Regeneracja planu BLISKO odbioru. Plan workowy generowany ~2h przed odbiorem i
# zamrażany (zero churn) front-loaduje odbiory: cel świeżości (R6 soft deadline)
# liczony względem „teraz" 2h wcześniej jest za luźny, by gryźć. Gdy najwcześniejszy
# nieodebrany odbiór wchodzi w to okno → odśwież plan, by cel liczył się względem
# czasu bliskiego wykonania (kurier dostaje trasę przeplataną, nie front-load).
# Diagnoza 2026-06-05 (replay: 84→12 naruszeń R6 na dzisiejszych workach).
ENABLE_PLAN_REGEN_NEAR_PICKUP = os.environ.get(
    "ENABLE_PLAN_REGEN_NEAR_PICKUP", "1"
) == "1"
PLAN_REGEN_NEAR_PICKUP_WINDOW_MIN = float(
    os.environ.get("PLAN_REGEN_NEAR_PICKUP_WINDOW_MIN", "45")
)
ACTIVE_STATUSES = frozenset({"assigned", "picked_up"})

TERMINAL_STATUSES = frozenset({"delivered", "cancelled", "returned_to_pool"})


def _haversine_m(p1: tuple, p2: tuple) -> float:
    """Distance in meters between 2 (lat, lng) pairs.

    Fail-loud guards (Lekcja #81 cross-codebase fail-loud sentinel):
    None / (0,0) → ValueError zamiast silent ~6285km drift fałszywy invalidate.
    """
    import math
    if p1 is None or p2 is None:
        raise ValueError(f"_haversine_m: None coords (p1={p1!r}, p2={p2!r})")
    if tuple(p1) == (0.0, 0.0) or tuple(p2) == (0.0, 0.0):
        raise ValueError(f"_haversine_m: sentinel (0,0) (p1={p1!r}, p2={p2!r})")
    lat1, lng1 = p1
    lat2, lng2 = p2
    R = 6371008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _load_gps_positions() -> Dict[str, Any]:
    if not GPS_PWA_PATH.exists():
        return {}
    try:
        with open(GPS_PWA_PATH) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        _log.warning(f"gps_positions load fail: {e}")
        return {}


def _gps_drift_check(cid: str, plan: Dict[str, Any],
                     gps_positions: Dict[str, Any],
                     now: datetime) -> Optional[Dict[str, Any]]:
    """Return finding dict {drift_m, age_min, gps_pos, start_pos} if GPS fresh
    AND drift > threshold, else None.
    """
    gps = gps_positions.get(cid)
    if not gps:
        return None
    try:
        ts_str = gps.get("timestamp")
        if not ts_str:
            return None
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_min = (now - ts).total_seconds() / 60.0
    except Exception:
        return None
    if age_min < 0 or age_min > GPS_DRIFT_FRESHNESS_MIN:
        return None  # stale GPS not used for drift detection
    gps_lat = gps.get("lat")
    gps_lon = gps.get("lon")
    if gps_lat is None or gps_lon is None:
        return None
    sp = plan.get("start_pos") or {}
    sp_lat = sp.get("lat")
    sp_lng = sp.get("lng")
    if sp_lat is None or sp_lng is None:
        return None
    # Placeholder start_pos (0,0) — saved from V3.19b hook without coords
    if (sp_lat, sp_lng) == (0.0, 0.0):
        return None
    drift = _haversine_m((gps_lat, gps_lon), (sp_lat, sp_lng))
    if drift <= GPS_DRIFT_THRESHOLD_M:
        return None
    return {
        "drift_m": round(drift, 1),
        "gps_age_min": round(age_min, 1),
        "gps_pos": [gps_lat, gps_lon],
        "start_pos": [sp_lat, sp_lng],
    }


def _load_orders_state() -> Dict[str, Any]:
    if not ORDERS_STATE_PATH.exists():
        return {}
    try:
        with open(ORDERS_STATE_PATH) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        _log.warning(f"orders_state load fail: {e}")
        return {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _log_recheck_entry(entry: Dict[str, Any]) -> None:
    try:
        RECHECK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RECHECK_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.warning(f"recheck log write fail: {e}")


def _check_plan(cid: str, plan: Dict[str, Any],
                orders_state: Dict[str, Any],
                gps_positions: Dict[str, Any],
                now: datetime) -> Dict[str, Any]:
    """Return structured finding dict. issues list is empty when plan healthy."""
    issues: List[str] = []
    auto_invalidate_reason: Optional[str] = None

    stops = plan.get("stops") or []
    stop_oids = {str(s.get("order_id")) for s in stops}

    missing = []
    terminal = []
    for oid in stop_oids:
        rec = orders_state.get(oid)
        if not rec:
            missing.append(oid)
            continue
        st = rec.get("status")
        if st in TERMINAL_STATUSES:
            terminal.append((oid, st))

    if missing:
        issues.append(f"missing_in_orders_state:{','.join(missing)}")
    if terminal:
        issues.append(f"terminal_status:{','.join(f'{o}={s}' for o,s in terminal)}")
        auto_invalidate_reason = "ORDER_DELIVERED_ALL" if all(
            s == "delivered" for _, s in terminal
        ) else "ORDER_CANCELLED"

    # age check
    age_min = None
    try:
        lm = plan.get("last_modified_at")
        if lm:
            lm_dt = datetime.fromisoformat(lm.replace("Z", "+00:00"))
            if lm_dt.tzinfo is None:
                lm_dt = lm_dt.replace(tzinfo=timezone.utc)
            age_min = (now - lm_dt).total_seconds() / 60.0
            if age_min > MAX_PLAN_AGE_MIN:
                issues.append(f"stale_age:{age_min:.1f}min")
    except Exception:
        pass

    # V3.19c sub D: GPS drift check
    gps_drift = _gps_drift_check(cid, plan, gps_positions, now)
    if gps_drift:
        issues.append(f"gps_drift:{gps_drift['drift_m']}m")

    return {
        "ts": now.isoformat(),
        "cid": cid,
        "plan_version": plan.get("plan_version"),
        "age_min": round(age_min, 1) if age_min is not None else None,
        "stops_count": len(stops),
        "missing_orders": missing,
        "terminal_orders": [{"oid": o, "status": s} for o, s in terminal],
        "gps_drift": gps_drift,
        "issues": issues,
        "auto_invalidate_reason": auto_invalidate_reason,
    }


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    """ISO-8601 → aware UTC datetime. None gdy puste/nie-str/nie-parsuje.

    NIE używać dla naiwnych Warsaw timestampów (np. orders_state.picked_up_at
    "YYYY-MM-DD HH:MM:SS" bez offsetu — interpretacja jako UTC = błąd +2h).
    """
    if not s or not isinstance(s, str):
        return None
    try:
        v = s.strip()
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _coords_ok(c: Any) -> bool:
    return (isinstance(c, (list, tuple)) and len(c) == 2
            and c[0] is not None and c[1] is not None)


def _sim_picked_up_at(rec: Dict[str, Any], status: Optional[str]):
    """Realny picked_up_at (aware UTC) dla NIESIONEGO zlecenia — kotwica deadline
    R6 (route_simulator_v2:1030) chroniąca stygnące jedzenie przed deferowaniem. F1.

    Ten sam parser co ścieżka propozycji (`_bag_dict_to_ordersim` →
    parse_panel_timestamp), żeby input symulatora był identyczny jak na Telegramie.
    None gdy flaga OFF / status≠picked_up / brak/niepoprawny timestamp (zachowanie
    sprzed F1: anchor=czas_kuriera). Lazy import — common już załadowany przez R.
    """
    if not ENABLE_PLAN_REAL_PICKED_UP_AT or status != "picked_up":
        return None
    try:
        from dispatch_v2.common import parse_panel_timestamp
        return parse_panel_timestamp(rec.get("picked_up_at"))
    except Exception:
        return None


def _bag_signature(oids: List[str], orders_state: Dict[str, Any]) -> str:
    """Sygnatura worka dla F2 — kiedy Ziomek (po)decyduje SEKWENCJĘ.

    Koduje (order_id, czy_picked_up) posortowane. Zmiana składu worka LUB odbiór
    zlecenia (assigned→picked_up znika węzeł pickup, jedzenie staje się niesione)
    = zmiana sygnatury = re-decyzja. Identyczna sygnatura = tylko re-czasowanie.
    """
    parts = []
    for oid in oids:
        rec = orders_state.get(oid) or {}
        parts.append(f"{oid}:{1 if rec.get('status') == 'picked_up' else 0}")
    return "|".join(sorted(parts))


# --- Kotwica startu trasy: GPS-free (flota z założenia bez GPS) ---------------
# Fix GPS starszy niż próg traktujemy jak BRAK — nie kotwiczymy estymaty na
# pozycji sprzed godzin/dni. Trasę liczymy z tego, co Ziomek SAM zna: committed
# odbiorów + obserwowanych zdarzeń odbioru/doręczenia (czas + lokalizacja
# ostatniego przystanku). Tak liczy człowiek, gdy nikt nie ma GPS.
GPS_FRESH_MAX_MIN = float(os.environ.get("GPS_FRESH_MAX_MIN", "10"))
ENABLE_GPS_FREE_ANCHOR = os.environ.get("ENABLE_GPS_FREE_ANCHOR", "0") == "1"
# 2026-06-26 (case 509 „plan tkwi 52 min"): ostatnia deska kotwicy dla kuriera BEZ
# świeżego GPS + bez kotwicy zdarzeniowej/committed → sięgnij do last-known-pos store
# (`courier_last_pos.json`, ten sam co courier_resolver rescue 08.06: TTL 25 min + bbox).
# Bez tego `_start_anchor`=None → `_gen_one_bag_plan` pomija CAŁEGO kuriera → plan nigdy
# się nie regeneruje (tkwi invalidated ze starymi dowiezionymi + bez nowych aktywnych).
# Parytet z decyzyjną ścieżką (courier_resolver JUŻ rescue'uje). Default OFF = bez zmiany.
ENABLE_GPS_FREE_ANCHOR_LAST_POS = os.environ.get("ENABLE_GPS_FREE_ANCHOR_LAST_POS", "0") == "1"
# F1 unifikacja silnika trasy: przekaż REALNY picked_up_at do symulatora (jak
# ścieżka propozycji `_bag_dict_to_ordersim`), żeby kara R6 soft-deadline
# (route_simulator_v2:1030) chroniła NIESIONE jedzenie. Bez tego anchor=None →
# `continue` → carried bez deadline → solver deferuje stygnące jedzenie. Default OFF.
ENABLE_PLAN_REAL_PICKED_UP_AT = os.environ.get("ENABLE_PLAN_REAL_PICKED_UP_AT", "0") == "1"
# F2 zunifikowany silnik trasy: Ziomek decyduje SEKWENCJĘ tylko na zmianę worka
# (bag_signature), a tick TYLKO re-czasuje wzdłuż stałej kolejności. Bez tego
# plan_recheck re-optymalizował co tick (oscylacja carried-first↔last). Default OFF.
ENABLE_PLAN_SEQUENCE_LOCK = os.environ.get("ENABLE_PLAN_SEQUENCE_LOCK", "0") == "1"
# F6: TWARDE niezmienniki kolejności W DECYZJI kanonu (carried-first + odbiory wg
# committed) + re-czasowanie po reorderze. Te same reguły co build_view, ale w
# kanonie → wszystkie powierzchnie (apka/panele/Telegram) widzą TĘ SAMĄ, poprawną
# kolejność (reorder build_view staje się no-op). Niezależne od pilności R6. OFF.
ENABLE_PLAN_CANON_ORDER_INVARIANTS = os.environ.get(
    "ENABLE_PLAN_CANON_ORDER_INVARIANTS", "0") == "1"

# Z-RULE (Adrian 2026-06-13, case Bartek/Raj 480295+480434): NIGDY nie wracaj do
# restauracji, którą kurier już opuścił, niosąc/po kolejnym odbiorze. Dwa odbiory
# z tej samej restauracji bierzemy w JEDNEJ wizycie (re-czasowanie clampuje
# committed → 2. order = czekanie pod restauracją, NIE powrót 2.5 km tam i z
# powrotem). DETEKCJA zawsze ON (log BACK_TO_DEPARTED_RESTAURANT — sygnał nawet
# przy fix OFF), REORDER za flagą (shadow-first, flip po ACK). Default OFF.
ENABLE_NO_RETURN_TO_DEPARTED_PICKUP = os.environ.get(
    "ENABLE_NO_RETURN_TO_DEPARTED_PICKUP", "0") == "1"

# COMMITTED-PROPAGATION (Adrian 2026-06-22, case Michał K. Goodboy+Sushi 482630/482633):
# re-sekwencer worka był ŚLEPY na punktualność committed, bo OrderSim budowany tu NIE
# niósł `czas_kuriera_warsaw` (tylko jako pickup_ready_at = dolna granica „nie odbieraj
# przed gotowym"). Cała egzekucja w route_simulator_v2 (okno frozen V3.27.4 :955, miękka
# kara N5 :1145 coeff=100, post-solve assercja :1310) czyta getattr(ref,"czas_kuriera_
# warsaw") → None → ciche no-opy. dispatch_pipeline.py:2642 dokleja to pole ręcznie; tu
# nie było. Fix: doklej pole tak samo (raw string). Efekt zależny od miękkiej kary
# (ENABLE_OBJ_COMMITTED_PICKUP_PENALTY, już ON) → gated, default OFF; flip dopiero po
# replayu na korpusie (przestawienia worków vs SLA/KOORD). Default OFF = zero zmiany.
ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION = os.environ.get(
    "ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION", "0") == "1"
# F3: natychmiastowa decyzja sekwencji NA ZMIANĘ WORKA (override/reassign) z
# panel_watcher — Ziomek układa trasę od razu, bez czekania ≤5 min na tick. Tylko
# gdy żaden ważny plan nie pokrywa worka (nie nadpisuje trasy z propozycji). OFF.
ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE = os.environ.get(
    "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", "0") == "1"
# Redecide także po ODEBRANE (zmiana stanu worka = zmiana bag_signature F2):
# bez tego kanon zdecydowany tuż PRZED wpisem statusu z panelu (reconcile lag
# ~1 min) zostaje z odbiorami przed niesionym aż do następnego 5-min ticku
# (case Gabriel cid=179, 11.06: pickup Mama Thai/Sushi przed dostawą 42PP,
# złe okno 17:03→17:08). Wołane z panel_watcher._update_plan_on_picked_up. OFF.
ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP = os.environ.get(
    "ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP", "0") == "1"
# RECANON-ON-WRITE (Adrian 2026-06-23, „od podstaw nie łatać"): niezmienniki kanonu
# (carried-first floor + odbiory wg committed + relax „po drodze") były dotąd doklejane
# WYŁĄCZNIE przez tick plan_recheck co 5 min. Każdy zapis ZDARZENIOWY (odbiór →
# mark_picked_up, dostawa → advance_plan, przydział → _save_plan_on_assign) pisał plan
# BEZ tej warstwy → niesione nie na froncie / odbiory niescalone wg czasu, aż do
# następnego ticku (case Piotr/Grzesiek/Dawid 23.06). Ta flaga sprawia, że panel_watcher
# RE-EGZEKWUJE kanon na istniejącym planie NATYCHMIAST po każdym zdarzeniu worka (przez
# _retime_one_bag_plan — bez re-TSP, sekwencja Ziomka zachowana). Foundational: kanon
# staje się częścią KAŻDEGO zapisu. Default OFF. Wymaga (jak tick) CANON_INVARIANTS+RELAX.
ENABLE_RECANON_ON_WRITE = os.environ.get("ENABLE_RECANON_ON_WRITE", "0") == "1"
_ANCHOR_EVENT_MAX_AGE_MIN = 360.0  # zdarzenia starsze niż 6h = inna zmiana

# CARRIED-FIRST RELAX (Adrian 2026-06-22, case Sioux→Wierzbowa cid=393): twarda
# reguła carried-first (niesione picked_up dropoffy na FRONT) eliminuje zygzaki,
# bo nie ma wyjątku na odbiór „po drodze". `_relax_carried_first` szuka KRÓTSZEJ
# trasy która (1) dowozi każde niesione jedzenie w ≤SOFT_MAX od picked_up_at,
# (2) nie opóźnia ŻADNEJ innej (przypisanej) dostawy o >DELAY_TOL vs carried-first,
# (3) nie tworzy nowego przekroczenia R6 — i przyjmuje ją TYLKO gdy skraca jazdę
# o >DRIVE_EPS. Inaczej zostaje carried-first. Z konstrukcji: tylko poprawa lub
# no-op (najgorszy przypadek = obecne zachowanie). Replay 29 058 sytuacji z całej
# historii (eod_drafts/2026-06-22): 0 szkód, mediana −3.7 min jazdy/przypadek.
# Default OFF — flip po ACK + spójnym wdrożeniu powierzchni (apka/konsola).
ENABLE_CARRIED_FIRST_RELAX = os.environ.get("ENABLE_CARRIED_FIRST_RELAX", "0") == "1"
CARRIED_FIRST_RELAX_SOFT_MAX_MIN = float(
    os.environ.get("CARRIED_FIRST_RELAX_SOFT_MAX_MIN", "20"))
CARRIED_FIRST_RELAX_DELAY_TOL_MIN = float(
    os.environ.get("CARRIED_FIRST_RELAX_DELAY_TOL_MIN", "3"))
CARRIED_FIRST_RELAX_DRIVE_EPS_MIN = float(
    os.environ.get("CARRIED_FIRST_RELAX_DRIVE_EPS_MIN", "0.3"))
CARRIED_FIRST_RELAX_MAX_STOPS = int(
    os.environ.get("CARRIED_FIRST_RELAX_MAX_STOPS", "8"))

# CARRIED-AGE TZ FIX (Adrian 2026-06-23, root spuchniętych predykcji bundla): relax
# liczył wiek niesionego jedzenia przez `_parse_dt(picked_up_at)`, a picked_up_at to
# NAIWNY czas Warsaw → _parse_dt traktuje go jako UTC (+2h, patrz docstring :273) →
# carried_age ~−120 min (jedzenie „z przyszłości") → guard SOFT_MAX (carry≤20) NIGDY nie
# odrzucał parkowania carried za nowym odbiorem → długi predicted_at dostawy → bundle PRED
# spuchnięty (15,6→28,8 min, real stabilny ~18-20). Fix: parsuj picked_up_at poprawnie
# (parse_panel_timestamp — jak _sim_picked_up_at / ścieżka propozycji Telegrama). Default
# OFF — flip po replay (carried_first_replay) + ACK. ON = relax znów respektuje świeżość
# (zostaje carried-first, gdy carried nie zdąży ≤SOFT_MAX od ODEBRANIA).
ENABLE_CARRIED_AGE_TZ_FIX = os.environ.get("ENABLE_CARRIED_AGE_TZ_FIX", "0") == "1"

# P-1 LEX-COMMITTED-WINDOW (handoff 2026-06-24, audyt P-1): HARD okno odbioru ±tol
# (R-DECLARED-TIME/R27) PRZEGRYWAŁO z SOFT carried-first w kanonie (carried-first wpychał
# niesione na front bezwarunkowo, relax ślepy na BEZWZGLĘDNE okno + flat cap świeżości 20).
# Fix „constrained lex D": wśród permutacji precedence-valid + NO-RETURN, FEASIBLE (brak
# nowego R6 vs baseline, carried ≤ R6=35, żadna INNA dostawa nie później >TOL vs baseline) →
# minimalizuj (naruszenia_okna, jazda, wiek_carried). Carried-first = emergentna miękka
# preferencja, NIE twardy niezmiennik. Anchored na wyniku carried-first+relax → NIGDY nie
# regresuje vs produkcja. Replay D zero-harm (eod_drafts/2026-06-24/lex_window_replay.py):
# tol=5 −24% okno/+4177m jazdy, tol=10 −32%/+4368m, R6/carry/deliv harm = 0 OBA. Dwie flagi:
#  SHADOW (oblicz + loguj rozjazd D-vs-live, ZERO zmiany decyzji) → mierz peak,
#  APPLY (zmień decyzję) → flip po obserwacji. Default OFF.
ENABLE_LEX_COMMITTED_WINDOW_SHADOW = os.environ.get("ENABLE_LEX_COMMITTED_WINDOW_SHADOW", "0") == "1"
ENABLE_LEX_COMMITTED_WINDOW = os.environ.get("ENABLE_LEX_COMMITTED_WINDOW", "0") == "1"
# Tolerancja okna: strict 5 (load-aware loose 10 @ loadgov≥4.5 = TODO wpiąć loadgov w plan_recheck;
# na razie stała, tunable z shadow). Mirror OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN.
LEX_WINDOW_TOL_MIN = float(os.environ.get("LEX_WINDOW_TOL_MIN", "5"))
LEX_WINDOW_DELAY_TOL_MIN = float(os.environ.get("LEX_WINDOW_DELAY_TOL_MIN", "3"))
LEX_WINDOW_MAX_STOPS = int(os.environ.get("LEX_WINDOW_MAX_STOPS", "8"))
LEX_WINDOW_SHADOW_PATH = "/root/.openclaw/workspace/dispatch_state/lex_committed_window_shadow.jsonl"

# FIX K — WSPÓŁLOKALNY ODBIÓR (Adrian 2026-06-24, case Kuba Olchowik 370 Rany Julek):
# reguła no-return seeduje restauracje carried jako 'opuszczone przed trasą' (idx=-2 w
# `_detect_departed_pickup_revisit`), więc relax ODRZUCA każdą permutację z odbiorem w
# tej restauracji — także gdy kurier WŁAŚNIE TAM STOI (start_pos == R). Skutek: zamiast
# zabrać współlokalne zlecenie od razu (koszt ~0), plan każe dowieźć niesione i WRÓCIĆ po
# nie 2.5 km. Korekta: restauracja w promieniu COLOC_M od pozycji kuriera NIE jest
# 'opuszczona' → relax może wziąć współlokalny odbiór na początek (jedna wizyta). Świeżość
# carried wciąż chroniona istniejącym SOFT_MAX. Replay 06-24: 15 worków, −drive, 0 regresji
# >SOFT_MAX. Default OFF. Wymaga ENABLE_CARRIED_FIRST_RELAX (działa wewnątrz relaxu).
ENABLE_RELAX_COLOC_PICKUP = os.environ.get("ENABLE_RELAX_COLOC_PICKUP", "0") == "1"
RELAX_COLOC_PICKUP_M = float(os.environ.get("RELAX_COLOC_PICKUP_M", "180"))

# FIX M — REORDER DROPOFFÓW W WORKU BEZ NIESIONYCH (Adrian 2026-06-24, case Mateusz
# Ostapczuk 413 Skłodowska/Lipowa): carried-first/committed/relax NIE ruszają worka bez
# żadnego niesionego (relax wymaga carried; sequence-lock wyłączył per-tick re-TSP) →
# zamrożona kolejność z insert_stop_optimal daje zygzaki (Skłodowska przed Lipową, choć
# Lipowa 0.26 km od odbioru). Min-jazda po permutacjach (precedencja odbiór<dostawa,
# committed odbiorów dotrzymane, brak nowego R6, ŻADNA dostawa/odbiór nie później >TOL vs
# obecna kolejność) — przyjmuje TYLKO gdy skraca jazdę >EPS. Deterministyczne, tylko
# poprawa lub no-op. NIE oscyluje (brak carried = brak konfliktu carried-first↔last; ta
# sama własność co relax, który już biega co tick). Replay 06-24: 18 worków, 0 pogorszeń.
# Default OFF. Brak interakcji z relaxem (mutualnie wykluczające: relax tylko-carried).
ENABLE_NONCARRIED_DROPOFF_REORDER = os.environ.get("ENABLE_NONCARRIED_DROPOFF_REORDER", "0") == "1"
NONCARRIED_REORDER_MAX_STOPS = int(os.environ.get("NONCARRIED_REORDER_MAX_STOPS", "8"))
NONCARRIED_REORDER_DRIVE_EPS_MIN = float(os.environ.get("NONCARRIED_REORDER_DRIVE_EPS_MIN", "0.3"))
NONCARRIED_REORDER_DELAY_TOL_MIN = float(os.environ.get("NONCARRIED_REORDER_DELAY_TOL_MIN", "6"))


def _gps_age_min(gps: Dict[str, Any], now: datetime) -> Optional[float]:
    ts = _parse_dt((gps or {}).get("timestamp"))
    return None if ts is None else (now - ts).total_seconds() / 60.0


def _last_event_anchor(cid: str, orders_state: Dict[str, Any],
                       now: datetime) -> Optional[Tuple[Tuple[float, float], datetime]]:
    """Najświeższe realne zdarzenie kuriera → (pozycja, czas), bez GPS.

    Doręczenie (COURIER_DELIVERED) lub odbiór (COURIER_PICKED_UP) z bieżącej
    zmiany. Pozycja: coords dostawy/odbioru danego zlecenia (fallback na
    pickup_coords gdy delivery_coords brak). History `at` = ISO UTC (parsowalne),
    w przeciwieństwie do naiwnego picked_up_at. Zdarzenia >6h pomijamy.
    """
    best_at: Optional[datetime] = None
    best_pos: Optional[Tuple[float, float]] = None
    for rec in orders_state.values():
        if not isinstance(rec, dict) or str(rec.get("courier_id") or "") != cid:
            continue
        for h in rec.get("history", []) or []:
            ev = h.get("event")
            if ev not in ("COURIER_DELIVERED", "COURIER_PICKED_UP"):
                continue
            at = _parse_dt(h.get("at"))
            if at is None:
                continue
            if (now - at).total_seconds() / 60.0 > _ANCHOR_EVENT_MAX_AGE_MIN:
                continue
            loc = rec.get("delivery_coords") if ev == "COURIER_DELIVERED" else None
            if not _coords_ok(loc):
                loc = rec.get("pickup_coords")  # delivery niegeokodowane / odbiór
            if not _coords_ok(loc):
                continue
            if best_at is None or at > best_at:
                best_at, best_pos = at, (float(loc[0]), float(loc[1]))
    if best_at is None:
        return None
    return best_pos, best_at


def _earliest_committed_pickup_anchor(
        oids: List[str], orders_state: Dict[str, Any]
) -> Optional[Tuple[Tuple[float, float], datetime]]:
    """Brak zdarzeń (kurier jeszcze nic nie odebrał) → kotwica na NAJBLIŻSZYM
    committed odbiorze: pozycja = restauracja, czas = committed (twarda podłoga).
    """
    best: Optional[Tuple[Tuple[float, float], datetime]] = None
    for oid in oids:
        rec = orders_state.get(oid) or {}
        if rec.get("status") != "assigned":
            continue
        ck = _parse_dt(rec.get("czas_kuriera_warsaw"))
        pc = rec.get("pickup_coords")
        if ck is None or not _coords_ok(pc):
            continue
        if best is None or ck < best[1]:
            best = ((float(pc[0]), float(pc[1])), ck)
    return best


def _start_anchor(cid: str, oids: List[str], orders_state: Dict[str, Any],
                  gps_positions: Dict[str, Any], now: datetime
                  ) -> Optional[Tuple[Tuple[float, float], Optional[datetime], str]]:
    """(pos, earliest_departure, source) startu symulacji.

    GPS tylko gdy ŚWIEŻY (≤GPS_FRESH_MAX_MIN); inaczej kotwica zdarzeniowa
    (ostatni przystanek, start=teraz) lub — gdy nic nieodebrane — committed
    najbliższego odbioru (pozycja=restauracja, start=committed). None gdy nic
    policzalnego. Flaga OFF → wyłącznie GPS (zachowanie sprzed zmiany).
    """
    gps = gps_positions.get(cid) or {}
    glat, glon = gps.get("lat"), gps.get("lon")
    has_gps = glat is not None and glon is not None
    age = _gps_age_min(gps, now)
    gps_fresh = has_gps and age is not None and age <= GPS_FRESH_MAX_MIN

    if not ENABLE_GPS_FREE_ANCHOR:
        return ((float(glat), float(glon)), None, "gps_pwa") if has_gps else None
    if gps_fresh:
        return (float(glat), float(glon)), None, "gps_pwa"

    ev = _last_event_anchor(cid, orders_state, now)
    if ev is not None:
        return ev[0], None, "last_event"  # pozycja=ostatni przystanek, start=teraz
    cp = _earliest_committed_pickup_anchor(oids, orders_state)
    if cp is not None:
        return cp[0], cp[1], "committed_pickup"  # restauracja + committed jako floor
    # Ostatnia deska: stary GPS lepszy niż nic (np. wszystko assigned bez committed).
    if has_gps:
        return (float(glat), float(glon)), None, "gps_stale"
    # Kurier BEZ GPS: sięgnij do last-known-pos store (parytet z courier_resolver rescue
    # 08.06) zamiast pomijać kuriera → plan się regeneruje, nie tkwi stale. Gated flagą.
    if ENABLE_GPS_FREE_ANCHOR_LAST_POS:
        lp = _last_known_pos_anchor(cid, now)
        if lp is not None:
            _log.info(
                f"START_ANCHOR_LAST_POS cid={cid} pos={tuple(round(x, 4) for x in lp)} "
                f"— plan regeneruje (kurier bez GPS, inaczej skip→tkwi)"
            )
            return lp, None, "last_known_pos"  # pozycja=ostatnia znana, start=teraz
    return None


def _last_known_pos_anchor(cid: str, now: datetime) -> Optional[Tuple[float, float]]:
    """(lat,lon) z last-known-pos store dla kuriera bez GPS, albo None. Reużywa
    czyste funkcje courier_resolver (TTL 25 min + bbox + dozwolone źródła) — JEDEN
    szkielet, zero duplikacji walidacji. Fail-soft (każdy błąd → None = stare zachowanie)."""
    try:
        from dispatch_v2 import courier_resolver as _CR
        entry = _CR._load_last_known_pos().get(str(cid))
        if entry is None:
            return None
        res = _CR._rescue_from_last_pos(entry, now)  # ((lat,lon), source, age) | None
        return res[0] if res is not None else None
    except Exception:
        return None


def _gen_one_bag_plan(cid: str, oids: List[str], orders_state: Dict[str, Any],
                      gps_positions: Dict[str, Any], now: datetime,
                      R: Any) -> bool:
    """Wygeneruj+zapisz plan Ziomka dla faktycznego worka kuriera.

    Zwraca True gdy zapisano, False gdy skip (worek za duży / brak GPS / brak
    coords / niekompletny plan). Wyjątki propagują do callera (per-courier guard).
    """
    if len(oids) > PLAN_FOR_ACTUAL_BAG_MAX:
        return False
    anchor = _start_anchor(cid, oids, orders_state, gps_positions, now)
    if anchor is None:
        return False  # ani (świeży) GPS, ani kotwica czasowa → nie ma od czego liczyć
    pos, anchor_departure, anchor_source = anchor

    sims: Dict[str, Any] = {}
    ck_by_oid: Dict[str, Any] = {}  # raw czas_kuriera_warsaw per oid (tie-breaker)
    for oid in oids:
        rec = orders_state.get(oid) or {}
        dc = rec.get("delivery_coords")
        if not _coords_ok(dc):
            return False  # brak coords dostawy → fallback_nn (jak dotąd)
        status = rec.get("status")
        pc = rec.get("pickup_coords")
        if status != "picked_up" and not _coords_ok(pc):
            return False  # assigned bez coords odbioru → skip cały kurier
        pickup_coords = (float(pc[0]), float(pc[1])) if _coords_ok(pc) \
            else (float(dc[0]), float(dc[1]))  # picked_up: nieużywane (brak pickup-node)
        picked_up_at = _sim_picked_up_at(rec, status)
        sims[oid] = R.OrderSim(
            order_id=oid,
            pickup_coords=pickup_coords,
            delivery_coords=(float(dc[0]), float(dc[1])),
            picked_up_at=picked_up_at,
            status=status,
            pickup_ready_at=_parse_dt(rec.get("czas_kuriera_warsaw")),
        )
        ck_by_oid[oid] = rec.get("czas_kuriera_warsaw")

    # 2026-06-26 tier-aware — PARYTET z feasibility_v2:804/811 (cs.tier_bag =
    # courier_tiers.json[cid].bag.tier).
    #  • drive_speed_mult: speed_mult_for_tier (flaga ENABLE_DRIVE_SPEED_TIER_
    #    CORRECTION OFF → 1.0; drive jest OK — motion ~OSRM, czerwcowy bias −1.37).
    #  • DWELL (właściwa warstwa driftu): plan_recheck (display/re-sekwencja przez
    #    panel_watcher) używał DEFAULTU route_simulator (dropoff 3.5) vs realny ~2.2
    #    (geofence n=793) i feasibility dwell_for_tier (gold 1.5, REKALIBR. 10.06 z
    #    eta_calibration_log 7496 rek — absorbuje per-tier rezyduum ETA) → ZAWYŻAŁ
    #    wyświetlany ETA o ~dwell_gap×stops = „czasy lecą w dół po stopie". Bramka
    #    ENABLE_PLAN_RECHECK_TIER_DWELL (OFF → default = byte-identyczny). NIE dotyka
    #    bramki R6/feasibility (osobna ścieżka, już używa dwell_for_tier). Fail-safe.
    try:
        from dispatch_v2 import common as _C
        from dispatch_v2 import courier_resolver as _CR
        _tinfo = _CR._load_courier_tiers().get(str(cid)) or {}
        _tier = (_tinfo.get("bag") or {}).get("tier")
        _drive_speed_mult = _C.speed_mult_for_tier(_tier)
        if _C.flag("ENABLE_PLAN_RECHECK_TIER_DWELL", False):
            _dwell_pickup, _dwell_dropoff = _C.dwell_for_tier(_tier)
        else:
            _dwell_pickup, _dwell_dropoff = R.DWELL_PICKUP_MIN, R.DWELL_DROPOFF_MIN
    except Exception:
        _drive_speed_mult = 1.0
        _dwell_pickup, _dwell_dropoff = R.DWELL_PICKUP_MIN, R.DWELL_DROPOFF_MIN

    # O2 RE-SEQ (2026-06-27, ENABLE_O2_READY_ANCHOR_SWEEP): flaga + cap-Z liczone RAZ
    # (closure dla _sweep ORAZ committed-tiebreak niżej — twins spójne). OFF = byte-identyczne.
    from dispatch_v2 import common as _C_o2sw
    _o2_on = _C_o2sw.flag("ENABLE_O2_READY_ANCHOR_SWEEP",
                          getattr(_C_o2sw, "ENABLE_O2_READY_ANCHOR_SWEEP", False))
    _o2_z = _C_o2sw.flag("O2_CAP_Z_MIN", getattr(_C_o2sw, "O2_CAP_Z_MIN", 35.0))

    def _o2_key(p):
        """Klucz O2 (over_z, overage, dur) gdy flaga ON / (sla, dur) gdy OFF. over_z = twardy
        cap-Z (carried>Z → na koniec, fallback gdy wszystkie). Bez sequence (dokleja caller)."""
        if _o2_on:
            _over_z = 1 if (p.max_carried_age or 0.0) > _o2_z else 0
            _o2 = p.o2_score if p.o2_score is not None else float("inf")
            return (_over_z, _o2, round(p.total_duration_min, 3))
        return (p.sla_violations, round(p.total_duration_min, 3))

    # Sweep designacji new_order (route_simulator_v2 traktuje 1 order jako wstawiany)
    # → wybierz najlepszy plan deterministycznie (O2 overage+cap-Z gdy ON / sla,dur OFF).
    def _sweep():
        ordered_l = list(sims.keys())
        best = None
        for newoid in ordered_l:
            bag = [sims[o] for o in ordered_l if o != newoid]
            p = R.simulate_bag_route_v2(pos, bag, sims[newoid], now=now, sla_minutes=35,
                                        earliest_departure=anchor_departure,
                                        drive_speed_mult=_drive_speed_mult,
                                        dwell_pickup=_dwell_pickup,
                                        dwell_dropoff=_dwell_dropoff)
            key = _o2_key(p) + (tuple(p.sequence),)
            if best is None or key < best[0]:
                best = (key, p)
        return best[1]

    if ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION:
        # TIE-BREAKER bez regresji dostaw (Adrian 2026-06-22): policz baseline
        # (sims bez committed) ORAZ wariant świadomy committed (doklejone
        # czas_kuriera_warsaw → okno frozen + miękka kara N5 w symulatorze).
        # Przyjmij świadomy TYLKO gdy NIE zwiększa naruszeń SLA dostaw (R6 35min
        # też twarda) — replay 22.06: zachowuje czyste wygrane punktualności
        # odbioru, odrzuca trade-offy gdzie poprawa odbioru psułaby dostawę.
        plan_base = _sweep()
        for _oid in sims:
            sims[_oid].czas_kuriera_warsaw = ck_by_oid.get(_oid)
        plan_ck = _sweep()
        # O2 ON → adoptuj committed gdy nie pogarsza objektywu O2 (over_z, overage, dur);
        # OFF → oryginał (sla_violations) byte-identyczny.
        _committed_ok = (_o2_key(plan_ck) <= _o2_key(plan_base)) if _o2_on \
            else (plan_ck.sla_violations <= plan_base.sla_violations)
        if _committed_ok:
            plan = plan_ck
            _adopted = (plan_ck.sequence != plan_base.sequence
                        or plan_ck.pickup_at != plan_base.pickup_at)
            if _adopted:
                _log.info(
                    f"COMMITTED_TIEBREAK_ADOPT cid={cid} oids={oids} "
                    f"sla={plan_ck.sla_violations} dur={plan_ck.total_duration_min:.1f}")
        else:
            plan = plan_base
            _log.info(
                f"COMMITTED_TIEBREAK_REJECT cid={cid} oids={oids} "
                f"sla_base={plan_base.sla_violations} sla_ck={plan_ck.sla_violations}")
    else:
        plan = _sweep()
    ordered = list(sims.keys())

    # Stopy w REALNEJ kolejności czasowej (przeplot pickup/dropoff) — apka czyta
    # kolejność tablicy stops jako kolejność przejazdu (_plan_stop_sequence).
    events = []
    for oid in ordered:
        pu = plan.pickup_at.get(oid)
        if pu is not None:
            events.append((pu, "pickup", oid))
        dp = plan.predicted_delivered_at.get(oid)
        if dp is None:
            return False  # niekompletny plan — nie zapisujemy częściowego
        events.append((dp, "dropoff", oid))
    events.sort(key=lambda e: e[0])

    stops = []
    for t, kind, oid in events:
        rec = orders_state.get(oid) or {}
        coords = rec.get("pickup_coords") if kind == "pickup" else rec.get("delivery_coords")
        stops.append({
            "order_id": oid,
            "type": kind,
            "coords": {"lat": float(coords[0]), "lng": float(coords[1])},
            "scheduled_at": None,
            "predicted_at": t.isoformat(),
            # 2026-06-26: stempel dwell SPÓJNY z tym, czego użył simulate powyżej
            # (_dwell_pickup/_dwell_dropoff — tier-aware gdy flaga ON, default 1.0/3.5
            # gdy OFF = byte-identyczny). Wcześniej hardkod 3.5 rozjeżdżał się z
            # policzonym predicted_at i psuł wybór pozycji w plan_manager.insert_stop_
            # optimal (_sequence_total_min sumuje dwell_min). Teraz konsekwentny.
            "dwell_min": _dwell_pickup if kind == "pickup" else _dwell_dropoff,
            "status_at_plan_time": "picked_up" if rec.get("status") == "picked_up" else "assigned",
        })

    # F6: twarde niezmienniki kolejności w DECYZJI kanonu (carried-first + odbiory
    # wg committed) + re-czasowanie po reorderze → kanon poprawny i identyczny na
    # wszystkich powierzchniach (reorder build_view staje się no-op). Best-effort:
    # gdy re-czasowanie się nie uda, zostaje surowa kolejność z reorderu (ETA z
    # symulatora) — nadal lepsza kolejność niż bez F6.
    if ENABLE_PLAN_CANON_ORDER_INVARIANTS:
        try:
            reordered = _apply_canon_order_invariants(stops, orders_state, pos, now)
            if [s["order_id"] for s in reordered] != [s["order_id"] for s in stops] or \
               [s["type"] for s in reordered] != [s["type"] for s in stops]:
                retimed = _retime_stops(reordered, pos, anchor_departure, orders_state, now)
                stops = retimed if retimed is not None else reordered
        except Exception as e:
            _log.warning(f"canon_order_invariants cid={cid} fail: {type(e).__name__}: {e}")

    # Floor-at-birth: odbiór nigdy < committed czas_kuriera (≥ start zmiany dla
    # pre-shift, bo proposal ustawia czas_kuriera = shift_start + dojazd). Domyka
    # okno surowego planu między (re)generacją a następnym tickiem refloor.
    if ENABLE_PICKUP_REFLOOR:
        try:
            stops = _floor_pickups_to_committed(stops, orders_state)
        except Exception as e:
            _log.warning(f"floor_pickups_to_committed cid={cid} fail: {type(e).__name__}: {e}")

    _gps = gps_positions.get(cid) or {}
    body = {
        "start_pos": {
            "lat": pos[0], "lng": pos[1],
            "source": anchor_source,
            "source_ts": _gps.get("timestamp") if anchor_source == "gps_pwa" else now.isoformat(),
        },
        "start_ts": now.isoformat(),
        "stops": stops,
        "optimization_method": "incremental",
        # F2: sygnatura worka w chwili DECYZJI sekwencji — kolejne ticki z tą samą
        # sygnaturą tylko re-czasują (nie permutują). Zawsze zapisywane (gdy F2 OFF
        # = nieszkodliwa metadana; gdy ON = baza porównania).
        "bag_signature": _bag_signature(oids, orders_state),
    }
    plan_manager.save_plan(cid, body)
    _log.info(
        f"BAG_PLAN_GENERATED cid={cid} stops={len(stops)} seq={plan.sequence} "
        f"sla={plan.sla_violations} dur={plan.total_duration_min:.1f} anchor={anchor_source}"
    )
    return True


def _retime_stops(stops, pos, anchor_departure, orders_state, now):
    """Przelicz predicted_at wzdłuż DANEJ kolejności stopów: łańcuch OSRM od `pos`
    + clamp committed na odbiorach + dwell. Coords z orders_state (autorytatywne —
    plany z propozycji mają 0,0). KOLEJNOŚCI NIE ZMIENIA. None gdy brak coords/OSRM.
    Używane przez F2 (re-czasowanie) i F6 (po reorderze niezmienników)."""
    if not stops:
        return None
    coords = []
    for s in stops:
        oid = str(s.get("order_id"))
        rec = orders_state.get(oid) or {}
        c = rec.get("pickup_coords") if s.get("type") == "pickup" else rec.get("delivery_coords")
        if not _coords_ok(c):
            return None
        coords.append((float(c[0]), float(c[1])))
    try:
        from dispatch_v2 import osrm_client
    except Exception:
        return None
    points = [pos] + coords
    matrix = osrm_client.table(points, points)
    if not matrix:
        return None
    t = max(now, anchor_departure) if anchor_departure else now
    out = []
    for i, s in enumerate(stops):
        cell = matrix[i][i + 1] if (i + 1) < len(matrix[i]) else None
        leg_min = (cell or {}).get("duration_s")
        leg_min = (leg_min / 60.0) if (leg_min is not None and leg_min < 9e8) else 0.0
        t = t + timedelta(minutes=leg_min)
        if s.get("type") == "pickup":
            ck = _parse_dt((orders_state.get(str(s.get("order_id"))) or {}).get("czas_kuriera_warsaw"))
            if ck is not None and ck > t:
                t = ck  # clamp committed (odbiór nie wcześniej niż deklaracja panelu)
        ns = dict(s)
        ns["predicted_at"] = t.astimezone(timezone.utc).isoformat()
        out.append(ns)
        dwell = s.get("dwell_min")
        if dwell is None:
            dwell = 1.0 if s.get("type") == "pickup" else 3.5
        t = t + timedelta(minutes=float(dwell))
    return out


def _floor_pickups_to_committed(stops, orders_state, min_delta_sec: float = 60.0):
    """Podnieś predicted_at KAŻDEGO odbioru do podłogi `czas_kuriera_warsaw` i
    przesuń o tę samą dodatnią deltę wszystkie kolejne stopy (kaskada w dół).

    Źródłowy odpowiednik `plan_manager.refloor_pickup` zastosowany JUŻ przy budowie
    planu (przed save_plan). `_gen_one_bag_plan` zapisuje surowe czasy symulatora
    (= przyjazd), a pętla refloor w `run_recheck` działa na planach z POCZĄTKU ticku
    → plan (re)generowany w tym samym ticku rodziłby się z czasem odbioru
    WCZEŚNIEJSZYM niż obietnica restauracji (np. kurier pre-shift: przyjazd 9:36 vs
    committed 10:15) i był surowy do następnego ticku — okno, w którym konsola/apka
    czytają zły czas. Floor-at-birth domyka to okno. MONOTONICZNY (tylko później),
    idempotentny (po retime/refloor delta<min → no-op). Mutuje `stops` w miejscu i
    zwraca tę samą listę. Gated `ENABLE_PICKUP_REFLOOR` (ta sama flaga co refloor)."""
    if not stops:
        return stops
    for idx, s in enumerate(stops):
        if s.get("type") != "pickup":
            continue
        ck = _parse_dt((orders_state.get(str(s.get("order_id"))) or {}).get("czas_kuriera_warsaw"))
        if ck is None:
            continue
        pred = _parse_dt(s.get("predicted_at"))
        if pred is None:
            continue
        delta_sec = (ck - pred).total_seconds()
        if delta_sec < min_delta_sec:
            continue
        shift = timedelta(seconds=delta_sec)
        for s2 in stops[idx:]:            # kaskada: odbiór + wszystko po nim
            sp = _parse_dt(s2.get("predicted_at"))
            if sp is not None:
                s2["predicted_at"] = (sp + shift).isoformat()
    return stops


def _repair_dropoffs_after_pickups(seq):
    """Dostawy wyprzedzone przez sortowanie odbiorów → przenieś tuż ZA ich odbiór.

    Worek PRZEPLATANY (odbiór→dostawa→odbiór): sortowanie odbiorów wg committed
    potrafi wepchnąć dostawę przed jej własny odbiór. Stary fail-safe rezygnował
    wtedy z CAŁEGO sortowania → inwersja odbiorów zostawała w kanonie i w apce
    (case Mateusz O 11.06: Zapiecek 16:23 przed Kebab Król 16:21). Zamiast
    rezygnować, każdą taką dostawę wstawiamy bezpośrednio za jej odbiór
    (kolejność względna reszty bez zmian). Przeniesienie dostawy W PRAWO nie
    tworzy nowych naruszeń → pętla domyka się w ≤ liczbie naruszeń; twardy limit
    iteracji = defense-in-depth. None gdy się nie domknęła (caller zostawia
    sekwencję bez zmian — zachowanie jak dawny fail-safe). Lustrzany helper w
    courier_api/courier_orders.py (klucz 'kind' zamiast 'type')."""
    out = list(seq)
    for _ in range(len(out) * len(out) + 1):
        pidx = {str(s.get("order_id")): i for i, s in enumerate(out)
                if s.get("type") == "pickup"}
        viol = next((i for i, s in enumerate(out)
                     if s.get("type") == "dropoff"
                     and pidx.get(str(s.get("order_id")), -1) > i), None)
        if viol is None:
            return out
        pi = pidx[str(out[viol].get("order_id"))]
        s = out.pop(viol)
        out.insert(pi, s)   # po pop odbiór zjechał na pi-1 → insert(pi) = tuż za nim
    return None


def _pickup_rest_key(stop, orders_state):
    """Klucz restauracji odbioru = zaokrąglone pickup_coords (~1 m). Adres bywa
    None/firmowy → coords są wiarygodne; fallback na znormalizowaną nazwę."""
    if stop.get("type") != "pickup":
        return None
    o = orders_state.get(str(stop.get("order_id"))) or {}
    pc = o.get("pickup_coords")
    if pc and len(pc) >= 2:
        try:
            return ("xy", round(float(pc[0]), 5), round(float(pc[1]), 5))
        except (TypeError, ValueError):
            pass
    return ("name", (o.get("restaurant_name") or o.get("restaurant") or "").strip().lower())


def _detect_departed_pickup_revisit(seq, orders_state, carried_rest_keys=None):
    """Z-RULE detekcja: odbiór w restauracji R występujący PO ≥1 stopie pośrednim,
    gdy WCZEŚNIEJ w trasie był już odbiór w tej samej R → kurier opuścił R i ma do
    niej wrócić. Zwraca listę (first_idx, revisit_idx, [oid_first, oid_revisit]);
    pusta = OK. Dwa odbiory z R obok siebie (jedna wizyta) = brak naruszenia.

    `carried_rest_keys`: restauracje, z których kurier JUŻ wiezie jedzenie (carried).
    Traktowane jak odwiedzone i opuszczone PRZED trasą (seed idx=-2) → KAŻDY ich
    odbiór w trasie = powrót (jedzenie w aucie, Adrian 2026-06-22). first_idx<0 =
    pierwsza wizyta to carried (brak węzła odbioru w seq) → oid_first=None."""
    out = []
    first_at = {}
    for rk in (carried_rest_keys or ()):
        if rk is not None:
            first_at[rk] = -2          # odwiedzona+opuszczona przed trasą → każdy odbiór = powrót
    for i, s in enumerate(seq):
        k = _pickup_rest_key(s, orders_state)
        if k is None:
            continue
        if k in first_at and (i - first_at[k]) >= 2:
            fi = first_at[k]
            out.append((fi, i,
                        [(seq[fi].get("order_id") if fi >= 0 else None), s.get("order_id")]))
        else:
            first_at.setdefault(k, i)
    return out


def _coalesce_same_pickup_nodes(seq, orders_state):
    """Z-RULE fix: każdy odbiór w restauracji już opuszczonej przesuwany jest tuż
    ZA pierwszy odbiór w tej R → oba w jednej wizycie. Dostawy wyprzedzone przez
    przesunięcie naprawia repair pass. Iteruje do zbieżności (twardy limit =
    defense-in-depth). Przesunięcie odbioru W LEWO obok bliźniaka nie tworzy
    nowych naruszeń tego samego typu → pętla domyka się."""
    out = list(seq)
    for _ in range(len(out) * len(out) + 1):
        viol = _detect_departed_pickup_revisit(out, orders_state)
        if not viol:
            break
        first_idx, revisit_idx, _oids = viol[0]
        node = out.pop(revisit_idx)          # revisit_idx > first_idx → first_idx stabilny
        out.insert(first_idx + 1, node)      # tuż za pierwszym odbiorem w tej R
    repaired = _repair_dropoffs_after_pickups(out)
    return repaired if repaired is not None else out


def _relax_carried_first(seq, orders_state, start_pos, now):
    """Guarded „po drodze" relaxation of carried-first (Adrian 2026-06-22, Sioux).
    Wejście = kolejność carried-first. Szuka KRÓTSZEJ (jazda) precedence-poprawnej
    permutacji stopów worka, która: (1) dowozi każde niesione (picked_up) jedzenie
    w ≤SOFT_MAX od picked_up_at, (2) nie opóźnia żadnej PRZYPISANEJ dostawy o
    >DELAY_TOL vs wejście, (3) nie dodaje przekroczenia R6 (>35′ w worku). Przyjmuje
    tylko gdy oszczędza >DRIVE_EPS jazdy; inaczej zwraca wejście. Deterministyczne,
    tylko poprawa lub no-op (najgorszy przypadek = carried-first). Replay zero-harm:
    eod_drafts/2026-06-22/carried_first_replay.py."""
    if not ENABLE_CARRIED_FIRST_RELAX:
        return seq
    import itertools
    n = len(seq)
    if n < 3 or n > CARRIED_FIRST_RELAX_MAX_STOPS:
        return seq
    oid_of = [str(s.get("order_id")) for s in seq]
    kind_pick = [s.get("type") == "pickup" for s in seq]
    carried = {oid_of[i] for i in range(n)
               if not kind_pick[i]
               and (orders_state.get(oid_of[i]) or {}).get("status") == "picked_up"}
    if not carried:
        return seq
    coords = []
    for i, s in enumerate(seq):
        rec = orders_state.get(oid_of[i]) or {}
        c = rec.get("pickup_coords") if kind_pick[i] else rec.get("delivery_coords")
        if not _coords_ok(c):
            return seq
        coords.append((float(c[0]), float(c[1])))
    try:
        from dispatch_v2 import osrm_client
    except Exception:
        return seq
    matrix = osrm_client.table([(float(start_pos[0]), float(start_pos[1]))] + coords,
                               [(float(start_pos[0]), float(start_pos[1]))] + coords)
    if not matrix:
        return seq
    leg = []
    for row in matrix:
        lr = []
        for cell in row:
            d = (cell or {}).get("duration_s")
            lr.append((d / 60.0) if (d is not None and d < 9e8) else 9e9)
        leg.append(lr)
    now_min = now.timestamp() / 60.0
    dwell = [float(s.get("dwell_min") if s.get("dwell_min") is not None
                   else (1.0 if kind_pick[i] else 3.5)) for i, s in enumerate(seq)]
    committed_rel = []
    for i in range(n):
        if kind_pick[i]:
            ck = _parse_dt((orders_state.get(oid_of[i]) or {}).get("czas_kuriera_warsaw"))
            committed_rel.append((ck.timestamp() / 60.0 - now_min) if ck is not None else None)
        else:
            committed_rel.append(None)
    carried_age = {}
    for oid in carried:
        _puat = (orders_state.get(oid) or {}).get("picked_up_at")
        if ENABLE_CARRIED_AGE_TZ_FIX:
            # picked_up_at = naiwny Warsaw → parsuj jak ścieżka propozycji (NIE _parse_dt=UTC).
            try:
                from dispatch_v2.common import parse_panel_timestamp
                pa = parse_panel_timestamp(_puat)
            except Exception:
                pa = None
        else:
            pa = _parse_dt(_puat)   # zachowanie sprzed fixa (błąd +2h) — flaga OFF
        carried_age[oid] = (now_min - pa.timestamp() / 60.0) if pa is not None else None
    ppos = {oid_of[i]: i for i in range(n) if kind_pick[i]}
    dpos = {oid_of[i]: i for i in range(n) if not kind_pick[i]}
    pairs = [(ppos[o], dpos[o]) for o in ppos]
    assigned = [o for o in dpos if o not in carried]
    # NO-RETURN (Adrian 2026-06-22): relax NIE wolno cofnąć Z-RULE — kurier nie wraca
    # do restauracji, z której już wiezie jedzenie (carried), ani nie rozbija dwóch
    # odbiorów tej samej restauracji na osobne wizyty. carried_rest = restauracje
    # zleceń niesionych (jedzenie w aucie = restauracja opuszczona).
    carried_rest_keys = set()
    for o in carried:
        rk = _pickup_rest_key({"type": "pickup", "order_id": o}, orders_state)
        if rk is None:
            continue
        # FIX K: restauracja pod którą kurier WŁAŚNIE STOI (start_pos) NIE jest 'opuszczona'
        # → współlokalny odbiór wolno wziąć od razu (jedna wizyta), to NIE powrót.
        if ENABLE_RELAX_COLOC_PICKUP:
            pc = (orders_state.get(o) or {}).get("pickup_coords")
            try:
                if _coords_ok(pc) and _haversine_m(
                        (float(start_pos[0]), float(start_pos[1])),
                        (float(pc[0]), float(pc[1]))) <= RELAX_COLOC_PICKUP_M:
                    continue
            except Exception:
                pass
        carried_rest_keys.add(rk)

    def _walk(perm):
        t = 0.0
        drive = 0.0
        prev = 0
        deliv = [None] * n
        pick = [None] * n
        for si in perm:
            lg = leg[prev][si + 1]
            if lg >= 9e8:
                return None
            drive += lg
            t += lg
            prev = si + 1
            if kind_pick[si]:
                cr = committed_rel[si]
                if cr is not None and cr > t:
                    t = cr
                pick[si] = t
                t += dwell[si]
            else:
                deliv[si] = t
                t += dwell[si]
        carry, breaches = {}, 0
        for i in range(n):
            if kind_pick[i]:
                continue
            oid = oid_of[i]
            dt = deliv[i]
            if dt is None:
                continue
            if oid in carried:
                age = carried_age.get(oid)
                bag = (age + dt) if age is not None else None
                if age is not None:
                    carry[oid] = age + dt
            else:
                bp = pick[ppos[oid]]
                bag = (dt - bp) if bp is not None else None
            if bag is not None and bag > 35.0:
                breaches += 1
        return drive, deliv, carry, breaches, pick

    wA = _walk(tuple(range(n)))
    if wA is None:
        return seq
    driveA, delivA, _carryA, breachesA, pickA = wA
    best = None
    tol = CARRIED_FIRST_RELAX_DELAY_TOL_MIN
    for perm in itertools.permutations(range(n)):
        pos = [0] * n
        for j, si in enumerate(perm):
            pos[si] = j
        if any(pos[p] > pos[d] for p, d in pairs):
            continue
        # NO-RETURN: odrzuć permutację wracającą do restauracji już opuszczonej
        # (carried) lub rozbijającą odbiory tej samej restauracji na dwie wizyty.
        if _detect_departed_pickup_revisit([seq[i] for i in perm], orders_state,
                                           carried_rest_keys):
            continue
        w = _walk(perm)
        if w is None:
            continue
        drive, deliv, carry, breaches, pick = w
        if any(carry.get(o, 0.0) > CARRIED_FIRST_RELAX_SOFT_MAX_MIN for o in carried):
            continue
        if breaches > breachesA:
            continue
        bad = False
        for oid in assigned:
            a, b = delivA[dpos[oid]], deliv[dpos[oid]]
            if a is not None and b is not None and (b - a) > tol:
                bad = True               # nie opóźniaj innej DOSTAWY
                break
            pa, pb = pickA[ppos[oid]], pick[ppos[oid]]
            if pa is not None and pb is not None and (pb - pa) > tol:
                bad = True               # nie opóźniaj ODBIORU (jedzenie czeka pod restauracją)
                break
        if bad:
            continue
        if best is None or drive < best[0]:
            best = (drive, perm)
    if best is not None and best[0] < driveA - CARRIED_FIRST_RELAX_DRIVE_EPS_MIN:
        return [seq[i] for i in best[1]]
    return seq


def _reorder_noncarried_min_drive(seq, orders_state, start_pos, now):
    """FIX M: min-jazda dla worka BEZ niesionych (Adrian 2026-06-24, Mateusz 413).
    Permutuje TYLKO DOSTAWY (dropoffy) między ich slotami — ODBIORY zostają nietknięte na
    swoich miejscach (zero ryzyka spóźnienia committed odbioru / R-DECLARED-TIME). Wybiera
    układ o NAJMNIEJSZEJ jeździe OSRM pod warunkiem: brak NOWEGO przekroczenia R6 (>35′ od
    odbioru do dostawy) — twarde SLA chronione. Dostawa może wylądować kilka min później (w
    granicy R6) — to celowe: dowieź to co po drodze, nie rób zygzaka. Przyjmuje TYLKO gdy
    skraca jazdę >EPS; inaczej no-op. Deterministyczne. NIE rusza worków z niesionymi
    (carried-first/relax). Replay 06-24: same poprawy jazdy, 0 nowych R6."""
    if not ENABLE_NONCARRIED_DROPOFF_REORDER or start_pos is None or now is None:
        return seq
    import itertools
    n = len(seq)
    if n < 3 or n > NONCARRIED_REORDER_MAX_STOPS:
        return seq
    oid_of = [str(s.get("order_id")) for s in seq]
    kind_pick = [s.get("type") == "pickup" for s in seq]
    # tylko worki BEZ niesionych — z niesionymi zajmuje się carried-first + relax
    if any((orders_state.get(oid_of[i]) or {}).get("status") == "picked_up" for i in range(n)):
        return seq
    coords = []
    for i, s in enumerate(seq):
        rec = orders_state.get(oid_of[i]) or {}
        c = rec.get("pickup_coords") if kind_pick[i] else rec.get("delivery_coords")
        if not _coords_ok(c):
            return seq
        coords.append((float(c[0]), float(c[1])))
    try:
        from dispatch_v2 import osrm_client
    except Exception:
        return seq
    matrix = osrm_client.table([(float(start_pos[0]), float(start_pos[1]))] + coords,
                               [(float(start_pos[0]), float(start_pos[1]))] + coords)
    if not matrix:
        return seq
    leg = []
    for row in matrix:
        leg.append([(d / 60.0) if ((d := (cell or {}).get("duration_s")) is not None
                                   and d < 9e8) else 9e9 for cell in row])
    now_min = now.timestamp() / 60.0
    dwell = [float(s.get("dwell_min") if s.get("dwell_min") is not None
                   else (1.0 if kind_pick[i] else 3.5)) for i, s in enumerate(seq)]
    committed_rel = []
    for i in range(n):
        if kind_pick[i]:
            ck = _parse_dt((orders_state.get(oid_of[i]) or {}).get("czas_kuriera_warsaw"))
            committed_rel.append((ck.timestamp() / 60.0 - now_min) if ck is not None else None)
        else:
            committed_rel.append(None)
    ppos = {oid_of[i]: i for i in range(n) if kind_pick[i]}

    def _walk(order):
        """order = lista indeksów stopów w kolejności przejazdu."""
        t = 0.0; drive = 0.0; prev = 0
        deliv = [None] * n; pick = [None] * n
        for si in order:
            lg = leg[prev][si + 1]
            if lg >= 9e8:
                return None
            drive += lg; t += lg; prev = si + 1
            if kind_pick[si]:
                cr = committed_rel[si]
                if cr is not None and cr > t:
                    t = cr
                pick[si] = t; t += dwell[si]
            else:
                deliv[si] = t; t += dwell[si]
        breaches = 0
        for i in range(n):
            if kind_pick[i]:
                continue
            dt = deliv[i]; bp = pick[ppos[oid_of[i]]] if oid_of[i] in ppos else None
            if dt is not None and bp is not None and (dt - bp) > 35.0:
                breaches += 1
        return drive, breaches, deliv

    drop_slots = [i for i in range(n) if not kind_pick[i]]      # pozycje dostaw w seq
    drop_idx = list(drop_slots)                                  # indeksy obiektów-dostaw
    base_order = list(range(n))
    wA = _walk(base_order)
    if wA is None:
        return seq
    driveA, breachesA, delivA = wA
    tol = NONCARRIED_REORDER_DELAY_TOL_MIN
    best = None
    for perm in itertools.permutations(drop_idx):
        order = list(range(n))
        for slot, di in zip(drop_slots, perm):
            order[slot] = di
        # precedencja: każda dostawa po swoim odbiorze (pozycja w 'order')
        posn = {si: j for j, si in enumerate(order)}
        ok = True
        for i in range(n):
            if kind_pick[i]:
                continue
            o = oid_of[i]
            if o in ppos and posn[ppos[o]] > posn[i]:
                ok = False; break
        if not ok:
            continue
        w = _walk(order)
        if w is None:
            continue
        drive, breaches, deliv = w
        if breaches > breachesA:
            continue
        # ŻADNA dostawa nie później >TOL vs obecna kolejność (chroni przed przerzucaniem
        # „kto czeka" w przeładowanych workach — duże przesunięcia odrzucone, drobne OK).
        if any(deliv[i] is not None and delivA[i] is not None and (deliv[i] - delivA[i]) > tol
               for i in range(n) if not kind_pick[i]):
            continue
        if best is None or drive < best[0]:
            best = (drive, order)
    if best is not None and best[0] < driveA - NONCARRIED_REORDER_DRIVE_EPS_MIN:
        return [seq[i] for i in best[1]]
    return seq


def _lex_committed_window_reorder(seq, orders_state, start_pos, now):
    """P-1 (handoff 2026-06-24): okno odbioru committed (R-DECLARED-TIME ±tol) PRZED
    carried-first. Anchored na `seq` (wynik carried-first+relax) → identity zawsze feasible,
    więc lex-min ≤ baseline = NIGDY nie regresuje. Lex-min (naruszenia_okna, jazda, wiek_carried)
    wśród perm precedence-valid + NO-RETURN + FEASIBLE (carried ≤ R6=35, brak nowego R6 vs
    baseline, żadna INNA dostawa nie później >TOL vs baseline). SHADOW: loguje rozjazd zawsze
    (gdy flaga shadow|apply); APPLY: zmienia kolejność tylko gdy ENABLE_LEX_COMMITTED_WINDOW.
    Replay D zero-harm: eod_drafts/2026-06-24/lex_window_replay.py."""
    if not (ENABLE_LEX_COMMITTED_WINDOW_SHADOW or ENABLE_LEX_COMMITTED_WINDOW):
        return seq
    if start_pos is None or now is None:
        return seq
    import itertools
    n = len(seq)
    if n < 3 or n > LEX_WINDOW_MAX_STOPS:
        return seq
    oid_of = [str(s.get("order_id")) for s in seq]
    kind_pick = [s.get("type") == "pickup" for s in seq]
    carried = {oid_of[i] for i in range(n) if not kind_pick[i]
               and (orders_state.get(oid_of[i]) or {}).get("status") == "picked_up"}
    if not carried:
        return seq                      # bez niesionych carried-first nie wiąże — to nie P-1
    coords = []
    for i in range(n):
        rec = orders_state.get(oid_of[i]) or {}
        c = rec.get("pickup_coords") if kind_pick[i] else rec.get("delivery_coords")
        if not _coords_ok(c):
            return seq
        coords.append((float(c[0]), float(c[1])))
    try:
        from dispatch_v2 import osrm_client
    except Exception:
        return seq
    matrix = osrm_client.table([(float(start_pos[0]), float(start_pos[1]))] + coords,
                               [(float(start_pos[0]), float(start_pos[1]))] + coords)
    if not matrix:
        return seq
    leg = []
    for row in matrix:
        leg.append([(d / 60.0) if ((d := (cell or {}).get("duration_s")) is not None
                                   and d < 9e8) else 9e9 for cell in row])
    now_min = now.timestamp() / 60.0
    dwell = [float(s.get("dwell_min") if s.get("dwell_min") is not None
                   else (1.0 if kind_pick[i] else 3.5)) for i, s in enumerate(seq)]
    committed_rel = []
    for i in range(n):
        if kind_pick[i]:
            ck = _parse_dt((orders_state.get(oid_of[i]) or {}).get("czas_kuriera_warsaw"))
            committed_rel.append((ck.timestamp() / 60.0 - now_min) if ck is not None else None)
        else:
            committed_rel.append(None)
    carried_age = {}
    for oid in carried:
        _puat = (orders_state.get(oid) or {}).get("picked_up_at")
        if ENABLE_CARRIED_AGE_TZ_FIX:
            try:
                from dispatch_v2.common import parse_panel_timestamp
                pa = parse_panel_timestamp(_puat)
            except Exception:
                pa = None
        else:
            pa = _parse_dt(_puat)
        carried_age[oid] = (now_min - pa.timestamp() / 60.0) if pa is not None else None
    ppos = {oid_of[i]: i for i in range(n) if kind_pick[i]}
    dpos = {oid_of[i]: i for i in range(n) if not kind_pick[i]}
    pairs = [(ppos[o], dpos[o]) for o in ppos]
    assigned = [o for o in dpos if o not in carried]
    carried_rest = {_pickup_rest_key({"type": "pickup", "order_id": o}, orders_state)
                    for o in carried}
    carried_rest.discard(None)

    def _metrics(perm):
        t = 0.0; drive = 0.0; prev = 0
        deliv = [None] * n; pick = [None] * n
        for si in perm:
            lg = leg[prev][si + 1]
            if lg >= 9e8:
                return None
            drive += lg; t += lg; prev = si + 1
            if kind_pick[si]:
                cr = committed_rel[si]
                if cr is not None and cr > t:
                    t = cr
                pick[si] = t; t += dwell[si]
            else:
                deliv[si] = t; t += dwell[si]
        n_viol = 0; breaches = 0; maxcarry = 0.0
        for i in range(n):
            if kind_pick[i]:
                cr = committed_rel[i]
                if cr is not None and pick[i] is not None and (pick[i] - cr) > LEX_WINDOW_TOL_MIN:
                    n_viol += 1
                continue
            oid = oid_of[i]; dt = deliv[i]
            if dt is None:
                continue
            if oid in carried:
                age = carried_age.get(oid)
                bag = (age + dt) if age is not None else None
                if age is not None:
                    maxcarry = max(maxcarry, age + dt)
            else:
                bp = pick[ppos[oid]] if oid in ppos else None
                bag = (dt - bp) if bp is not None else None
            if bag is not None and bag > 35.0:
                breaches += 1
        return drive, deliv, pick, n_viol, breaches, maxcarry

    base = _metrics(tuple(range(n)))
    if base is None:
        return seq
    bdrive, bdeliv, bpick, bviol, bbreach, bcarry = base
    carry_cap = max(35.0, bcarry)
    best = None
    for perm in itertools.permutations(range(n)):
        pos = [0] * n
        for j, si in enumerate(perm):
            pos[si] = j
        if any(pos[p] > pos[d] for p, d in pairs):
            continue
        if _detect_departed_pickup_revisit([seq[i] for i in perm], orders_state, carried_rest):
            continue
        m = _metrics(perm)
        if m is None:
            continue
        drive, deliv, pick, n_viol, breaches, maxcarry = m
        if maxcarry > carry_cap:
            continue
        if breaches > bbreach:
            continue
        bad = False
        for oid in assigned:
            a, b = bdeliv[dpos[oid]], deliv[dpos[oid]]
            if a is not None and b is not None and (b - a) > LEX_WINDOW_DELAY_TOL_MIN:
                bad = True; break
        if bad:
            continue
        key = (n_viol, round(drive, 1), round(maxcarry, 1))
        if best is None or key < best[0]:
            best = (key, perm, n_viol, drive)
    if best is None:
        return seq
    _bkey, bperm, dviol, ddrive = best
    if list(bperm) == list(range(n)):
        return seq                                  # baseline już lex-optymalny
    # rozjazd: lex-D różny od baseline (i ≤ baseline po anchorze). Shadow log + apply za flagą.
    try:
        from dispatch_v2.core.jsonl_appender import append_jsonl
        append_jsonl(LEX_WINDOW_SHADOW_PATH, {
            "ts": now.isoformat(), "carried": sorted(carried),
            "base_window_viol": bviol, "lex_window_viol": dviol,
            "d_drive_min": round(ddrive - bdrive, 1),
            "base_max_carry": round(bcarry, 1), "lex_max_carry": round(_bkey[2], 1),
            "applied": ENABLE_LEX_COMMITTED_WINDOW,
            "base_seq": [(oid_of[i], "P" if kind_pick[i] else "D") for i in range(n)],
            "lex_seq": [(oid_of[i], "P" if kind_pick[i] else "D") for i in bperm],
        })
    except Exception as e:
        _log.warning("lex_window shadow log fail: %s: %s", type(e).__name__, e)
    _log.info("LEX_COMMITTED_WINDOW base_viol=%d lex_viol=%d d_drive=%.1f apply=%s",
              bviol, dviol, ddrive - bdrive, ENABLE_LEX_COMMITTED_WINDOW)
    if ENABLE_LEX_COMMITTED_WINDOW:
        return [seq[i] for i in bperm]
    return seq


def _apply_canon_order_invariants(stops, orders_state, start_pos=None, now=None):
    """F6: TWARDE niezmienniki kolejności kanonu (1:1 jak build_view, ale w decyzji):
    (1) niesione (picked_up) dropoffy → front (kolejność względna zachowana),
    (2) odbiory wg committed (czas_kuriera) rosnąco. Deterministyczne, niezależne od
    pilności R6. 'Dostawa po odbiorze' trzymana przez repair pass (dostawa
    wyprzedzona sortem → tuż za swój odbiór), NIE przez rezygnację z sortu.
    Zwraca przestawioną listę (te same obiekty stopów). Re-czasowanie robi caller.
    Gdy start_pos+now podane i ENABLE_CARRIED_FIRST_RELAX — końcowy guarded relax
    „po drodze" (tylko poprawa jazdy, nigdy kosztem świeżości/innych dostaw)."""
    seq = list(stops)
    carried = {str(oid) for oid, o in orders_state.items()
               if isinstance(o, dict) and o.get("status") == "picked_up"}
    if carried:
        front = [s for s in seq if s.get("type") == "dropoff" and str(s.get("order_id")) in carried]
        if front and seq[:len(front)] != front:
            rest = [s for s in seq if s not in front]
            seq = front + rest
    pickup_positions = [i for i, s in enumerate(seq) if s.get("type") == "pickup"]
    if len(pickup_positions) >= 2:
        pickup_steps = [seq[i] for i in pickup_positions]

        def _ck(s):
            o = orders_state.get(str(s.get("order_id")))
            dt = _parse_dt(o.get("czas_kuriera_warsaw")) if isinstance(o, dict) else None
            return dt.timestamp() if dt is not None else float("inf")

        ordered = sorted(pickup_steps, key=_ck)
        if [s.get("order_id") for s in ordered] != [s.get("order_id") for s in pickup_steps]:
            new_seq = list(seq)
            for pos_i, s in zip(pickup_positions, ordered):
                new_seq[pos_i] = s
            repaired = _repair_dropoffs_after_pickups(new_seq)
            if repaired is not None:
                seq = repaired
    # Z-RULE: detekcja zawsze (sygnał nawet gdy fix OFF), reorder za flagą.
    try:
        viol = _detect_departed_pickup_revisit(seq, orders_state)
        if viol:
            _log.warning(
                "BACK_TO_DEPARTED_RESTAURANT pairs=%s coalesce=%s",
                [v[2] for v in viol], ENABLE_NO_RETURN_TO_DEPARTED_PICKUP)
            if ENABLE_NO_RETURN_TO_DEPARTED_PICKUP:
                seq = _coalesce_same_pickup_nodes(seq, orders_state)
    except Exception as e:
        _log.warning("no_return_to_departed_pickup fail: %s: %s",
                     type(e).__name__, e)
    if ENABLE_CARRIED_FIRST_RELAX and start_pos is not None and now is not None:
        try:
            relaxed = _relax_carried_first(seq, orders_state, start_pos, now)
            if relaxed is not seq and \
                    [s.get("order_id") for s in relaxed] != [s.get("order_id") for s in seq]:
                _log.info("CARRIED_FIRST_RELAX applied seq=%s",
                          [(s.get("order_id"), s.get("type")) for s in relaxed])
            seq = relaxed
        except Exception as e:
            _log.warning("carried_first_relax fail: %s: %s", type(e).__name__, e)
    # P-1: okno odbioru committed PRZED carried-first (anchored na relax → nie regresuje).
    # Shadow log zawsze gdy flaga shadow/apply; zmiana decyzji tylko gdy APPLY flaga.
    if start_pos is not None and now is not None:
        try:
            lexed = _lex_committed_window_reorder(seq, orders_state, start_pos, now)
            if lexed is not seq and ENABLE_LEX_COMMITTED_WINDOW and \
                    [s.get("order_id") for s in lexed] != [s.get("order_id") for s in seq]:
                _log.info("LEX_COMMITTED_WINDOW applied seq=%s",
                          [(s.get("order_id"), s.get("type")) for s in lexed])
            seq = lexed
        except Exception as e:
            _log.warning("lex_committed_window fail: %s: %s", type(e).__name__, e)
    # FIX M: worek bez niesionych — min-jazda po dropoffach (relax tego nie rusza).
    if ENABLE_NONCARRIED_DROPOFF_REORDER and start_pos is not None and now is not None:
        try:
            mreorder = _reorder_noncarried_min_drive(seq, orders_state, start_pos, now)
            if [s.get("order_id") for s in mreorder] != [s.get("order_id") for s in seq] or \
                    [s.get("type") for s in mreorder] != [s.get("type") for s in seq]:
                _log.info("NONCARRIED_MIN_DRIVE_REORDER applied seq=%s",
                          [(s.get("order_id"), s.get("type")) for s in mreorder])
            seq = mreorder
        except Exception as e:
            _log.warning("noncarried_min_drive fail: %s: %s", type(e).__name__, e)
    return seq


def _retime_one_bag_plan(cid: str, plan: Dict[str, Any], oids: List[str],
                         orders_state: Dict[str, Any],
                         gps_positions: Dict[str, Any], now: datetime) -> bool:
    """F2 RE-CZASOWANIE: przelicz predicted_at wzdłuż ISTNIEJĄCEJ, STAŁEJ sekwencji.

    Ziomek zdecydował kolejność przy zmianie worka; tu tylko odświeżamy czasy
    (kurier jedzie / spóźnia się), NIE permutujemy. Zwraca False gdy brak
    kotwicy/coords/OSRM → caller spada do pełnej decyzji (defense-in-depth).
    """
    stops = plan.get("stops") or []
    if not stops:
        return False
    anchor = _start_anchor(cid, oids, orders_state, gps_positions, now)
    if anchor is None:
        return False
    pos, anchor_departure, anchor_source = anchor
    # F6 też w re-czasowaniu: niezmienniki są DETERMINISTYCZNE (carried-first +
    # committed), więc egzekwowanie ich przy każdym ticku NIE oscyluje (≠ re-
    # optymalizacja solvera) i sprawia, że zamrożone złe sekwencje same się
    # poprawiają na następnym ticku, bez czekania na zmianę worka.
    if ENABLE_PLAN_CANON_ORDER_INVARIANTS:
        try:
            stops = _apply_canon_order_invariants(stops, orders_state, pos, now)
        except Exception as e:
            _log.warning(f"canon_order_invariants(retime) cid={cid} fail: {type(e).__name__}: {e}")
    new_stops = _retime_stops(stops, pos, anchor_departure, orders_state, now)
    if new_stops is None:
        return False

    _gps = gps_positions.get(cid) or {}
    body = {
        "start_pos": {
            "lat": pos[0], "lng": pos[1],
            "source": anchor_source,
            "source_ts": _gps.get("timestamp") if anchor_source == "gps_pwa" else now.isoformat(),
        },
        "start_ts": now.isoformat(),
        "stops": new_stops,
        "optimization_method": plan.get("optimization_method") or "incremental",
        "bag_signature": plan.get("bag_signature") or _bag_signature(oids, orders_state),
        "retimed_at": now.isoformat(),
    }
    plan_manager.save_plan(cid, body)
    _log.info(f"BAG_PLAN_RETIMED cid={cid} stops={len(new_stops)} anchor={anchor_source}")
    return True


# ─── Bug #4 SHADOW (log-only): zamrożona sekwencja RETIME vs świeży solve ──────
_BUG4_RESEQ_SHADOW_PATH = "/root/.openclaw/workspace/dispatch_state/bug4_reseq_shadow.jsonl"
_BUG4_RESEQ_SHADOW_MAX_PER_TICK = 20  # cap kosztu OSRM/OR-tools na tick


def _osrm_drive_min_sum(start, coords_list):
    """Suma minut OSRM po kolei start→c0→c1→… (fail-soft → None)."""
    try:
        from dispatch_v2 import osrm_client as _osrm
    except Exception:
        return None
    tot = 0.0
    cur = (float(start[0]), float(start[1]))
    for c in coords_list:
        r = _osrm.route(cur, (float(c[0]), float(c[1])))
        d = r.get("duration_min") if isinstance(r, dict) else None
        if d is None:
            return None
        tot += float(d)
        cur = (float(c[0]), float(c[1]))
    return tot


def _bug4_reseq_shadow(cid, oids, existing_plan, orders_state, gps_positions, now, R, summary):
    """Bug #4 SHADOW (flaga ENABLE_BUG4_RESEQ_SHADOW, log-only): przy RETIME worka
    ≥2 zleceń policz też ŚWIEŻY solve (jak _gen_one_bag_plan._sweep) i zaloguj deltę
    drive (zamrożona kolejność stopów vs świeża) + czy sekwencja inna. ZERO wpływu na
    decyzje/zapis — tylko jsonl. Fail-soft: każdy błąd = no-op (nie psuje retime)."""
    try:
        from dispatch_v2 import common as _C
        if not _C.flag("ENABLE_BUG4_RESEQ_SHADOW", False):
            return
        if len(oids) < 2:
            return
        if summary.get("bug4_shadow_evals", 0) >= _BUG4_RESEQ_SHADOW_MAX_PER_TICK:
            return
        anchor = _start_anchor(cid, oids, orders_state, gps_positions, now)
        if anchor is None:
            return
        pos, anchor_departure, _src = anchor
        sims = {}
        for oid in oids:
            rec = orders_state.get(oid) or {}
            dc = rec.get("delivery_coords")
            if not _coords_ok(dc):
                return
            status = rec.get("status")
            pc = rec.get("pickup_coords")
            if status != "picked_up" and not _coords_ok(pc):
                return
            pickup_coords = (float(pc[0]), float(pc[1])) if _coords_ok(pc) \
                else (float(dc[0]), float(dc[1]))
            sims[oid] = R.OrderSim(
                order_id=oid, pickup_coords=pickup_coords,
                delivery_coords=(float(dc[0]), float(dc[1])),
                picked_up_at=_sim_picked_up_at(rec, status), status=status,
                pickup_ready_at=_parse_dt(rec.get("czas_kuriera_warsaw")))
        # świeży solve — kopia _sweep z _gen_one_bag_plan (defaulty dwell/mult: delta DRIVE odporna)
        best = None
        for newoid in sims:
            bag = [sims[o] for o in sims if o != newoid]
            p = R.simulate_bag_route_v2(pos, bag, sims[newoid], now=now, sla_minutes=35,
                                        earliest_departure=anchor_departure)
            key = (p.sla_violations, round(p.total_duration_min, 3), tuple(p.sequence))
            if best is None or key < best[0]:
                best = (key, p)
        if best is None:
            return
        plan_fresh = best[1]
        events = []
        for oid in sims:
            # #1 bug4 fix (audyt 28.06): NIE dokładaj fikcyjnego pickupu dla ODEBRANYCH (jedzenie
            # już w worku) — served plan też ma dla nich tylko dropoff. Inaczej fresh dostaje extra
            # węzeł (powrót do restauracji) → fresh_drive zawyżony → delta<0 (fresh „gorszy" od
            # frozen = niemożliwe dla wiernego solvera). Pickup tylko dla NIE-odebranych.
            if sims[oid].status != "picked_up":
                pu = (plan_fresh.pickup_at or {}).get(oid)
                if pu is not None:
                    events.append((pu, oid, "pickup", sims[oid].pickup_coords))
            dp = (plan_fresh.predicted_delivered_at or {}).get(oid)
            if dp is None:
                return
            events.append((dp, oid, "dropoff", sims[oid].delivery_coords))
        events.sort(key=lambda e: e[0])
        fresh_labels = [f"{e[1]}:{e[2]}" for e in events]
        fresh_coords = [e[3] for e in events]
        # kolejność DOSTAW = realna zmienna decyzyjna (plan.sequence, deliveries-only), NIE proxy
        # sort-ts (#8). Czysty sygnał „worek przesekwencjonowany"; sort-ts tylko do trasy drive.
        fresh_deliv_order = [str(o) for o in (plan_fresh.sequence or [])]
        frozen_labels, frozen_coords = [], []
        for s in (existing_plan.get("stops") or []):
            c = s.get("coords") or {}
            if "lat" not in c or "lng" not in c:
                return
            frozen_labels.append(f"{s.get('order_id')}:{s.get('type')}")
            frozen_coords.append((float(c["lat"]), float(c["lng"])))
        if not frozen_coords:
            return
        # frozen kolejność DOSTAW (dropoff-y w kolejności planu) — like-for-like z fresh_deliv_order
        frozen_deliv_order = [str(s.get("order_id")) for s in (existing_plan.get("stops") or [])
                              if s.get("type") == "dropoff"]
        fresh_drive = _osrm_drive_min_sum(pos, fresh_coords)
        frozen_drive = _osrm_drive_min_sum(pos, frozen_coords)
        if fresh_drive is None or frozen_drive is None:
            return
        delta = round(frozen_drive - fresh_drive, 2)
        rec_out = {
            "ts": now.isoformat(), "cid": str(cid), "bag": [str(o) for o in oids],
            "n_orders": len(oids),
            # REALNY sygnał: czy kolejność DOSTAW inna (plan.sequence vs frozen dropoff-y)
            "deliv_seq_differs": frozen_deliv_order != fresh_deliv_order,
            "frozen_deliv_order": frozen_deliv_order, "fresh_deliv_order": fresh_deliv_order,
            "seq_differs": frozen_labels != fresh_labels,   # pełna trasa (z pickupami) — pomocniczo
            "frozen_drive_min": round(frozen_drive, 2),
            "fresh_drive_min": round(fresh_drive, 2),
            "delta_min": delta,
            # INWARIANT-TRIPWIRE (audyt C9): wierny re-solve NIE może być GORSZY od istniejącego
            # planu (frozen = feasible sekwencja, którą solver też mógł wybrać). delta<−0.5 =
            # pomiar skażony (resztkowy fikcyjny węzeł / semantyka) → suspect, NIE traktuj jak dane.
            "invariant_violation": delta < -0.5,
            "frozen_seq": frozen_labels, "fresh_seq": fresh_labels,
        }
        with open(_BUG4_RESEQ_SHADOW_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec_out, ensure_ascii=False) + "\n")
        summary["bug4_shadow_evals"] = summary.get("bug4_shadow_evals", 0) + 1
    except Exception as e:
        _log.warning(f"bug4_reseq_shadow cid={cid} fail: {type(e).__name__}: {e}")


def redecide_courier(courier_id: str, orders_state: Optional[Dict[str, Any]] = None,
                     gps_positions: Optional[Dict[str, Any]] = None,
                     now: Optional[datetime] = None,
                     reason: str = "override") -> bool:
    """F3: natychmiastowa decyzja sekwencji dla JEDNEGO kuriera (wywoływana z
    panel_watcher na zmianę worka: override/reassign LUB odebranie zlecenia),
    bez czekania na 5-min tick.

    Samo-bramkująca: jeśli ważny plan POKRYWA cały bieżący worek I ma AKTUALNĄ
    bag_signature → no-op (NIE nadpisuje trasy z propozycji). Pokrycie bez
    aktualnej sygnatury = plan sprzed zmiany stanu worka (np. odebranie) →
    decyzja od nowa, dokładnie jak na 5-min ticku F2, tylko natychmiast.
    Inaczej liczy kanon `_gen_one_bag_plan`. Best-effort, zawsze zwraca bool,
    nigdy nie rzuca. reason: 'override' (flaga F3) / 'pickup' (osobna flaga).
    """
    if reason == "pickup":
        if not ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP:
            return False
    elif not ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE:
        return False
    try:
        from dispatch_v2 import route_simulator_v2 as R
        cid = str(courier_id)
        if orders_state is None:
            try:
                with open(ORDERS_STATE_PATH) as fh:
                    orders_state = json.load(fh)
            except Exception:
                return False
        oids = [str(oid) for oid, rec in orders_state.items()
                if isinstance(rec, dict) and str(rec.get("courier_id") or "") == cid
                and rec.get("status") in ACTIVE_STATUSES]
        if not oids:
            return False
        # Już pokryte ważnym planem (np. świeży zapis propozycji)? → nie ruszaj.
        # Wyjątek reason='pickup': pokrycie NIE wystarcza — odebranie zmienia
        # bag_signature, a plan zdecydowany przed wpisem statusu może mieć
        # niesione w środku trasy; redecide tylko gdy sygnatura nieaktualna.
        # Dla 'override' pokrycie = no-op jak dotąd (plan z propozycji NIE ma
        # własnej bag_signature — zapis dziedziczy starą — więc test sygnatury
        # nadpisywałby świeże trasy z propozycji).
        plan = plan_manager.load_plan(cid)
        if plan and plan.get("stops"):
            covered = {str(s.get("order_id")) for s in plan.get("stops", [])}
            if set(oids) <= covered:
                if reason != "pickup":
                    return False
                if plan.get("bag_signature") == _bag_signature(oids, orders_state):
                    return False
        if gps_positions is None:
            gps_positions = _load_gps_positions()
        if now is None:
            now = datetime.now(timezone.utc)
        ok = _gen_one_bag_plan(cid, oids, orders_state, gps_positions, now, R)
        if ok:
            _log.info(f"REDECIDE_ON_{reason.upper()} cid={cid} bag={len(oids)}")
        return ok
    except Exception as e:
        _log.warning(f"redecide_courier cid={courier_id} fail: {type(e).__name__}: {e}")
        return False


def recanon_courier(courier_id: str, orders_state: Optional[Dict[str, Any]] = None,
                    gps_positions: Optional[Dict[str, Any]] = None,
                    now: Optional[datetime] = None, reason: str = "event") -> bool:
    """RECANON-ON-WRITE: re-egzekwuj niezmienniki kanonu (carried-first floor +
    odbiory wg committed + relax „po drodze") na ISTNIEJĄCYM planie kuriera
    NATYCHMIAST po zdarzeniu worka (odbiór/dostawa/przydział), bez czekania ≤5 min
    na tick i BEZ re-TSP — sekwencja Ziomka zachowana, tylko twarde reguły kolejności
    + re-czasowanie (`_retime_one_bag_plan`). Foundational: kanon = część KAŻDEGO
    zapisu, nie tylko okresowego.

    Self-gating (no-op, zwraca False): flaga OFF / brak aktywnego worka / brak planu /
    plan invalidated (load_plan→None) / plan NIE pokrywa worka (świeży/częściowy po
    przydziale → pełna decyzja należy do _gen lub ticku). Best-effort, nigdy nie rzuca.
    Determinizm niezmienników (carried-first + committed) gwarantuje brak oscylacji
    między zdarzeniem a tickiem (ta sama transformacja co F6/F2)."""
    if not ENABLE_RECANON_ON_WRITE:
        return False
    try:
        cid = str(courier_id)
        if orders_state is None:
            try:
                with open(ORDERS_STATE_PATH) as fh:
                    orders_state = json.load(fh)
            except Exception:
                return False
        oids = [str(oid) for oid, rec in orders_state.items()
                if isinstance(rec, dict) and str(rec.get("courier_id") or "") == cid
                and rec.get("status") in ACTIVE_STATUSES]
        if not oids:
            return False
        plan = plan_manager.load_plan(cid)
        if not plan or not plan.get("stops"):
            return False  # brak/invalidated plan → decyzja należy do _gen/ticku
        covered = {str(s.get("order_id")) for s in plan.get("stops", [])}
        if not (set(oids) <= covered):
            return False  # plan nie pokrywa worka (nowy przydział) → tick/gen
        if gps_positions is None:
            gps_positions = _load_gps_positions()
        if now is None:
            now = datetime.now(timezone.utc)
        ok = _retime_one_bag_plan(cid, plan, oids, orders_state, gps_positions, now)
        if ok:
            _log.info(f"RECANON_ON_{reason.upper()} cid={cid} bag={len(oids)}")
        return ok
    except Exception as e:
        _log.warning(f"recanon_courier cid={courier_id} fail: {type(e).__name__}: {e}")
        return False


def _pickup_approaching(oids: List[str], orders_state: Dict[str, Any],
                        now: datetime) -> bool:
    """True gdy najwcześniejszy NIEODEBRANY odbiór w worku jest w oknie
    PLAN_REGEN_NEAR_PICKUP_WINDOW_MIN od teraz (lub już minął — spóźniony).

    Wtedy plan z pełnym pokryciem warto odświeżyć mimo zero-churn, by cel
    świeżości liczył się względem czasu bliskiego wykonania. Odbiory daleko w
    przyszłości (> okno) → False (zachowanie jak dotąd, brak churnu). Brak
    nieodebranych odbiorów (cały worek picked_up) → False (nic do odświeżenia
    pod kątem front-loadu odbiorów).
    """
    if not ENABLE_PLAN_REGEN_NEAR_PICKUP:
        return False
    soonest: Optional[datetime] = None
    for oid in oids:
        rec = orders_state.get(oid) or {}
        if rec.get("status") == "picked_up":
            continue
        ck = _parse_dt(rec.get("czas_kuriera_warsaw"))
        if ck is None:
            continue
        if soonest is None or ck < soonest:
            soonest = ck
    if soonest is None:
        return False
    delta_min = (soonest - now).total_seconds() / 60.0
    return delta_min <= PLAN_REGEN_NEAR_PICKUP_WINDOW_MIN


def _gap_fill_plans(orders_state: Dict[str, Any], plans: Dict[str, Any],
                    gps_positions: Dict[str, Any], now: datetime,
                    summary: Dict[str, Any]) -> None:
    """Dla kuriera z realnym workiem bez planu LUB z planem CZĘŚCIOWYM →
    wygeneruj plan Ziomka i zapisz, by apka pokazała ziomek_plan zamiast
    fallback_nn.

    Dwa przypadki regeneracji:
    1. brak aktywnego planu (PANEL_OVERRIDE — koordynator przypisał innego
       kuriera niż Ziomek proponował, więc panel_watcher nie zapisał planu);
    2. aktywny plan pokrywa tylko CZĘŚĆ realnego worka (część zapisana, potem
       doszło nowe zlecenie). courier_api/build_view renderuje ziomek_plan
       TYLKO przy pełnym pokryciu (worek ⊆ plan) — częściowy plan tam spada do
       fallback_nn. Regenerujemy, by ziomek_plan został autorytatywny.

    Plan z PEŁNYM pokryciem (worek ⊆ plan) NIE jest ruszany (zero churn —
    konwerguje: po regeneracji kolejny tick widzi pełne pokrycie i pomija).
    Worek > PLAN_FOR_ACTUAL_BAG_MAX → _gen_one_bag_plan bailuje przed OSRM,
    apka zostaje na spójnym fallbacku. Fail-soft per kurier. NIE dotyka
    Telegrama (zapis tylko do courier_plans.json czytanego przez apkę).
    """
    summary["bag_plans_generated"] = 0
    summary["bag_plans_skipped"] = 0
    summary["bag_plans_partial_regen"] = 0
    summary["bag_plans_near_pickup_regen"] = 0
    summary["bag_plans_retimed"] = 0
    try:
        from dispatch_v2 import route_simulator_v2 as R
    except Exception as e:
        _log.warning(f"gap_fill import fail (skip pass): {e}")
        return

    bags: Dict[str, List[str]] = {}
    for oid, rec in orders_state.items():
        if not isinstance(rec, dict) or rec.get("status") not in ACTIVE_STATUSES:
            continue
        cid = str(rec.get("courier_id") or "")
        if not cid:
            continue
        bags.setdefault(cid, []).append(str(oid))

    for cid, oids in bags.items():
        existing = plans.get(cid)
        valid = (existing is not None and existing.get("invalidated_at") is None
                 and existing.get("stops"))

        # ---- F2: sekwencja zamrożona, decyzja tylko na zmianę worka ----
        if ENABLE_PLAN_SEQUENCE_LOCK:
            if valid and existing.get("bag_signature") == _bag_signature(oids, orders_state):
                # Worek bez zmian (skład + picked_up) → TYLKO re-czasuj, nie permutuj.
                try:
                    if _retime_one_bag_plan(cid, existing, oids, orders_state, gps_positions, now):
                        summary["bag_plans_retimed"] += 1
                        _bug4_reseq_shadow(cid, oids, existing, orders_state,
                                           gps_positions, now, R, summary)
                        continue
                except Exception as e:
                    _log.warning(f"retime cid={cid} fail: {type(e).__name__}: {e}")
                # re-czasowanie się nie udało → spadnij do pełnej decyzji
            # Zmiana worka / brak planu / retime fail → DECYZJA sekwencji (raz).
            try:
                ok = _gen_one_bag_plan(cid, oids, orders_state, gps_positions, now, R)
            except Exception as e:
                summary["bag_plans_skipped"] += 1
                _log.warning(f"gap_fill cid={cid} fail: {type(e).__name__}: {e}")
                continue
            summary["bag_plans_generated" if ok else "bag_plans_skipped"] += 1
            continue

        # ---- F2 OFF: zachowanie sprzed (re-optymalizacja per tick) ----
        partial = False
        near_regen = False
        if valid:
            plan_ids = {str(s.get("order_id"))
                        for s in existing.get("stops", [])
                        if s.get("order_id") is not None}
            if set(oids) <= plan_ids:
                # Pełne pokrycie. Normalnie zero churn — ALE gdy odbiory się
                # zbliżają, odśwież plan, by cel świeżości (R6 soft deadline)
                # liczył się względem czasu bliskiego wykonania. Bez tego
                # zamrożony plan sprzed ~2h front-loaduje odbiory.
                if not _pickup_approaching(oids, orders_state, now):
                    continue  # odbiory daleko → nie nadpisuj (zero churn)
                near_regen = True
            else:
                partial = True  # plan częściowy → regeneruj na pełnym worku
        try:
            ok = _gen_one_bag_plan(cid, oids, orders_state, gps_positions, now, R)
        except Exception as e:
            summary["bag_plans_skipped"] += 1
            _log.warning(f"gap_fill cid={cid} fail: {type(e).__name__}: {e}")
            continue
        summary["bag_plans_generated" if ok else "bag_plans_skipped"] += 1
        if ok and partial:
            summary["bag_plans_partial_regen"] += 1
            _log.info(f"BAG_PLAN_PARTIAL_REGEN cid={cid} bag={len(oids)}")
        if ok and near_regen:
            summary["bag_plans_near_pickup_regen"] += 1
            _log.info(f"BAG_PLAN_NEAR_PICKUP_REGEN cid={cid} bag={len(oids)}")


def _refresh_live_eta_from_plans(plans: Dict[str, Any], summary: Dict[str, Any]) -> None:
    """A: odśwież live_order_eta.json czasami z AKTUALNEGO planu każdego aktywnego
    kuriera (latest-wins, decided_at=now), żeby cache nie zamarzał między decyzjami
    Ziomka. Fail-soft: każdy błąd = no-op (nie wpływa na recheck). Pisze tylko do
    live_order_eta.json (źródło konsoli+apki), nic poza tym."""
    if not ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH:
        return
    try:
        from dispatch_v2 import live_eta_cache as _live_eta
    except Exception:
        return
    refreshed = 0
    for cid, plan in plans.items():
        if not isinstance(plan, dict) or plan.get("invalidated_at") is not None:
            continue
        deliv: Dict[str, str] = {}
        pick: Dict[str, str] = {}
        for s in (plan.get("stops") or []):
            if not isinstance(s, dict):
                continue
            oid = str(s.get("order_id"))
            pa = s.get("predicted_at")
            if not pa:
                continue
            if s.get("type") == "pickup":
                pick.setdefault(oid, pa)
            else:
                deliv.setdefault(oid, pa)
        if not deliv:
            continue
        try:
            if _live_eta.upsert(deliv, pick, cid):
                refreshed += 1
        except Exception:
            pass
    summary["live_eta_refreshed"] = refreshed
    if refreshed:
        _log.info(f"LIVE_ETA_REFRESH couriers={refreshed}")


def run_recheck() -> Dict[str, Any]:
    """Main entry point. Returns summary dict."""
    # ETAP 4 (2026-06-10, Z-04): fingerprint flag decyzyjnych — MUSI być
    # identyczny z shadow/czasowka (re-plan liczy TYM SAMYM silnikiem OBJ).
    try:
        from dispatch_v2 import common as _C
        _log.info("FLAG_FINGERPRINT proc=plan-recheck %s", _C.flag_fingerprint())
    except Exception:
        pass
    now = _now_utc()
    orders_state = _load_orders_state()
    plans = plan_manager.load_plans()

    summary = {
        "ts": now.isoformat(),
        "total_plans": 0,
        "active_plans": 0,
        "healthy": 0,
        "with_issues": 0,
        "auto_invalidated": 0,
    }

    gps_positions = _load_gps_positions()
    summary["gps_drift_detected"] = 0
    summary["gps_drift_invalidated"] = 0
    summary["pickup_refloored"] = 0

    findings: List[Dict[str, Any]] = []
    for cid, plan in plans.items():
        summary["total_plans"] += 1
        if plan.get("invalidated_at") is not None:
            continue
        summary["active_plans"] += 1
        # KROK 2: dosuń pickupy planu do ustalonego czas_kuriera (źródłowy fix).
        # refloor liczy deltę pod lockiem na świeżym pliku, więc przekazanie
        # nieaktualnego snapshotu planu jest bezpieczne (re-read wewnątrz).
        if ENABLE_PICKUP_REFLOOR:
            for s in plan.get("stops", []):
                if s.get("type") != "pickup":
                    continue
                oid = str(s.get("order_id"))
                order = orders_state.get(oid)
                kur = order.get("czas_kuriera_warsaw") if isinstance(order, dict) else None
                if not kur:
                    continue
                shifted_min = plan_manager.refloor_pickup(cid, oid, kur)
                if shifted_min > 0:
                    summary["pickup_refloored"] += 1
                    _log.info(
                        f"PICKUP_REFLOOR cid={cid} oid={oid} "
                        f"shift=+{shifted_min:.1f}min floor={kur}"
                    )
        finding = _check_plan(cid, plan, orders_state, gps_positions, now)
        if finding["issues"]:
            summary["with_issues"] += 1
            findings.append(finding)
            _log_recheck_entry(finding)
            if AUTO_INVALIDATE_STALE and finding.get("auto_invalidate_reason"):
                plan_manager.invalidate_plan(cid, finding["auto_invalidate_reason"])
                summary["auto_invalidated"] += 1
                _log.info(
                    f"AUTO_INVALIDATE cid={cid} reason={finding['auto_invalidate_reason']}"
                )
            if finding.get("gps_drift"):
                summary["gps_drift_detected"] += 1
                if ENABLE_GPS_DRIFT_INVALIDATION:
                    plan_manager.mark_stale(cid, "GPS_DRIFT")
                    summary["gps_drift_invalidated"] += 1
                    _log.info(
                        f"GPS_DRIFT_INVALIDATE cid={cid} drift={finding['gps_drift']['drift_m']}m"
                    )
        else:
            summary["healthy"] += 1

    # Gap-fill: kurierzy z realnym workiem ale bez aktywnego planu → plan Ziomka.
    if ENABLE_PLAN_FOR_ACTUAL_BAG:
        _gap_fill_plans(orders_state, plans, gps_positions, now, summary)

    # A (2026-06-24): po wszystkich zmianach planów dosyłamy świeże ETA do cache'a
    # czytanego przez konsolę+apkę (gap-fill mógł dopisać/zmienić plany → reload).
    if ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH:
        try:
            _refresh_live_eta_from_plans(plan_manager.load_plans(), summary)
        except Exception as _e:
            _log.warning(f"live_eta_refresh fail: {type(_e).__name__}: {_e}")

    _log.info(f"PLAN_RECHECK summary={summary}")
    return summary


if __name__ == "__main__":
    sys.exit(0 if run_recheck()["auto_invalidated"] == 0 or AUTO_INVALIDATE_STALE else 1)
