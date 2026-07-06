#!/usr/bin/env python3
"""reassignment_forward_shadow.py — v2 FORWARD shadow przerzutów (READ-ONLY, OSOBNY PROCES).

Następca offline `reassignment_shadow.py` (v1, 2026-06-07 — werdykt "niejednoznaczny",
bo 85% przerzutów było nieocenialnych: martwe logi nie miały geokodu adresu dostawy).

v2 czyta ŻYWY stan (`orders_state` ma `delivery_coords` + `pickup_coords` per zlecenie)
i dla każdego NIEODEBRANEGO zlecenia O (przypisanego kurierowi A) pyta kontrfaktycznie:
    "gdyby O było TERAZ nieprzypisane, kogo wskazałby Ziomek?"
— wołając PRAWDZIWY `dispatch_pipeline.assess_order` nad pełną (dispatchable) flotą,
z O WYJĘTYM z worka A. Jeśli best != A o margines => `would_reassign=True`.

DLACZEGO PRAWDZIWY assess_order (nie własny scoring): zero dryftu — shadow rankuje
DOKŁADNIE tym samym silnikiem co prod (feasibility_v2 + scoring + OSRM + R6 + A2).

DLACZEGO OSOBNY PROCES (nie hook w shadow_dispatcher hot-path): doktryna projektu —
shadow w hot-path raz wywalił produkcję (V3.27.4 NameError; patrz docstring v1).
Tu wołamy assess_order read-only we WŁASNYM procesie/timerze => latency izolowana,
ZERO ryzyka dla żywego dispatchu. Flaga `ENABLE_REASSIGNMENT_FORWARD_SHADOW` (default OFF).

ZERO MUTACJI: nie pisze orders_state, nie emituje eventów, nie woła Telegrama
(filtrujemy zlecenia bez pickup_coords => omijamy ścieżkę admin-alert w assess_order).
Jedyny zapis: append do `dispatch_state/reassignment_shadow.jsonl`.

⚠ dispatchable_fleet() (NIE surowe build_fleet_snapshot) — wzbogaca shift_end,
inaczej feasibility hard-rejectuje całą flotę (bug czasówki #471036 / Lekcja #80).

Użycie:
  cd /root/.openclaw/workspace/scripts
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.reassignment_forward_shadow
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import json
import os
import tempfile
import logging
import time
import copy as _copy
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

from dispatch_v2 import common as C
from dispatch_v2 import courier_resolver as CR
from dispatch_v2.core.decide import decide as _decide  # K09 fasada
from dispatch_v2.core.world_state import WorldState

_log = logging.getLogger("reassignment_forward_shadow")

ORDERS_STATE = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
OUT_JSONL = "/root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl"
KURIER_IDS_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"  # {nazwa: cid} — aliasy do komunikatu TG

FLAG = "ENABLE_REASSIGNMENT_FORWARD_SHADOW"
MARGIN_KEY = "REASSIGN_FWD_MARGIN"
MAX_ORDERS_KEY = "REASSIGN_FWD_MAX_ORDERS"
DEFAULT_MARGIN = 15.0          # pkt score — rząd wielkości jak AUTO_PROXIMITY min_score_margin
DEFAULT_MAX_ORDERS = 60        # cap zleceń na sweep (latency-guard na 2-vCPU w peaku)
KOORDYNATOR_CID = "26"         # virtual holding bucket (czasówki) — NIE przerzucamy
FLAG_TG = "REASSIGN_FWD_TELEGRAM_LIVE"   # podgląd live na grupę ziomka (default OFF)
NOTIFIED_PATH = "/root/.openclaw/workspace/dispatch_state/reassignment_shadow_notified.json"
TG_CAP = 8                     # max pozycji w 1 komunikacie/sweep (anty-spam grupy)
_SYNTH_POS = {"none", "pin", "pre_shift", ""}  # brak realnej lokalizacji → fikcja/grafik (oznacz „zgadnięta")
# Pewna pozycja = GPS lub ostatnia znana realna lokalizacja (jak silnik liczy no_gps,
# Adrian 22.06). Mirror allow-listy z reassignment_shadow_eval._REAL_POS. Wszystko poza
# tym (pin/pre_shift/none) = pozycja zgadnięta (fikcja centrum/grafik) = szum powiadomień.
_REAL_POS = {"gps", "last_picked_up", "last_delivered", "last_assigned", "last_known", "store"}
NOTIFY_TRUSTED_ONLY_FLAG = "REASSIGN_FWD_NOTIFY_TRUSTED_ONLY"  # notify tylko gdy A i B pewna poz. (default ON)
NOTIFY_COOLDOWN_KEY = "REASSIGN_FWD_NOTIFY_COOLDOWN_MIN"       # min między powtórkami per zlecenie (default 20)
DEFAULT_NOTIFY_COOLDOWN_MIN = 20.0

# --- KROK 1 (2026-06-28): gate JAKOŚCI przerzutu (gradient) — duch tylko gdy „na pewno lepiej".
# Ramię 1 (RATUNEK): obecny dowiezie po czasie (R6>próg / infeasible) a nowy NA CZAS → przerzut.
# Ramię 2 (OSZCZĘDNOŚĆ): obecny na czas, nowy dowiezie >= BIG_SAVE_MIN min wcześniej (krótsza trasa) → przerzut.
# Wymóg: pozycja A i B realna/checkpointowa (NIE fikcja). Metryka „kto kiedy dowiezie" = plan.predicted_delivered_at,
# R6 per-zlecenie = predicted_delivered_at − pickup_at (zgodne z sla_log delivery_time_minutes>35).
# Flaga OFF default → pola quality_* NIE liczone (rekord bajt-identyczny). ON (drop-in timera) = obserwacja log-only.
QUALITY_FLAG = "ENABLE_REASSIGN_QUALITY_GATE"
QUALITY_BIG_SAVE_KEY = "REASSIGN_QUALITY_BIG_SAVE_MIN"
QUALITY_R6_LATE_KEY = "REASSIGN_QUALITY_R6_LATE_MIN"
DEFAULT_BIG_SAVE_MIN = 8.0     # „DUŻO szybciej" dla A-na-czas (Adrian 28.06: 8-10 min) — env-tunable
DEFAULT_R6_LATE_MIN = 35.0     # bag_time(O)>35 = dowóz po czasie (= sla_log delivery_time_minutes>35)
# RESERVE-AWARE (Adrian 28.06, measure: peak median 1 wolny kurier, ~1 zlec/min, koszt braku rezerwy
# +2 min R6 + breachy nadchodzącym): ramię OSZCZĘDNOŚĆ odpala TYLKO jako dołożenie do JADĄCEGO kuriera
# (b_bag>0 = bundling „dwa dalekie na jednego"); WOLNEGO (bag=0) NIE palimy pod samą oszczędność —
# zostaje w rezerwie na napływ. RATUNEK bez zmian (spóźnienie holdera > koszt rezerwy → wolny OK).
OSZCZ_BUNDLING_ONLY_FLAG = "ENABLE_REASSIGN_OSZCZ_BUNDLING_ONLY"
# Sprint 2 NO-GPS-EQUAL (Adrian 2026-06-29): „duch przerzutu nie ma ripować zleceń od
# kuriera który ma dobrą trasę". Ramię RATUNEK odpalało na samym `a_cand is None` (holder
# wypadł z hipotetycznej puli re-pickupu) — a to NIE dowód spóźnienia: kurier BEZ GPS /
# pre_shift / już-jadący w GRAFIKU wypada z re-assess (np. już odebrał), choć dowozi dobrze.
# 59% quality_reassign było takim fałszywym ratunkiem (replay 29.06). Gdy flaga ON: a_cand=None
# liczy się jako spóźnienie TYLKO gdy holder NIEOBECNY w żywej flocie (realnie po zmianie/
# zniknął, cs_a is None); holder PRACUJĄCY bez zmierzonego R6>próg → brak dowodu → NIE ratujemy.
# Default OFF=legacy. Env (drop-in, oneshot timer czyta świeżo co tick).
RESCUE_REQUIRE_ABSENT_FLAG = "ENABLE_REASSIGN_RESCUE_REQUIRE_HOLDER_ABSENT"

# Pola czytane przez assess_order (zweryfikowane dispatch_pipeline.py:2881-3055).
_EVENT_FIELDS = (
    "order_id", "restaurant", "delivery_address", "pickup_coords", "delivery_coords",
    "czas_kuriera_warsaw", "pickup_at_warsaw", "pickup_at", "address_id", "order_type",
    "created_at_utc", "created_at", "delivery_city", "uwagi_pickup_parsed", "prep_minutes",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _state_to_order_event(rec: dict) -> dict:
    """Rekord orders_state → order_event (kopia pól, które czyta assess_order)."""
    return {k: rec.get(k) for k in _EVENT_FIELDS if rec.get(k) is not None}


def _active_assigned_orders(orders: dict) -> List[Tuple[str, str, dict]]:
    """Zlecenia NIEODEBRANE (status=assigned, NIE picked_up/delivered), z coords i realnym
    kurierem (nie Koordynator/None). Zwraca [(oid, cid, rekord)]."""
    out: List[Tuple[str, str, dict]] = []
    for oid, r in orders.items():
        if not isinstance(r, dict):
            continue
        if r.get("status") != "assigned":
            continue
        if r.get("picked_up_at"):
            continue   # JUŻ ODEBRANE (status laguje za realnym pickupem) — nie przerzucaj jedzenia z ręki
        cid = r.get("courier_id")
        scid = str(cid) if cid is not None else ""
        if scid in ("", "None", KOORDYNATOR_CID):
            continue
        if not r.get("pickup_coords") or not r.get("delivery_coords"):
            continue
        out.append((str(oid), scid, r))
    return out


def _bag_oid(b: dict) -> str:
    return str(b.get("order_id") or b.get("id") or "")


def _fleet_without_order(fleet: Dict[str, Any], oid: str, holder_cid: str) -> Dict[str, Any]:
    """Płytka kopia floty z O wyjętym z worka kuriera-posiadacza A (kontrfaktyk
    'gdyby O było teraz nieprzypisane'). NIE mutuje żywego snapshotu (kopiujemy
    tylko zmienianego kuriera + jego listę bag)."""
    out = dict(fleet)
    cs = out.get(holder_cid)
    if cs is None:
        return out
    bag = list(cs.bag or [])
    new_bag = [b for b in bag if _bag_oid(b) != oid]
    if len(new_bag) != len(bag):
        cs2 = _copy.copy(cs)
        cs2.bag = new_bag
        out[holder_cid] = cs2
    return out


_ALIAS_CACHE: Dict[str, str] = {}
_ALIAS_MTIME: float = 0.0


def _alias_map() -> Dict[str, str]:
    """{str(cid): nazwa} z kurier_ids.json (plik trzyma {nazwa: cid}). Cache po mtime,
    fail-soft (gdy plik znika → ostatni cache)."""
    global _ALIAS_CACHE, _ALIAS_MTIME
    try:
        st = os.stat(KURIER_IDS_PATH)
    except OSError:
        return _ALIAS_CACHE
    if st.st_mtime != _ALIAS_MTIME or not _ALIAS_CACHE:
        try:
            with open(KURIER_IDS_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            _ALIAS_CACHE = {str(cid): str(name) for name, cid in raw.items()}
            _ALIAS_MTIME = st.st_mtime
        except (OSError, ValueError) as e:
            _log.warning(f"alias_map load fail: {e}")
    return _ALIAS_CACHE


def _alias(cid: str, name_hint: Optional[str] = None) -> str:
    """Nazwa kuriera: najpierw nazwa z silnika (Candidate.name), potem kurier_ids,
    na końcu fallback #cid (nigdy gołe cid bez kontekstu)."""
    if name_hint:
        return str(name_hint)
    return _alias_map().get(str(cid)) or f"#{cid}"


def _cand_km(c: Any) -> Optional[float]:
    """km_to_pickup z metryk kandydata (lub None)."""
    if c is None:
        return None
    m = getattr(c, "metrics", None) or {}
    v = m.get("km_to_pickup")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _quality_on() -> bool:
    return os.environ.get(QUALITY_FLAG, "0") == "1"


def _usable_pos(ps) -> bool:
    """Pozycja wiarygodna do geometrii: GPS lub checkpoint/last-known (prefiks last_*) lub
    store/interp. Fikcja (none/pin/pre_shift/no_gps) = niewiarygodna. PREFIKS (sufiksy realne:
    last_picked_up_pickup, last_assigned_pickup, last_picked_up_interp, ...) — exact-match by zaniżał."""
    ps = str(ps or "")
    return ps == "gps" or ps.startswith("last_") or ps in {"store", "interp"}


def _plan_dt(c: Any, oid: str, attr: str):
    """datetime z planu kandydata: attr ∈ {predicted_delivered_at, pickup_at}. None gdy brak planu/klucza."""
    plan = getattr(c, "plan", None) if c is not None else None
    if plan is None:
        return None
    return (getattr(plan, attr, {}) or {}).get(oid)


def _minutes(a, b) -> Optional[float]:
    """(a−b) w minutach (float) gdy oba to datetime; None inaczej. Dodatni = a PÓŹNIEJ niż b."""
    if a is None or b is None:
        return None
    try:
        return (a - b).total_seconds() / 60.0
    except Exception:
        return None


def _quality_gate(a_cand: Any, best: Any, oid: str, a_pos, b_pos,
                  holder_cid: str, b_cid: str, b_bag=None, a_in_fleet: bool = False) -> dict:
    """Gradient gate JAKOŚCI (Krok 1, log-only). Duch-przerzut TYLKO gdy „na pewno lepiej":
      • RAMIĘ 1 (ratunek): obecny dowiezie po czasie (R6>próg) LUB jest infeasible, a nowy NA CZAS;
      • RAMIĘ 2 (oszczędność): obecny na czas, nowy dowiezie ≥ BIG_SAVE_MIN min wcześniej.
    Wymóg: B≠A oraz pozycje A i B usable (NIE fikcja). „kto kiedy dowiezie" = predicted_delivered_at;
    R6 per-zlecenie = predicted_delivered_at − pickup_at. Zwraca dict pól quality_*."""
    big_save = float(os.environ.get(QUALITY_BIG_SAVE_KEY, DEFAULT_BIG_SAVE_MIN))
    r6_late = float(os.environ.get(QUALITY_R6_LATE_KEY, DEFAULT_R6_LATE_MIN))
    bundling_only = os.environ.get(OSZCZ_BUNDLING_ONLY_FLAG, "0") == "1"
    require_absent = os.environ.get(RESCUE_REQUIRE_ABSENT_FLAG, "0") == "1"
    a_pred = _plan_dt(a_cand, oid, "predicted_delivered_at")
    a_pick = _plan_dt(a_cand, oid, "pickup_at")
    b_pred = _plan_dt(best, oid, "predicted_delivered_at")
    b_pick = _plan_dt(best, oid, "pickup_at")
    a_bag_time = _minutes(a_pred, a_pick)
    b_bag_time = _minutes(b_pred, b_pick)
    # obecny spóźniony: zmierzony R6 dla O ponad próg LUB infeasible.
    a_measured_late = (a_bag_time is not None and a_bag_time > r6_late)
    rescue_suppressed_working = False
    if require_absent:
        # Sprint 2 NO-GPS-EQUAL: a_cand=None liczy się jako spóźnienie TYLKO gdy holder
        # NIEOBECNY w żywej flocie (po zmianie/zniknął). Holder PRACUJĄCY (w grafiku, bez
        # GPS/pre_shift/już jedzie) bez zmierzonego R6>próg = brak dowodu → NIE ratujemy.
        a_genuinely_absent = (a_cand is None) and (not a_in_fleet)
        a_late = a_genuinely_absent or a_measured_late
        rescue_suppressed_working = (a_cand is None) and a_in_fleet and not a_measured_late
    else:
        # legacy: brak feasible A (infeasible) LUB R6 ponad próg
        a_late = (a_cand is None) or a_measured_late
    b_late = (b_bag_time is not None and b_bag_time > r6_late)
    save_min = _minutes(a_pred, b_pred)   # dodatni = nowy dowiezie wcześniej
    pos_ok = _usable_pos(a_pos) and _usable_pos(b_pos)
    quality = False
    reason = "obecny zdąży na czas i nikt istotnie nie szybszy — bez przerzutu"
    if b_cid and b_cid != holder_cid and pos_ok:
        if a_late and not b_late:
            reason = "ratunek: obecny dowiezie po czasie (R6>%.0f), nowy na czas" % r6_late
            quality = True
        elif (not a_late) and save_min is not None and save_min >= big_save:
            b_busy = isinstance(b_bag, int) and b_bag > 0
            if bundling_only and not b_busy:
                # B = WOLNY → NIE pal rezerwy pod samą oszczędność (reserve-aware). Wolny zostaje na napływ.
                reason = ("oszczędność ~%.0f min, ale B WOLNY → trzymamy w rezerwie (tylko bundling)" % save_min)
            else:
                reason = (("oszczędność: nowy (po drodze) dowiezie ~%.0f min wcześniej" % save_min)
                          if b_busy else ("oszczędność: nowy dowiezie ~%.0f min wcześniej" % save_min))
                quality = True
    if rescue_suppressed_working and not quality:
        reason = ("obecny pracuje (w grafiku, poz. bez GPS/pre_shift/już jedzie) — "
                  "brak dowodu spóźnienia (R6 niezmierzony) → NIE przerzucamy [Sprint2 no-GPS-equal]")
    return {
        "quality_reassign": quality,
        "quality_reason": reason,
        "quality_pos_ok": pos_ok,
        "quality_a_in_fleet": bool(a_in_fleet),
        "quality_rescue_suppressed_working": bool(rescue_suppressed_working),
        "a_pred_deliver": a_pred.isoformat() if a_pred is not None else None,
        "b_pred_deliver": b_pred.isoformat() if b_pred is not None else None,
        "a_bag_time_min": round(a_bag_time, 1) if a_bag_time is not None else None,
        "b_bag_time_min": round(b_bag_time, 1) if b_bag_time is not None else None,
        "a_late": bool(a_late),
        "b_late": bool(b_late),
        "save_min": round(save_min, 1) if save_min is not None else None,
        "quality_big_save_min": big_save,
    }


def _why(would: bool, a_in_pool: bool, a_km: Optional[float], b_km: Optional[float],
         a_bag: Optional[int], b_bag: Optional[int], a_real: bool, b_real: bool,
         delta: Optional[float]) -> str:
    """JEDNO zdanie PL: dlaczego Ziomek wskazałby innego kuriera. Wyłącznie z faktów
    porównania obecny↔najlepszy (bliskość odbioru / wielkość worka / realny GPS /
    margines pkt) — nic nie zgaduje."""
    if not would:
        return "obecny kurier nadal najlepszy — Ziomek by nie przerzucał"
    bits: List[str] = []
    if not a_in_pool:
        bits.append("obecny kurier wypadł z puli wykonalnych (niedostępny/po zmianie)")
    else:
        if a_km is not None and b_km is not None and (a_km - b_km) >= 0.5:
            bits.append(f"bliżej odbioru ({b_km:.1f} vs {a_km:.1f} km)")
        if a_bag is not None and b_bag is not None and b_bag < a_bag:
            bits.append(f"luźniejszy worek ({b_bag} vs {a_bag} zlec.)")
        if b_real and not a_real:
            bits.append("ma realny GPS (pozycja obecnego zgadywana)")
    if not bits:
        bits.append("wyższe dopasowanie do trasy/floty")
    s = "; ".join(bits[:2])
    if delta is not None:
        s += f" (Δ{delta:+.0f} pkt)"
    return s


def evaluate_order(rec: dict, holder_cid: str, fleet: Dict[str, Any],
                   now: Optional[datetime] = None, margin: float = DEFAULT_MARGIN) -> Optional[dict]:
    """Dla nieodebranego O (u A): policz PRAWDZIWYM assess_order nad flotą z O wyjętym
    z worka A. Zwraca rekord shadow (would_reassign True/False) lub None gdy nieoceniane
    (brak oid / wyjątek silnika / brak jakiegokolwiek feasible kandydata)."""
    now = now or _now_utc()
    oid = str(rec.get("order_id") or "")
    if not oid:
        return None
    order_event = _state_to_order_event(rec)
    fleet_cf = _fleet_without_order(fleet, oid, holder_cid)
    try:
        res = _decide(WorldState(fleet_snapshot=fleet_cf, now=now), order_event, _bypass_early_bird=True)  # K09
    except Exception as e:
        _log.warning(f"assess_order fail oid={oid}: {type(e).__name__}: {e}")
        return None

    best = getattr(res, "best", None)
    cands = getattr(res, "candidates", None) or []
    a_cand = next((c for c in cands if str(getattr(c, "courier_id", "")) == holder_cid), None)
    a_score = float(getattr(a_cand, "score", 0.0) or 0.0) if a_cand is not None else None

    if best is None:
        return None  # brak feasible kandydata = sytuacja KOORD-owa, NIE przerzut (osobny temat)

    b_cid = str(getattr(best, "courier_id", ""))
    b_score = float(getattr(best, "score", 0.0) or 0.0)
    delta = (b_score - a_score) if a_score is not None else None
    would = (b_cid != holder_cid) and (a_score is None or (b_score - a_score) >= margin)

    cs_b = fleet_cf.get(b_cid)
    cs_a = fleet.get(holder_cid)
    a_bag = len(cs_a.bag) if cs_a is not None and cs_a.bag is not None else None
    b_bag = len(cs_b.bag) if cs_b is not None and cs_b.bag is not None else None
    a_real = (getattr(cs_a, "pos_source", None) not in _SYNTH_POS) if cs_a is not None else False
    b_real = (getattr(cs_b, "pos_source", None) not in _SYNTH_POS) if cs_b is not None else False
    a_km = _cand_km(a_cand)
    b_km = _cand_km(best)
    a_name = getattr(a_cand, "name", None) if a_cand is not None else None
    b_name = getattr(best, "name", None)
    reason = _why(bool(would), a_cand is not None, a_km, b_km, a_bag, b_bag, a_real, b_real, delta)
    rec_out = {
        "ts": now.isoformat(),
        "order_id": oid,
        "restaurant": rec.get("restaurant"),
        "holder_cid": holder_cid,
        "best_cid": b_cid,
        "a_name": a_name,
        "b_name": b_name,
        "a_km": round(a_km, 2) if a_km is not None else None,
        "b_km": round(b_km, 2) if b_km is not None else None,
        "reason": reason,
        "would_reassign": bool(would),
        "a_in_pool": a_cand is not None,
        "a_score": round(a_score, 2) if a_score is not None else None,
        "b_score": round(b_score, 2),
        "delta_score": round(delta, 2) if delta is not None else None,
        "verdict": getattr(res, "verdict", None),
        "pool_feasible": int(getattr(res, "pool_feasible_count", 0) or 0),
        "a_pos_source": getattr(cs_a, "pos_source", None) if cs_a is not None else None,
        "a_bag_size": a_bag,
        "b_pos_source": getattr(cs_b, "pos_source", None) if cs_b is not None else None,
        "b_bag_size": b_bag,
        "b_tier": getattr(cs_b, "tier_bag", None) if cs_b is not None else None,
        "pickup_coords": rec.get("pickup_coords"),
        "delivery_coords": rec.get("delivery_coords"),
    }
    # KROK 1 gate JAKOŚCI — tylko gdy flaga ON (inaczej rekord bajt-identyczny ze starym).
    if _quality_on():
        rec_out.update(_quality_gate(
            a_cand, best, oid,
            getattr(cs_a, "pos_source", None) if cs_a is not None else None,
            getattr(cs_b, "pos_source", None) if cs_b is not None else None,
            holder_cid, b_cid, b_bag=b_bag,
            a_in_fleet=(cs_a is not None)))
    return rec_out


def _append_jsonl(rows: List[dict], path: str = OUT_JSONL) -> None:
    """Append-only log (jak shadow_decisions.jsonl). flush+fsync dla trwałości."""
    if not rows:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        _log.warning(f"append_jsonl fail: {e}")


def _load_notified() -> dict:
    try:
        with open(NOTIFIED_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save_notified(d: dict) -> None:
    try:
        fd, t = tempfile.mkstemp(dir=os.path.dirname(NOTIFIED_PATH))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(t, NOTIFIED_PATH)
    except Exception as e:
        _log.warning(f"save_notified fail: {e}")


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    """ISO ts → aware datetime (lub None). Tolerancyjny na format/None."""
    try:
        return datetime.fromisoformat(s) if s else None
    except (ValueError, TypeError):
        return None


def _pos_trusted(a_pos: Any, b_pos: Any) -> bool:
    """True gdy OBAJ — obecny kurier A i proponowany B — mają realną pozycję
    (GPS lub ostatnia znana lokalizacja). pin/pre_shift/none = zgadnięta → False."""
    return (str(a_pos or "") in _REAL_POS) and (str(b_pos or "") in _REAL_POS)


def _notify_eligible(r: dict, notified: dict, now: datetime,
                     cooldown_min: float, trusted_only: bool) -> bool:
    """Czy rekord ma iść na POWIADOMIENIE (zapis do jsonl jest osobny i dostaje
    WSZYSTKO). Bramki: (1) would_reassign; (2) trusted_only → A i B pewna pozycja
    (tnie ~90% szumu na zgadniętych pozycjach); (3) cooldown → nie powtarzaj tego
    samego zlecenia częściej niż co cooldown_min (dławi churn best_cid co 3 min).
    Stary format notified (oid→cid, bez ts) = cooldown nie blokuje (kompat wsteczna)."""
    if not r.get("would_reassign"):
        return False
    if trusted_only and not _pos_trusted(r.get("a_pos_source"), r.get("b_pos_source")):
        return False
    if cooldown_min > 0:
        prev = notified.get(str(r.get("order_id") or ""))
        last_ts = _parse_iso(prev.get("ts")) if isinstance(prev, dict) else None
        if last_ts is not None and (now - last_ts).total_seconds() < cooldown_min * 60.0:
            return False
    return True


def _notify_telegram(new_rows: list) -> int:
    """JEDEN komunikat SHADOW per sweep na grupę ziomka (send_admin_alert →
    chat_id=admin_id=-5149910559). Wyraźnie NIE-do-wykonania — to grupa operacyjna."""
    if not new_rows:
        return 0
    lines = ["🔁 SHADOW przerzutów (PODGLĄD Ziomka — NIE wykonane, NIE przydzielaj ręcznie):"]
    for r in new_rows[:TG_CAP]:
        real = (r.get("a_pos_source") not in _SYNTH_POS) and (r.get("b_pos_source") not in _SYNTH_POS)
        a_nm = _alias(r["holder_cid"], r.get("a_name"))
        b_nm = _alias(r["best_cid"], r.get("b_name"))
        rest = r.get("restaurant") or "?"
        reason = r.get("reason") or "wyższe dopasowanie do trasy/floty"
        lines.append(f"• #{r['order_id']} {rest}: {a_nm} → {b_nm}")
        lines.append(f"   ↳ {reason} · {'GPS' if real else 'poz.~zgadnięta'}")
    extra = len(new_rows) - TG_CAP
    if extra > 0:
        lines.append(f"…+{extra} więcej w tym ticku")
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert("\n".join(lines), source="reassignment_fwd_live")
        return min(len(new_rows), TG_CAP)
    except Exception as e:  # noqa: BLE001 — notyfikacja nie może wywalić sweepu
        _log.warning(f"reassign tg notify fail: {e}")
        return 0


def run_once(now: Optional[datetime] = None, max_orders: Optional[int] = None,
             margin: Optional[float] = None) -> dict:
    """Jeden sweep: czyta żywy stan, buduje dispatchable flotę, ocenia aktywne zlecenia,
    dopisuje do jsonl. No-op (natychmiastowy) gdy flaga OFF."""
    if not C.flag(FLAG, False):
        return {"skipped": "flag_off"}
    now = now or _now_utc()
    _t0 = time.monotonic()
    flags = C.load_flags()
    if margin is None:
        margin = float(flags.get(MARGIN_KEY, DEFAULT_MARGIN))
    if max_orders is None:
        max_orders = int(flags.get(MAX_ORDERS_KEY, DEFAULT_MAX_ORDERS))

    try:
        with open(ORDERS_STATE, encoding="utf-8") as f:
            d = json.load(f)
        orders = d.get("orders", d) if isinstance(d, dict) else d
    except Exception as e:
        _log.warning(f"orders_state load fail: {e}")
        return {"error": "state_load"}

    active = _active_assigned_orders(orders)
    # priorytet: najstarsze (najpilniejsze) najpierw, potem cap
    active.sort(key=lambda t: t[2].get("assigned_at") or t[2].get("created_at_utc") or "")
    active = active[:max_orders]
    if not active:
        return {"active": 0, "evaluated": 0, "would_reassign": 0,
                "duration_s": round(time.monotonic() - _t0, 2), "ts": now.isoformat()}

    fleet_list = CR.dispatchable_fleet()   # ⚠ enriched (shift_end) — NIE build_fleet_snapshot
    fleet = {str(cs.courier_id): cs for cs in fleet_list}

    rows: List[dict] = []
    n_would = 0
    for oid, cid, rec in active:
        r = evaluate_order(rec, cid, fleet, now=now, margin=margin)
        if r is None:
            continue
        rows.append(r)
        if r["would_reassign"]:
            n_would += 1

    _append_jsonl(rows)

    # Live podgląd na grupę ziomka (flag OFF default): bramki = pewna pozycja (A i B
    # realny GPS/last-known) + cooldown per zlecenie (dławi churn best_cid co 3 min).
    # ⚠ filtr działa TYLKO na notify — _append_jsonl wyżej dostał WSZYSTKIE rows (eval pełny).
    tg_sent = 0
    tg_trusted_only = None
    tg_cooldown_min = None
    if C.flag(FLAG_TG, False):
        tg_trusted_only = bool(C.flag(NOTIFY_TRUSTED_ONLY_FLAG, True))
        tg_cooldown_min = float(flags.get(NOTIFY_COOLDOWN_KEY, DEFAULT_NOTIFY_COOLDOWN_MIN))
        notified = _load_notified()
        new_rows = [r for r in rows
                    if _notify_eligible(r, notified, now, tg_cooldown_min, tg_trusted_only)]
        if new_rows:
            tg_sent = _notify_telegram(new_rows)
            for r in new_rows:  # stempel czasu tylko dla FAKTYCZNIE powiadomionych
                notified[str(r["order_id"])] = {"best": str(r["best_cid"]), "ts": now.isoformat()}
        active_oids = {str(r["order_id"]) for r in rows}
        merged = {oid: v for oid, v in notified.items() if oid in active_oids}  # auto-clean
        _save_notified(merged)

    summary = {
        "active": len(active),
        "evaluated": len(rows),
        "would_reassign": n_would,
        "tg_sent": tg_sent,
        "tg_trusted_only": tg_trusted_only,
        "tg_cooldown_min": tg_cooldown_min,
        "margin": margin,
        "duration_s": round(time.monotonic() - _t0, 2),
        "ts": now.isoformat(),
    }
    _log.info(f"REASSIGN_FWD sweep {summary}")
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_once()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
