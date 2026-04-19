"""telegram_approver - bot Telegram dla shadow proposals (Faza 1, D15).

Flow:
    shadow_decisions.jsonl  ─┐
                             ├→ [PROPOSE] → Telegram sendMessage(inline_kbd)
                             │
    long-poll getUpdates ←───┘
         └→ callback_query → gastro_assign (subprocess)
    watchdog → 5 min timeout → auto-KOORD

4 asyncio tasks:
    shadow_tailer    — ogon shadow_decisions.jsonl
    proposal_sender  — wysyłka inline-button propozycji
    updates_poller   — getUpdates long-polling
    watchdog         — 5-min timeout auto-KOORD

State:
    pending_proposals.json — atomic {order_id: {message_id, sent_at, expires_at, decision_record}}
    learning_log.jsonl     — append-only trail (TAK/NIE/INNY/KOORD/TIMEOUT)
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dispatch_v2.common import (
    ENABLE_TIMELINE_FORMAT,
    ENABLE_TRANSPARENCY_REASON,
    ENABLE_TRANSPARENCY_ROUTE,
    WARSAW,
    load_config,
    now_iso,
    parse_panel_timestamp,
    setup_logger,
)


POLL_SHADOW_SEC = 3
PROPOSAL_TIMEOUT_SEC = 300  # 5 min → auto-KOORD
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_ENV_PATH = "/root/.openclaw/workspace/.secrets/telegram.env"
PENDING_PATH = "/root/.openclaw/workspace/dispatch_state/pending_proposals.json"
LEARNING_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"
GASTRO_ASSIGN_PATH = "/root/.openclaw/workspace/scripts/gastro_assign.py"

_log = setup_logger(
    "telegram_approver",
    "/root/.openclaw/workspace/scripts/logs/telegram_approver.log",
)
_shutdown = False


# ---- telegram env ----

def _load_env(path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        _log.error(f"telegram env not found: {path}")
    return env


# ---- telegram HTTP (urllib — no external deps) ----

def tg_request(token: str, method: str, payload: Optional[dict] = None, timeout: int = 35) -> dict:
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---- formatting ----

COURIER_NAMES_PATH = "/root/.openclaw/workspace/dispatch_state/courier_names.json"
_courier_names_cache: Optional[Dict[str, str]] = None


def _load_courier_names() -> Dict[str, str]:
    """Lazy load + cache courier_names.json (F1.2).

    Shadow pipeline już populuje cs.name w dispatch_pipeline (z courier_resolver),
    ale tutaj mamy fallback gdy decision.best.name=None/brak.
    """
    global _courier_names_cache
    if _courier_names_cache is not None:
        return _courier_names_cache
    try:
        with open(COURIER_NAMES_PATH) as f:
            _courier_names_cache = json.load(f)
    except Exception as e:
        _log.warning(f"courier_names load fail: {e}")
        _courier_names_cache = {}
    return _courier_names_cache


def name_lookup(courier_id: Optional[str], existing_name: Optional[str]) -> str:
    if existing_name:
        return existing_name
    if courier_id:
        names = _load_courier_names()
        cached = names.get(str(courier_id))
        if cached:
            return cached
        return f"K{courier_id}"
    return "?"


def _to_warsaw_hhmm(dt_utc: datetime) -> str:
    return dt_utc.astimezone(WARSAW).strftime("%H:%M")


def _pickup_ready_warsaw(decision: dict, now_utc: datetime) -> Tuple[Optional[str], Optional[float]]:
    """Z pickup_ready_at → (HH:MM Warsaw, minuty od now). None gdy brak."""
    iso = decision.get("pickup_ready_at")
    if not iso:
        return None, None
    try:
        ready = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None, None
    if ready.tzinfo is None:
        ready = ready.replace(tzinfo=timezone.utc)
    delta_min = (ready - now_utc).total_seconds() / 60.0
    return _to_warsaw_hhmm(ready), delta_min


def _candidate_line(c: dict, now_utc: datetime, prep_remaining_min: float) -> str:
    """Linia top-N. 3 warianty per pos_source:
    - normalny:  '{name} ({score}) — {km} km, ETA {hhmm} → deklarujemy {hhmm}'
    - no_gps:    '{name} ({score}) — brak GPS, czas: 15 min → deklarujemy {hhmm}'
    - pre_shift: '{name} ({score}) — start {hhmm} → deklarujemy {hhmm}'
    """
    name = name_lookup(c.get("courier_id"), c.get("name"))
    score = c.get("score", 0)
    km = c.get("km_to_pickup")
    # F1.9b fix: ETA display = plan-based (eta_pickup_hhmm). Uwzględnia
    # dostarczenie aktualnego baga PRZED nowym pickupem. drive_hhmm (pure drive
    # z pos kuriera) był mylący dla bundling case — np. Bartek stojący przy
    # aktualnej restauracji pokazywał ETA "za 0.1 min" zamiast "po bagu".
    eta = c.get("eta_pickup_hhmm") or c.get("eta_drive_hhmm")
    travel_min = c.get("travel_min")
    pos_source = c.get("pos_source")
    no_gps = pos_source == "no_gps"
    pre_shift = pos_source == "pre_shift"

    # Czas deklarowany: max(eta, prep) → round_up_to_5min → HH:MM Warsaw
    eta_t = round_up_to_5min(travel_min)
    prep_t = round_up_to_5min(prep_remaining_min)
    dekl_min = max(eta_t, prep_t)
    dekl_hhmm = _to_warsaw_hhmm(now_utc + timedelta(minutes=dekl_min))

    bits = [f"{name} ({score:.2f})"]
    if pre_shift:
        # eta_pickup_hhmm == start zmiany (pipeline ustawił eta = now + shift_start_min)
        bits.append(f"start {eta}" if eta else "pre-shift")
    elif no_gps:
        tm_int = int(round(travel_min)) if travel_min is not None else None
        bits.append(f"brak GPS, czas: {tm_int} min" if tm_int is not None else "brak GPS")
    else:
        sub = []
        if km is not None:
            sub.append(f"{km:.1f} km")
        if eta:
            sub.append(f"ETA {eta}")
        if sub:
            bits.append(", ".join(sub))
    head = " — ".join(bits)
    line = f"{head} → deklarujemy {dekl_hhmm}"
    tags = []
    # Availability tag (free/wkrótce wolny)
    free_at = c.get("free_at_min")
    if free_at is not None:
        if free_at <= 0:
            tags.append("🟢 wolny")
        elif free_at < 15:
            tags.append(f"🟡 za {int(round(free_at))} min")
        elif free_at < 30:
            tags.append(f"🟠 za {int(round(free_at))} min")
    # Bundle tags
    if c.get("bundle_level1"):
        tags.append(f"🔗 same: {c['bundle_level1']}")
    elif c.get("bundle_level2"):
        d2 = c.get("bundle_level2_dist")
        if d2 is not None:
            tags.append(f"🔗 po odbiorze z {c['bundle_level2']} → +{d2:.2f}km")
        else:
            tags.append(f"🔗 po odbiorze z {c['bundle_level2']}")
    if c.get("bundle_level3"):
        d3 = c.get("bundle_level3_dev")
        d3_str = f" ({d3:.1f}km)" if d3 is not None else ""
        tags.append(f"🔗 po drodze{d3_str}")
    if tags:
        line += "  " + "  ".join(tags)
    return line


def _reason_line(c: dict, all_candidates: list) -> str:
    """Transparency OPCJA A: natural-language wyjaśnienie CZEMU ten kurier.

    Zwraca pusty string jeśli brak meaningful info lub flaga wyłączona.
    Format: '   💡 najbliższy + fala z Eljot + wolny za 3min'
    """
    if not ENABLE_TRANSPARENCY_REASON or not c:
        return ""
    reasons: list = []
    km = c.get("km_to_pickup")
    others_km = [
        x.get("km_to_pickup") for x in all_candidates
        if x and x is not c and x.get("km_to_pickup") is not None
    ]
    if km is not None and others_km and km <= min(others_km):
        reasons.append("najbliższy")
    if c.get("bundle_level1"):
        reasons.append(f"fala z {c['bundle_level1']}")
    elif c.get("bundle_level2"):
        reasons.append(f"fala z {c['bundle_level2']}")
    elif c.get("bundle_level3"):
        reasons.append("po drodze")
    free = c.get("free_at_min")
    if free is not None:
        if free <= 0:
            reasons.append("wolny")
        elif free < 15:
            reasons.append(f"wolny za {int(round(free))} min")
    if not reasons:
        return ""
    return "   💡 " + " + ".join(reasons)


def _iso_to_warsaw_hhmm(iso_utc):
    """V3.17: ISO UTC → Warsaw HH:MM, None on failure (used by timeline formatter)."""
    if not iso_utc:
        return None
    try:
        dt = datetime.fromisoformat(iso_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(WARSAW).strftime("%H:%M")
    except Exception:
        return None


def _build_timeline_section(decision: dict, best: dict) -> str:
    """V3.17: per-stop chronological timeline z plan.pickup_at + predicted_delivered_at.

    Wymaga etapu A (shadow_dispatcher propagacja). Format:
        📦 N ordery w bagu → trasa z nowym zleceniem:
        HH:MM 🍕 pickup {restaurant}
        HH:MM 📍 drop {delivery_address}
        ...
        HH:MM 👉 pickup [NOWY] {restaurant}
        HH:MM 👉 drop [NOWY] {delivery_address}

    Zwraca "" gdy: flag off, brak plan, plan.pickup_at+predicted_delivered_at oba puste,
    sequence ≤ 1 (solo). Fallback: caller używa _route_section() starego.
    """
    plan = (best or {}).get("plan") or {}
    pickup_at = plan.get("pickup_at") or {}
    delivered_at = plan.get("predicted_delivered_at") or {}
    sequence = plan.get("sequence") or []
    if not pickup_at and not delivered_at:
        return ""
    if len(sequence) <= 1 and len(delivered_at) <= 1:
        return ""

    cur_oid = str(decision.get("order_id") or "")
    mapping = {
        cur_oid: (decision.get("restaurant"), decision.get("delivery_address")),
    }
    for b in (best.get("bag_context") or []):
        boid = str(b.get("order_id") or "")
        if boid:
            mapping[boid] = (b.get("restaurant"), b.get("delivery_address"))

    events = []
    for oid, iso in pickup_at.items():
        hhmm = _iso_to_warsaw_hhmm(iso)
        if hhmm is None:
            continue
        events.append((iso, hhmm, "pickup", str(oid)))
    for oid, iso in delivered_at.items():
        hhmm = _iso_to_warsaw_hhmm(iso)
        if hhmm is None:
            continue
        events.append((iso, hhmm, "drop", str(oid)))
    if not events:
        return ""
    # Stable sort by ISO (lexicographic == chronological for same-TZ ISO 8601).
    # Tie-break: pickup before drop of same oid (rare; different legs differ).
    events.sort(key=lambda e: (e[0], 0 if e[2] == "pickup" else 1))

    lines = []
    n_bag = len([x for x in mapping.keys() if x and x != cur_oid])
    if n_bag > 0:
        header = f"📦 {n_bag} ordery w bagu → trasa z nowym zleceniem:"
    else:
        header = f"📦 trasa dla nowego zlecenia:"
    lines.append(header)
    for _iso, hhmm, etype, oid in events:
        rest, drop_addr = mapping.get(oid, (None, None))
        is_new = (oid == cur_oid)
        if etype == "pickup":
            emoji = "👉" if is_new else "🍕"
            name = rest or "?"
            prefix_tag = "[NOWY] " if is_new else ""
            lines.append(f"{hhmm} {emoji} pickup {prefix_tag}{name}")
        else:
            emoji = "👉" if is_new else "📍"
            addr = drop_addr or "?"
            prefix_tag = "[NOWY] " if is_new else ""
            lines.append(f"{hhmm} {emoji} drop {prefix_tag}{addr}")
    return "\n".join(lines)


def _route_section(decision: dict, best: dict) -> str:
    """Transparency OPCJA A: route section — pickupy then drops w kolejności plan.sequence.

    V3.17: gdy ENABLE_TIMELINE_FORMAT=True AND timeline data dostępne →
    delegate do `_build_timeline_section()`. Fallback do starego formatu
    (pickups|drops) gdy timeline zwraca "" (brak danych / solo order / flag off).

    Zwraca pusty string dla solo orderów (sequence ≤ 1), flagi wyłączonej, lub braku mapping.
    Format legacy (flag off / fallback):
        📦 N ordery w bagu:
        🗺️ Kolejność:
           🍕 {pickup1} → {pickup2} → ...
           📍 {drop1} → {drop2} → ...
    """
    if not ENABLE_TRANSPARENCY_ROUTE or not best:
        return ""
    if ENABLE_TIMELINE_FORMAT:
        timeline = _build_timeline_section(decision, best)
        if timeline:
            return timeline
    plan = best.get("plan") or {}
    sequence = plan.get("sequence") or []
    if len(sequence) <= 1:
        return ""
    cur_oid = str(decision.get("order_id") or "")
    mapping: dict = {
        cur_oid: (decision.get("restaurant"), decision.get("delivery_address")),
    }
    for b in (best.get("bag_context") or []):
        boid = str(b.get("order_id") or "")
        if boid:
            mapping[boid] = (b.get("restaurant"), b.get("delivery_address"))
    pickups: list = []
    drops: list = []
    for oid in sequence:
        soid = str(oid)
        rest, drop = mapping.get(soid, (None, None))
        if rest and rest not in pickups:
            pickups.append(rest)
        if drop:
            drops.append(drop)
    if not pickups or not drops:
        return ""
    n = len(sequence)
    return (
        f"📦 {n} ordery w bagu:\n"
        f"🗺️ Kolejność:\n"
        f"   🍕 " + " → ".join(pickups) + "\n"
        f"   📍 " + " → ".join(drops)
    )


def format_proposal(decision: dict) -> str:
    """[PROPOZYCJA] z top3 + pickup_ready + czas deklarowany per kandydat."""
    oid = decision.get("order_id", "?")
    rest = decision.get("restaurant") or "?"
    delivery = decision.get("delivery_address") or "—"
    best = decision.get("best") or {}
    alts = decision.get("alternatives") or []
    best_effort = best.get("best_effort", False)

    now_utc = datetime.now(timezone.utc)
    pickup_hhmm, pickup_in_min = _pickup_ready_warsaw(decision, now_utc)
    prep_remaining = max(0.0, pickup_in_min) if pickup_in_min is not None else 0.0

    header_tag = "[PROPOZYCJA best_effort]" if best_effort else "[PROPOZYCJA]"
    banner = "⚠️ " if best_effort else ""

    lines = [
        f"{header_tag} #{oid}",
        f"{rest} → {delivery}",
    ]
    if pickup_hhmm is not None:
        if pickup_in_min is not None and pickup_in_min >= 0:
            lines.append(f"🕐 Odbiór: {pickup_hhmm} (za {int(round(pickup_in_min))} min)")
        else:
            lines.append(f"🕐 Odbiór: {pickup_hhmm} (gotowe)")
    lines.append("")

    medals = ["🎯", "🥈", "🥉"]
    top3 = [best] + list(alts[:2])
    top3_nonempty = [c for c in top3 if c]
    for i, c in enumerate(top3):
        if not c:
            continue
        marker = medals[i] if i < len(medals) else "•"
        prefix = f"{marker} {banner}" if i == 0 else f"{marker} "
        lines.append(prefix + _candidate_line(c, now_utc, prep_remaining))
        reason = _reason_line(c, top3_nonempty)
        if reason:
            lines.append(reason)

    route = _route_section(decision, best)
    if route:
        lines.append("")
        lines.append(route)

    lines.append("")
    lines.append(f"✓ {decision.get('reason','')}")
    lines.append("")
    lines.append("TAK / NIE / INNY / KOORD")
    return "\n".join(lines)


def build_keyboard(order_id: str, candidates: Optional[list] = None) -> dict:
    """Inline keyboard z przyciskami per-kandydat (F2.4).

    Rząd 1: do 3 przycisków ✅ {Imię} {tmin}min — callback ASSIGN:{oid}:{cid}:{tmin}.
        tmin = round(travel_min) + 2, clamp [5, 60] (zgodnie z gastro_assign).
        Pusty jeśli candidates is None/[] lub brak valid courier_id.
    Rząd 2: INNY + KOORD. NIE usunięte — brak decyzji = auto-timeout (5 min).
    """
    rows = []
    row1 = []
    for c in (candidates or [])[:3]:
        if not c:
            continue
        cid = str(c.get("courier_id") or "")
        if not cid:
            continue
        name = name_lookup(cid, c.get("name"))
        tm_raw = c.get("travel_min") or 0.0
        try:
            tm_raw = float(tm_raw)
        except (TypeError, ValueError):
            tm_raw = 0.0
        time_min = max(5, min(60, int(round(tm_raw)) + 2))
        row1.append({
            "text": f"✅ {name} {time_min}min",
            "callback_data": f"ASSIGN:{order_id}:{cid}:{time_min}",
        })
    if row1:
        rows.append(row1)
    rows.append([
        {"text": "🔄 INNY", "callback_data": f"INNY:{order_id}"},
        {"text": "👤 KOORD", "callback_data": f"KOORD:{order_id}"},
    ])
    return {"inline_keyboard": rows}


# ---- pending state ----

def load_pending(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        _log.warning(f"load_pending fail: {e}")
        return {}


def save_pending(path: str, pending: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(pending, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_learning(path: str, record: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---- gastro_assign subprocess ----

def round_up_to_5min(eta_minutes: Optional[float]) -> int:
    """ETA kuriera (min from now) → time_param dla gastro_assign.
    Zaokrąglenie w górę do 5, min 5, max 60. None → 5."""
    import math
    if eta_minutes is None:
        return 5
    try:
        m = float(eta_minutes)
    except (TypeError, ValueError):
        return 5
    t = int(math.ceil(m / 5.0) * 5)
    if t < 5:
        t = 5
    if t > 60:
        t = 60
    return t


def _prep_minutes_remaining(decision: dict) -> Optional[float]:
    """Z decision record → minuty od teraz do pickup_ready_at (gotowość jedzenia)."""
    iso = decision.get("pickup_ready_at")
    if not iso:
        return None
    try:
        ready = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None
    delta = (ready - datetime.now(timezone.utc)).total_seconds() / 60.0
    return max(0.0, delta)


def _parse_courier_time(
    text: str,
    allow_name_only: bool = False,
    known_names: Optional[list] = None,
) -> Optional[tuple]:
    """Parse free-text "Imię [HH:MM | Xmin | N]" → (courier_name, time_min_or_None).

    time_min_or_None == None sygnalizuje: brak jawnego czasu w tekście.
    Caller liczy default (np. compute_assign_time(decision) z pickup_ready_at+ETA).

    Dopasowanie courier_name:
      1. known_names prefix-match (case-insensitive, normalizacja trailing punct)
         — zwraca kanoniczną nazwę ("Bartek O" → "Bartek O.", "Grzegorz W" → "Grzegorz W")
      2. fallback first-word (gdy brak known_names lub brak matcha)

    Zwraca None gdy:
      - tekst pusty / brak imienia
      - brak czasu AND brak matcha w known_names AND not allow_name_only
        (anty-false-positive dla "Dzień dobry" w free-text bez kandydatów)
      - trailing/xmin time poza [1, 60]
    """
    import re as _re
    if not text or not text.strip():
        return None
    s = text.strip()
    time_min = None
    courier_text = s
    time_source = None  # "hhmm" | "xmin" | "trailing" | None

    # HH:MM → minuty do tej godziny (Warsaw). Eksplicit → NIE podlega guard [1,60]
    # (np. "Bartek 14:30" przy 12:00 = 150 min — legit).
    t_match = _re.search(r"(\d{1,2}):(\d{2})", courier_text)
    if t_match:
        now_w = datetime.now(WARSAW)
        h, m = int(t_match.group(1)), int(t_match.group(2))
        target = now_w.replace(hour=h, minute=m, second=0, microsecond=0)
        time_min = max(1, int((target - now_w).total_seconds() / 60))
        courier_text = courier_text[:t_match.start()].strip()
        time_source = "hhmm"

    # Xmin / X min — eksplicit "min" suffix
    if time_min is None:
        m_match = _re.search(r"(\d+)\s*min", courier_text, _re.IGNORECASE)
        if m_match:
            time_min = int(m_match.group(1))
            courier_text = courier_text[:m_match.start()].strip()
            time_source = "xmin"

    # Trailing number: "Gabriel 40" — ambiguous, wymaga guard
    if time_min is None:
        n_match = _re.search(r"\s+(\d+)$", courier_text)
        if n_match:
            time_min = int(n_match.group(1))
            courier_text = courier_text[:n_match.start()].strip()
            time_source = "trailing"

    # Guard anti-false-positive dla ambiguous time (trailing/xmin).
    # HH:MM explicit → bez ograniczeń (trust the human, np. "Bartek 14:30" przy 12:00 = 150).
    if time_source in ("trailing", "xmin") and (time_min < 1 or time_min > 60):
        return None

    courier_text = courier_text.strip()
    if not courier_text:
        return None

    # 1) known_names prefix-match. Normalizacja: lowercase + strip trailing . , ; :
    courier_name = None
    matched_known = False
    if known_names:
        def _norm(x: str) -> str:
            return x.strip().rstrip(".,;:").lower()
        text_norm = _norm(courier_text)
        # sort desc po długości normalized — "Grzegorz W" przed "Grzegorz", "Bartek O." przed "Bartek"
        cands = sorted(
            [n for n in known_names if n and n.strip()],
            key=lambda n: len(_norm(n)),
            reverse=True,
        )
        for c in cands:
            c_norm = _norm(c)
            if not c_norm:
                continue
            if text_norm == c_norm or text_norm.startswith(c_norm + " "):
                courier_name = c.strip()  # kanoniczna nazwa z listy
                matched_known = True
                break

    # 2) Fallback: pierwsze słowo (gdy brak known_names lub brak matcha)
    if not courier_name:
        first_word = courier_text.split()[0] if courier_text.split() else None
        if not first_word:
            return None
        courier_name = first_word

    # Jeśli brak czasu i ani allow_name_only ani matched_known → reject (anty-false-positive)
    if time_min is None and not (allow_name_only or matched_known):
        return None

    return (courier_name, time_min)


def _known_names_from_decision(dr: dict) -> list:
    """Zbierz imiona kandydatów z decision_record: best.name + alternatives[].name."""
    if not isinstance(dr, dict):
        return []
    names = []
    best = dr.get("best") or {}
    n = best.get("name") or best.get("courier_name")
    if n:
        names.append(n)
    for a in (dr.get("alternatives") or []):
        n = (a or {}).get("name") or (a or {}).get("courier_name")
        if n:
            names.append(n)
    return names


def compute_assign_time(decision: dict) -> int:
    """time_param = ceil(max(eta_kuriera, prep_jedzenia) / 5) * 5, clamp [5, 60].

    eta z best.travel_min (statyczne z propozycji), prep liczone świeżo
    z pickup_ready_at vs now. round(..., 4) tnie FP noise.
    """
    import math
    best = decision.get("best") or {}
    eta_min = best.get("travel_min") or 0.0
    try:
        eta_min = float(eta_min)
    except (TypeError, ValueError):
        eta_min = 0.0
    prep_min = _prep_minutes_remaining(decision) or 0.0
    needed_min = round(max(eta_min, prep_min), 4)
    if needed_min <= 0:
        return 5
    t = int(math.ceil(needed_min / 5.0) * 5)
    if t < 5:
        t = 5
    if t > 60:
        t = 60
    return t


def run_gastro_assign(
    order_id: str,
    kurier_name: Optional[str],
    time_minutes: int = 0,
    koordynator: bool = False,
) -> Tuple[bool, str]:
    cmd = ["python3", GASTRO_ASSIGN_PATH, "--id", str(order_id)]
    if koordynator:
        cmd.append("--koordynator")
    elif kurier_name:
        cmd += ["--kurier", kurier_name, "--time", str(time_minutes)]
    else:
        return False, "no_target"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True, (r.stdout.strip() or "ok")[-400:]
        return False, f"exit={r.returncode} {r.stderr.strip()[-400:]}"
    except subprocess.TimeoutExpired:
        return False, "subprocess_timeout"
    except Exception as e:
        return False, str(e)


# ---- async tasks ----

async def shadow_tailer(state: dict) -> None:
    path = state["shadow_log_path"]
    try:
        offset = Path(path).stat().st_size
    except FileNotFoundError:
        offset = 0
    _log.info(f"tailer start offset={offset} path={path}")
    while not _shutdown:
        try:
            if Path(path).exists():
                size = Path(path).stat().st_size
                if size < offset:
                    offset = 0  # rotated
                if size > offset:
                    with open(path) as f:
                        f.seek(offset)
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if rec.get("verdict") == "PROPOSE":
                                await state["incoming"].put(rec)
                        offset = f.tell()
        except Exception as e:
            _log.warning(f"tailer err: {e}")
        await asyncio.sleep(POLL_SHADOW_SEC)


async def proposal_sender(state: dict) -> None:
    while not _shutdown:
        try:
            rec = await asyncio.wait_for(state["incoming"].get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        oid = str(rec.get("order_id") or "")
        if not oid or oid in state["pending"]:
            continue
        text = format_proposal(rec)
        top_candidates = [rec.get("best")] + list((rec.get("alternatives") or []))[:2]
        kbd = build_keyboard(oid, candidates=top_candidates)
        r = await asyncio.to_thread(
            tg_request, state["token"], "sendMessage",
            {
                "chat_id": state["admin_id"],
                "text": text,
                "reply_markup": kbd,
            },
        )
        if not r.get("ok"):
            _log.warning(f"sendMessage fail oid={oid}: {r.get('error') or r.get('description')}")
            continue
        message_id = r["result"]["message_id"]
        state["pending"][oid] = {
            "order_id": oid,
            "message_id": message_id,
            "sent_at": now_iso(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=PROPOSAL_TIMEOUT_SEC)).isoformat(),
            "decision_record": rec,
        }
        save_pending(state["pending_path"], state["pending"])
        _log.info(f"SENT oid={oid} msg={message_id}")


async def updates_poller(state: dict) -> None:
    offset = 0
    fail_count = 0
    MAX_FAILS = 10
    while not _shutdown:
        try:
            r = await asyncio.to_thread(
                tg_request, state["token"], "getUpdates",
                {"offset": offset, "timeout": 30}, 35,
            )
        except Exception as e:
            fail_count += 1
            backoff = min(5 * fail_count, 60)
            _log.error(f"getUpdates exception ({fail_count}/{MAX_FAILS}): {e}, backoff {backoff}s")
            if fail_count >= MAX_FAILS:
                _log.critical(f"getUpdates: {MAX_FAILS} consecutive fails — sys.exit(1)")
                sys.exit(1)
            await asyncio.sleep(backoff)
            continue
        if not r.get("ok"):
            fail_count += 1
            backoff = min(5 * fail_count, 60)
            _log.warning(f"getUpdates fail ({fail_count}/{MAX_FAILS}): {r.get('error') or r.get('description')}, backoff {backoff}s")
            if fail_count >= MAX_FAILS:
                _log.critical(f"getUpdates: {MAX_FAILS} consecutive fails — sys.exit(1)")
                sys.exit(1)
            await asyncio.sleep(backoff)
            continue
        fail_count = 0
        for upd in r.get("result", []):
            offset = upd["update_id"] + 1
            cb = upd.get("callback_query")
            msg = upd.get("message")
            try:
                if cb:
                    data = cb.get("data", "")
                    if ":" in data:
                        action, oid = data.split(":", 1)
                        await handle_callback(state, action, oid, cb)
                elif msg:
                    await handle_message(state, msg)
            except Exception as e:
                _log.error(f"update err: {e}")


# ---- message handlers (F1.4a /status) ----

SLA_LOG_PATH = "/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"


def _today_warsaw_start_utc() -> datetime:
    """Start dnia (00:00 Warsaw) w UTC."""
    now_warsaw = datetime.now(WARSAW)
    start_warsaw = now_warsaw.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_warsaw.astimezone(timezone.utc)


def _yesterday_warsaw_range_utc():
    """Wczoraj 00:00 → dzis 00:00 Warsaw, w UTC (F1.6)."""
    today_start = _today_warsaw_start_utc()
    return today_start - timedelta(days=1), today_start


def _count_delivered_today(start_utc: datetime) -> int:
    """State orders delivered od początku dnia Warsaw."""
    from dispatch_v2 import state_machine
    count = 0
    for oid, o in state_machine.get_all().items():
        if o.get("status") != "delivered":
            continue
        d = o.get("delivered_at") or o.get("czas_doreczenia")
        dt = parse_panel_timestamp(d) if d else None
        if dt is not None and dt >= start_utc:
            count += 1
    return count


def _count_learning_today(path: str, start_utc: datetime) -> Counter:
    """Zlicz action w learning_log.jsonl od start_utc (Warsaw 00:00)."""
    counts: Counter = Counter()
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts_str = r.get("ts", "")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= start_utc:
                        counts[r.get("action", "?")] += 1
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return counts


def _count_learning_in_range(path: str, start_utc: datetime, end_utc: datetime) -> Counter:
    """Zlicz action w learning_log.jsonl w zakresie [start_utc, end_utc) (F1.6)."""
    counts: Counter = Counter()
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts_str = r.get("ts", "")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if start_utc <= ts < end_utc:
                        counts[r.get("action", "?")] += 1
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return counts


def _sla_records_in_range(path: str, start_utc: datetime, end_utc: datetime) -> list:
    """Zwraca listę sla_log records z logged_at w zakresie (F1.6)."""
    records = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts_str = r.get("logged_at", "")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if start_utc <= ts < end_utc:
                        records.append(r)
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return records


def _systemd_status() -> Dict[str, bool]:
    services = [
        "dispatch-panel-watcher",
        "dispatch-sla-tracker",
        "dispatch-shadow",
        "dispatch-telegram",
    ]
    result: Dict[str, bool] = {}
    for svc in services:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            )
            result[svc] = (r.stdout.strip() == "active")
        except Exception:
            result[svc] = False
    return result


def format_status() -> str:
    """Build /status message body (F1.4a)."""
    from dispatch_v2 import state_machine

    now_warsaw = datetime.now(WARSAW)
    today_start_utc = _today_warsaw_start_utc()

    try:
        stats = state_machine.stats()
    except Exception:
        all_o = state_machine.get_all()
        c = Counter(o.get("status", "?") for o in all_o.values())
        stats = {"total": len(all_o), "by_status": dict(c), "active_per_courier": {}}

    by_status = stats.get("by_status", {}) or {}
    active_per_courier = stats.get("active_per_courier", {}) or {}

    svcs = _systemd_status()
    svc_names = [
        ("dispatch-panel-watcher", "watcher"),
        ("dispatch-sla-tracker",   "tracker"),
        ("dispatch-shadow",        "shadow"),
        ("dispatch-telegram",      "telegram"),
    ]
    svc_lines = [f"{'✅' if svcs.get(full) else '❌'} {short}" for full, short in svc_names]

    delivered_today = _count_delivered_today(today_start_utc)
    lc = _count_learning_today(LEARNING_LOG_PATH, today_start_utc)
    tak = lc.get("TAK", 0)
    nie = lc.get("NIE", 0)
    inny = lc.get("INNY", 0)
    koord = lc.get("KOORD", 0)
    timeout = lc.get("TIMEOUT", 0)
    total_proposals = tak + nie + inny + koord + timeout
    agreement_rate = (100 * tak / total_proposals) if total_proposals > 0 else 0.0

    active_couriers = len([v for v in active_per_courier.values() if v])

    lines = [
        f"🟢 Ziomek status ({now_warsaw.strftime('%H:%M')})",
        "",
        "Serwisy:",
    ]
    lines.extend(svc_lines)
    lines.append("")
    lines.append("Ordery (state):")
    for key in ("assigned", "picked_up", "planned", "delivered"):
        val = by_status.get(key, 0)
        lines.append(f"• {key}: {val}")
    lines.append("")
    lines.append(f"Fleet aktywny: {active_couriers}")
    lines.append("")
    lines.append("Dziś:")
    lines.append(f"• Delivered: {delivered_today}")
    lines.append(f"• Propozycje: {total_proposals}")
    lines.append(f"• Agreement: {tak}/{total_proposals} = {agreement_rate:.1f}%")
    if timeout > 0:
        lines.append(f"• Timeouts: {timeout}")

    # === WCZORAJ (F1.6 — zastępuje auto-briefing) ===
    yest_start, yest_end = _yesterday_warsaw_range_utc()
    yest_sla = _sla_records_in_range(SLA_LOG_PATH, yest_start, yest_end)
    yest_delivered = len(yest_sla)
    yest_lc = _count_learning_in_range(LEARNING_LOG_PATH, yest_start, yest_end)
    y_tak = yest_lc.get("TAK", 0)
    y_total = sum(v for k, v in yest_lc.items() if k in ("TAK", "NIE", "INNY", "KOORD", "TIMEOUT"))
    y_rate = (100 * y_tak / y_total) if y_total > 0 else 0.0

    lines.append("")
    lines.append("Wczoraj:")
    lines.append(f"• Delivered: {yest_delivered}")
    lines.append(f"• Propozycje: {y_total}")
    lines.append(f"• Agreement: {y_tak}/{y_total} = {y_rate:.1f}%")

    # Top 3 kurierów wczoraj (lazy import — unika circular z courier_ranking)
    if yest_delivered > 0:
        try:
            from dispatch_v2 import courier_ranking
            ranking = courier_ranking.compute_ranking(yest_sla)
            names = courier_ranking._load_courier_names()
            if ranking:
                lines.append("")
                lines.append("Top 3 wczoraj:")
                for i, r in enumerate(ranking[:3], start=1):
                    cname = names.get(r["courier_id"]) or f"K{r['courier_id']}"
                    lines.append(
                        f"{i}. {cname} — {r['deliveries']} dostaw | "
                        f"SLA {r['sla_pct']:.0f}% {courier_ranking._stars(r['sla_pct'])}"
                    )
        except Exception as e:
            _log.warning(f"ranking fail: {e}")
            lines.append("")
            lines.append("Top 3 wczoraj: (ranking error)")

    return "\n".join(lines)


async def handle_message(state: dict, msg: dict) -> None:
    """Handle text messages: /status + free-text manual courier overrides.

    Autoryzacja po chat.id (grupa) — każdy member grupy może pisać.
    """
    chat_id = str(((msg.get("chat") or {}).get("id") or ""))
    from_id = str((msg.get("from") or {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if chat_id != str(state["admin_id"]):
        _log.warning(f"message from unauthorized chat_id={chat_id} user={from_id}")
        return

    if text.startswith("/"):
        cmd = text.split()[0].lower()
        if cmd == "/status":
            try:
                body = await asyncio.to_thread(format_status)
            except Exception as e:
                _log.exception("format_status failed")
                body = f"❌ status error: {type(e).__name__}: {e}"
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {"chat_id": state["admin_id"], "text": body},
            )
            _log.info(f"/status responded to admin={from_id}")
        return

    # NLP assistant — wolny tekst (F2.2)
    text_lower = text.lower().strip()

    if any(w in text_lower for w in ["pomoc", "help", "komendy", "co umiesz"]):
        help_body = (
            "🤖 Ziomek rozumie:\n"
            "• 'Mykyta nie pracuje' — wyklucza kuriera do końca dnia\n"
            "• 'Mykyta wrócił' — przywraca kuriera\n"
            "• 'reset' — czyści wszystkie wykluczenia\n"
            "• 'kto pracuje' — lista kurierów na zmianie\n"
            "• 'ile zleceń' — statystyki dnia\n"
            "• /status — pełny raport serwisów"
        )
        await asyncio.to_thread(
            tg_request, state["token"], "sendMessage",
            {"chat_id": state["admin_id"], "text": help_body},
        )
        return

    if any(w in text_lower for w in ["kto pracuje", "kto jest", "flota", "kurierzy", "ilu kurierów"]):
        schedule_path = "/root/.openclaw/workspace/dispatch_state/schedule_today.json"
        try:
            with open(schedule_path) as _f:
                sched = json.loads(_f.read())
            if isinstance(sched, dict):
                active = [n for n, v in sched.items() if (v or {}).get("on_shift")]
            elif isinstance(sched, list):
                active = [e.get("name", "?") for e in sched if e.get("on_shift")]
            else:
                active = []
            if active:
                body = f"👥 Na zmianie ({len(active)}):\n" + "\n".join(f"• {n}" for n in sorted(active)[:20])
            else:
                body = "👥 Brak kurierów na zmianie wg grafiku (lub grafik pusty)"
        except FileNotFoundError:
            body = "👥 Grafik nie załadowany (schedule_today.json brak)"
        except Exception as e:
            body = f"👥 Błąd odczytu grafiku: {e}"
        await asyncio.to_thread(
            tg_request, state["token"], "sendMessage",
            {"chat_id": state["admin_id"], "text": body},
        )
        return

    if any(w in text_lower for w in ["ile zleceń", "ile dziś", "ordery", "statystyki", "ile orderów"]):
        state_path = "/root/.openclaw/workspace/dispatch_state/state.json"
        try:
            with open(state_path) as _f:
                st = json.loads(_f.read())
            stats = st.get("session_stats") or st.get("stats") or {}
            delivered = stats.get("delivered_today", "?")
            proposals = stats.get("proposals_today", "?")
            agreement = stats.get("agreement_today")
            body = f"📊 Dziś: {delivered} dostaw, {proposals} propozycji Ziomka"
            if agreement is not None:
                body += f", agreement {agreement}"
        except Exception as e:
            body = f"📊 Błąd odczytu state.json: {e}"
        await asyncio.to_thread(
            tg_request, state["token"], "sendMessage",
            {"chat_id": state["admin_id"], "text": body},
        )
        return

    # REPLY FEEDBACK (F2.1c): Adrian odpowiada na propozycję Ziomka
    # Format: "Gabriel 40" / "Michał 20min" / "Bartek 14:30" / "Bartek"
    reply_to = msg.get("reply_to_message") or {}
    reply_msg_id = reply_to.get("message_id")
    if reply_msg_id:
        matched_oid = None
        matched_rec = None
        for p_oid, p_rec in list(state["pending"].items()):
            if p_rec.get("message_id") == reply_msg_id:
                matched_oid = p_oid
                matched_rec = p_rec
                break
        if matched_oid and matched_rec:
            # REPLY context: samo imię OK (default z compute_assign_time gdy brak czasu).
            dr_matched = matched_rec.get("decision_record") or {}
            known = _known_names_from_decision(dr_matched)
            parsed = _parse_courier_time(text, allow_name_only=True, known_names=known)
            if parsed is None:
                # Nie wygląda jak "Imię [czas]" — ignoruj Reply (nie spamuj gastro_assign)
                _log.info(f"REPLY_OVERRIDE skip oid={matched_oid}: text unparseable {text[:60]!r}")
                return
            courier_name, time_min = parsed
            if time_min is None:
                time_min = compute_assign_time(dr_matched)
            try:
                ok, assign_msg = await asyncio.to_thread(
                    run_gastro_assign, matched_oid, courier_name, time_min, False
                )
                confirm = (
                    f"✅ Przypisano {courier_name or '?'} za {time_min} min (#{matched_oid})"
                    if ok else
                    f"⚠️ Błąd przypisania {courier_name} #{matched_oid}: {assign_msg[:60]}"
                )
            except Exception as e:
                confirm = f"❌ Błąd: {e}"
                ok = False
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {"chat_id": state["admin_id"], "text": confirm},
            )
            dr = matched_rec.get("decision_record") or {}
            append_learning(state["learning_log_path"], {
                "ts": now_iso(),
                "order_id": matched_oid,
                "action": "REPLY_OVERRIDE",
                "ok": ok,
                "feedback": f"reply: {text[:80]}",
                "decision": dr,
            })
            state["pending"].pop(matched_oid, None)
            save_pending(state["pending_path"], state["pending"])
            _log.info(f"REPLY_OVERRIDE oid={matched_oid} courier={courier_name} time={time_min} ok={ok}")
            return

    # Free-text bez Reply → najnowszy pending proposal (F2.4 Task #6).
    # Gdy wiadomość nie jest Reply i nie pasuje do komendy — spróbuj sparsować
    # jako "Imię [czas]" i przypisz do najnowszego pending (max sent_at).
    # allow_name_only=False + known_names z latest_rec: samo imię OK gdy matchuje
    # kandydata; fallback first-word wymaga czasu (anty-false-positive).
    if state["pending"]:
        latest_oid = max(
            state["pending"].keys(),
            key=lambda k: state["pending"][k].get("sent_at", ""),
        )
        latest_rec = state["pending"][latest_oid]
        dr_latest = latest_rec.get("decision_record") or {}
        known = _known_names_from_decision(dr_latest)
        parsed = _parse_courier_time(text, allow_name_only=False, known_names=known)
    else:
        parsed = None
    if parsed is not None and state["pending"]:
        courier_name, time_min = parsed
        if time_min is None:
            time_min = compute_assign_time(dr_latest)
        try:
            ok, assign_msg = await asyncio.to_thread(
                run_gastro_assign, latest_oid, courier_name, time_min, False
            )
            confirm = (
                f"✅ Przypisano {courier_name} za {time_min} min (#{latest_oid} — najnowszy pending)"
                if ok else
                f"⚠️ Błąd przypisania {courier_name} #{latest_oid}: {assign_msg[:60]}"
            )
        except Exception as e:
            confirm = f"❌ Błąd: {e}"
            ok = False
        await asyncio.to_thread(
            tg_request, state["token"], "sendMessage",
            {"chat_id": state["admin_id"], "text": confirm},
        )
        dr = latest_rec.get("decision_record") or {}
        append_learning(state["learning_log_path"], {
            "ts": now_iso(),
            "order_id": latest_oid,
            "action": "REPLY_OVERRIDE",
            "ok": ok,
            "feedback": f"free_text(latest): {text[:80]}",
            "decision": dr,
        })
        state["pending"].pop(latest_oid, None)
        save_pending(state["pending_path"], state["pending"])
        _log.info(
            f"REPLY_OVERRIDE (free-text) oid={latest_oid} courier={courier_name} "
            f"time={time_min} ok={ok}"
        )
        return

    # Nieznana wiadomość — loguj from_id (zbieramy user_id Bartka)
    _log.info(f"unhandled msg from={from_id} text={text[:80]!r}")

    # Free-text → manual courier overrides
    from dispatch_v2 import manual_overrides
    action, response = await asyncio.to_thread(manual_overrides.parse_command, text)
    if not response:
        return
    await asyncio.to_thread(
        tg_request, state["token"], "sendMessage",
        {"chat_id": state["admin_id"], "text": response},
    )
    _log.info(f"override action={action} text={text!r}")


async def handle_callback(state: dict, action: str, oid: str, cb: dict) -> None:
    # ASSIGN callback format: "ASSIGN:{oid}:{courier_id}:{time_min}" → po split(":",1)
    # w updates_poller zmienna `oid` zawiera "466700:207:15". Rozdzielamy na części
    # PRZED state["pending"].get(oid), żeby lookup używał bare order_id.
    assign_cid: Optional[str] = None
    assign_time_min: Optional[int] = None
    if action == "ASSIGN":
        parts = oid.split(":")
        if len(parts) == 3:
            oid = parts[0]
            assign_cid = parts[1]
            try:
                assign_time_min = int(parts[2])
            except (TypeError, ValueError):
                assign_time_min = None
        else:
            # malformed — keep oid as-is, will fall to "unknown" branch
            pass

    # Security: weryfikacja chat_id + logowanie from_id (F2.2)
    cb_chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
    cb_from_id = str((cb.get("from") or {}).get("id", ""))
    cb_from_name = (cb.get("from") or {}).get("first_name", "?")
    if cb_chat_id != str(state["admin_id"]):
        _log.warning(
            f"SECURITY: callback from unauthorized chat={cb_chat_id} "
            f"user={cb_from_id}({cb_from_name}) action={action} oid={oid}"
        )
        await asyncio.to_thread(
            tg_request, state["token"], "answerCallbackQuery",
            {"callback_query_id": cb["id"], "text": "⛔ unauthorized"},
        )
        return
    _log.info(f"callback action={action} oid={oid} from={cb_from_name}(id={cb_from_id})")

    entry = state["pending"].get(oid)
    token = state["token"]
    if entry is None:
        await asyncio.to_thread(
            tg_request, token, "answerCallbackQuery",
            {"callback_query_id": cb["id"], "text": f"Unknown order #{oid}"},
        )
        return

    rec = entry["decision_record"]
    best = rec.get("best") or {}
    courier_name = name_lookup(best.get("courier_id"), best.get("name"))

    ok = False
    action_for_log = action  # dla learning_log (ASSIGN → ASSIGN_DIRECT)
    if action == "TAK":
        time_min = compute_assign_time(rec)
        ok, msg = await asyncio.to_thread(run_gastro_assign, oid, courier_name, time_min, False)
        feedback = f"✅ {courier_name} ({time_min}m)" if ok else f"❌ assign: {msg[:80]}"
    elif action == "ASSIGN":
        # Per-candidate przycisk (F2.4). Lookup name z best+alternatives.
        if assign_cid is None or assign_time_min is None:
            ok, feedback = False, "❌ malformed ASSIGN callback"
        else:
            alts = rec.get("alternatives") or []
            all_cands = [best] + list(alts)
            match = next(
                (c for c in all_cands if c and str(c.get("courier_id")) == str(assign_cid)),
                None,
            )
            match_name = match.get("name") if match else None
            assign_name = name_lookup(assign_cid, match_name)
            ok, msg = await asyncio.to_thread(
                run_gastro_assign, oid, assign_name, assign_time_min, False
            )
            feedback = (
                f"✅ {assign_name} ({assign_time_min}m)"
                if ok else f"❌ assign: {msg[:80]}"
            )
            action_for_log = "ASSIGN_DIRECT"
    elif action == "NIE":
        ok, feedback = True, "⏭ pozostaje w puli"
    elif action == "INNY":
        # MVP: just ack — full flow (follow-up message with kurier_id) is TODO
        ok, feedback = True, "🔄 INNY (MVP: wpisz ręcznie w panel lub wyślij ponownie)"
    elif action == "KOORD":
        ok, msg = await asyncio.to_thread(run_gastro_assign, oid, None, 0, True)
        feedback = "👤 KOORD" if ok else f"❌ koord: {msg[:80]}"
    else:
        feedback = f"unknown {action}"

    await asyncio.to_thread(
        tg_request, token, "answerCallbackQuery",
        {"callback_query_id": cb["id"], "text": feedback},
    )
    # Strip buttons from original message so it can't be clicked twice
    await asyncio.to_thread(
        tg_request, token, "editMessageReplyMarkup",
        {
            "chat_id": state["admin_id"],
            "message_id": entry["message_id"],
            "reply_markup": {"inline_keyboard": []},
        },
    )

    log_rec = {
        "ts": now_iso(),
        "order_id": oid,
        "action": action_for_log,
        "ok": ok,
        "feedback": feedback,
        "decision": rec,
    }
    if action == "ASSIGN" and assign_cid is not None:
        log_rec["chosen_courier_id"] = str(assign_cid)
        log_rec["assign_time_min"] = assign_time_min
        log_rec["proposed_courier_id"] = str((best or {}).get("courier_id") or "")
    append_learning(state["learning_log_path"], log_rec)
    state["pending"].pop(oid, None)
    save_pending(state["pending_path"], state["pending"])
    _log.info(f"CB {action_for_log} oid={oid} → {feedback}")


def _classify_timeout_outcome(cur_status: Optional[str]) -> str:
    """Map state_machine cur_status to learning_log timeout_outcome bucket.

    F2.2-prep P1 (2026-04-18): discriminate TIMEOUT_SUPERSEDED events for
    downstream analyzers. Keeps umbrella action label for backward-compat.

    Buckets:
    - AWAITING_ASSIGNMENT: order planned (never-assigned OR returned-to-pool).
      Empirycznie 54.6% z 874 historical TIMEOUT_SUPERSEDED events. Sprint C5
      może later split via events.db join (never-assigned vs returned-to-pool).
    - OVERRIDDEN_BY_LATER: order past proposal (assigned/picked_up/delivered).
      Legitimate user/koord decision override.
    - ORDER_CANCELLED: order dropped — different semantics than override.
    - EXPIRED_NO_USER_INPUT: defensive, cur_status=new edge case
      (watchdog filters != "new", rarely hit here).
    - UNKNOWN_STATE: defensive, logs warning when state_machine evolves
      beyond classifier awareness.
    """
    if cur_status == "planned":
        return "AWAITING_ASSIGNMENT"
    if cur_status in ("assigned", "picked_up", "delivered"):
        return "OVERRIDDEN_BY_LATER"
    if cur_status == "cancelled":
        return "ORDER_CANCELLED"
    if cur_status == "new":
        return "EXPIRED_NO_USER_INPUT"
    return "UNKNOWN_STATE"


async def watchdog(state: dict) -> None:
    while not _shutdown:
        now = datetime.now(timezone.utc)
        expired = []
        for oid, entry in list(state["pending"].items()):
            try:
                exp = datetime.fromisoformat(entry["expires_at"])
            except Exception:
                continue
            if now >= exp:
                expired.append(oid)
        # Snapshot state raz na wszystkie expired, żeby uniknąć N×get_all
        state_all = {}
        if expired:
            try:
                from dispatch_v2 import state_machine
                state_all = state_machine.get_all()
            except Exception as _e:
                _log.warning(f"watchdog state_machine load fail: {_e}")
        for oid in expired:
            entry = state["pending"][oid]
            cur = state_all.get(str(oid)) or {}
            cur_status = cur.get("status")
            # BUG1 fix: jeśli zlecenie zostało już ręcznie obsłużone (assigned/picked_up/
            # delivered/cancelled), nie spamuj timeoutem — cicho usuń z pending.
            if cur_status and cur_status != "new":
                _log.info(f"TIMEOUT silent oid={oid}: status={cur_status} (już obsłużone)")
                timeout_outcome = _classify_timeout_outcome(cur_status)
                if timeout_outcome == "UNKNOWN_STATE":
                    _log.warning(f"TIMEOUT_SUPERSEDED unknown cur_status={cur_status!r} oid={oid}")
                append_learning(state["learning_log_path"], {
                    "ts": now_iso(),
                    "order_id": oid,
                    "action": "TIMEOUT_SUPERSEDED",
                    "timeout_outcome": timeout_outcome,
                    "timeout_outcome_detail": cur_status or "unknown",
                    "ok": True,
                    "feedback": f"order już {cur_status} — silent skip",
                    "decision": entry["decision_record"],
                })
                state["pending"].pop(oid, None)
                save_pending(state["pending_path"], state["pending"])
                continue
            _log.warning(f"TIMEOUT oid={oid} → brak odpowiedzi, zlecenie pozostaje w puli")
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {
                    "chat_id": state["admin_id"],
                    "text": f"⏰ Timeout #{oid} (5 min) → brak decyzji, zlecenie w puli",
                },
            )
            append_learning(state["learning_log_path"], {
                "ts": now_iso(),
                "order_id": oid,
                "action": "TIMEOUT_SKIP",
                "ok": True,
                "feedback": "brak decyzji w czasie — zlecenie pozostaje w puli",
                "decision": entry["decision_record"],
            })
            state["pending"].pop(oid, None)
            save_pending(state["pending_path"], state["pending"])
        await asyncio.sleep(10)


# ---- main ----

async def main_async() -> None:
    cfg = load_config()
    env = _load_env(TELEGRAM_ENV_PATH)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    admin_id = str(cfg["telegram"]["admin_id"])

    if not token or token == "PLACEHOLDER":
        _log.error(
            "TELEGRAM_BOT_TOKEN missing / PLACEHOLDER — "
            "approver WILL NOT SEND until real token is set in .secrets/telegram.env"
        )

    state = {
        "token": token,
        "admin_id": admin_id,
        "shadow_log_path": cfg["paths"]["shadow_log"],
        "pending_path": PENDING_PATH,
        "learning_log_path": LEARNING_LOG_PATH,
        "incoming": asyncio.Queue(),
        "pending": load_pending(PENDING_PATH),
    }

    _log.info(
        f"telegram_approver START admin={admin_id} "
        f"pending={len(state['pending'])} token={'SET' if token and token != 'PLACEHOLDER' else 'MISSING'}"
    )

    await asyncio.gather(
        shadow_tailer(state),
        proposal_sender(state),
        updates_poller(state),
        watchdog(state),
    )


def _sigterm(signum, frame):
    global _shutdown
    _log.info(f"signal {signum} → shutdown")
    _shutdown = True


def run() -> int:
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
    _log.info("telegram_approver STOP")
    return 0


if __name__ == "__main__":
    sys.exit(run())
