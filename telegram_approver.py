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
import re
import signal
import subprocess
import sys
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dispatch_v2 import manual_overrides  # V3.26 hotfix: top-level import
                                          # (memory: V3.19g1 crash z `from X import Y` w funkcji)
from dispatch_v2.common import (
    ENABLE_TELEGRAM_FREETEXT_ASSIGN,
    ENABLE_TIMELINE_FORMAT,
    ENABLE_TRANSPARENCY_REASON,
    ENABLE_TRANSPARENCY_ROUTE,
    FIRMOWE_KONTO_ADDRESS_IDS,
    WARSAW,
    drop_zone_from_address,
    flag,
    load_config,
    load_flags,
    now_iso,
    parse_panel_timestamp,
    setup_logger,
)


# TASK B SHIFT NOTIFICATIONS — manual /koniec + /poprawa commands + SHIFT_*
# DM callback auth (gate expand fix 2026-05-05). Authorized user IDs allowed to:
#   - Issue /koniec + /poprawa text commands (handle_message)
#   - Klikać SHIFT_* callback buttons z prywatnego DM (security gate expand)
#   - W przyszłości CZAS_TAK/NIE/CZEKAJ z TASK A (gdy czasówki trafią do DM)
# Adrian = 8765130486; Bartek = 8753482870 (added 2026-05-05 06:43 UTC, post /start).
#
# Backlog #8 (2026-05-07): hot-reload via flags.json. Runtime checks używają
# `_authorized_user_ids()` — kolejne user_id Adrian dodaje przez edit `flags.json`
# bez restart dispatch-telegram (jeden restart dziś = ostatni). Module-level alias
# `KONIEC_AUTHORIZED_USER_IDS` zostaje dla backward compat unit tests.
_KONIEC_AUTHORIZED_USER_IDS_DEFAULT = [8765130486, 8753482870]


def _authorized_user_ids() -> list[int]:
    """Hot-reload list authorized user_ids dla /koniec /poprawa /shift_* DM commands.

    Fallback do `_KONIEC_AUTHORIZED_USER_IDS_DEFAULT` gdy flags.json brak klucza,
    typu nie list-of-int, lub IO error — bezpiecznie zachowuje obecny stan.

    MP-#10 (2026-05-08): silent-killer fix per Lekcja #32 — flags.json corrupt
    mid-runtime nie może spowodować niezauważonego revert do hardcoded default.
    Log warning + dedup once-per-process dla nie-spammowania (dedup attribute na
    funkcji). Auth boundary — fallback bezpieczny (default list 2 osoby), ale
    operator MUSI wiedzieć że hot-reload nie działa.
    """
    try:
        flags = load_flags() or {}
        ids = flags.get("KONIEC_AUTHORIZED_USER_IDS")
        if isinstance(ids, list) and ids and all(isinstance(x, int) for x in ids):
            return ids
    except Exception as e:
        if not getattr(_authorized_user_ids, "_warned", False):
            _log.warning(
                f"_authorized_user_ids: flags.json read fail ({type(e).__name__}: {e}), "
                f"fallback do _KONIEC_AUTHORIZED_USER_IDS_DEFAULT={_KONIEC_AUTHORIZED_USER_IDS_DEFAULT}. "
                f"Hot-reload (Backlog #8) nie zadziała do czasu naprawy flags.json."
            )
            _authorized_user_ids._warned = True
    return _KONIEC_AUTHORIZED_USER_IDS_DEFAULT


# Backward-compat module-level alias — używany przez unit tests (pre-#8 referencje).
# Snapshot przy import; runtime checks używają `_authorized_user_ids()` dla hot-reload.
KONIEC_AUTHORIZED_USER_IDS = _authorized_user_ids()
KONIEC_RE = re.compile(r"^/koniec\s+(\d+)\s*$")
# TB-3 (2026-05-05): /poprawa [cid] mirror /koniec — odwołanie "Nie przyjdzie"
# gdy kurier mimo wszystko przyszedł. Reuses KONIEC_AUTHORIZED_USER_IDS auth.
POPRAWA_RE = re.compile(r"^/poprawa\s+(\d+)\s*$")


# V3.19i (2026-04-30): structured override reason codes dla Telegram callback
# buttons. Każdy odpowiada jednej z hipotez Bartka FILOZ + diagnostic patterns.
# Logged jako action='TG_REASON' w learning_log.jsonl. Forward-compat: V3.19j
# planuje dorzucić TG_TIMING (osobny prefix INNY_CZAS:) — nie kolidują.
TG_REASON_CODES = {
    "wrong_direction": "zły kierunek",
    "better_bundle": "lepszy bundle",
    "bag_overload": "za duży bag",
    "better_eta": "lepszy ETA",
    "wrong_shift_tier": "zły shift/tier",
    "dropzone_mismatch": "drop-zone mismatch",
    "wave_anticipation": "wave anticipation",
    "other": "inny powód",
    # Mockup v2 [⏰ +10 min] button (2026-05-07): operator postpones decision
    # 10 min, koordynator manual reassign w międzyczasie. Faktyczne auto-replan
    # = osobny sprint (wymaga refactoringu pending queue + cron sweeper).
    "postpone_10min": "przesuń o 10 min",
}

# Backlog #12 (2026-05-07): Faza 7-AUTO-PROXIMITY agreement metric.
# Inline buttons "to powinno być AUTO/ACK/ALERT" przy każdej shadow decyzji.
# Adrian klika niezależnie od głównej akcji (ASSIGN/INNY/KOORD) — log only,
# NIE finalizuje proposala (brak editMessage). Daje signal "czy Adrian by tak
# zrobił" jako agreement_rate vs distribution metric.
# Logged jako action='F7AGREE' w learning_log.jsonl (kolumna decision z buttonu,
# shadow_route z decision_record.auto_route). Compute agreement = match/total.
# Flag-gated: FAZA7_AGREEMENT_BUTTONS_ENABLED w flags.json (default False).
F7_AGREE_LABELS = {
    "AUTO": "🤖 AUTO",
    "ACK": "✋ ACK",
    "ALERT": "🚨 ALERT",
}


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
KURIER_IDS_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"  # V3.25 inverse fallback
_courier_names_cache: Optional[Dict[str, str]] = None


def _load_courier_names() -> Dict[str, str]:
    """Lazy load + cache courier_names.json (F1.2).

    V3.25 (STEP A.2): MERGE inverse(kurier_ids.json) lower-priority +
    courier_names.json higher-priority. Cache invalidated only przy restart
    procesu (telegram_approver żyje długo — fresh kurier_ids changes wymagają
    restart, akceptowalne dla rzadkich onboarding events).
    """
    global _courier_names_cache
    if _courier_names_cache is not None:
        return _courier_names_cache
    merged: Dict[str, str] = {}
    try:
        with open(KURIER_IDS_PATH) as f:
            ids = json.load(f)
        for name, cid in ids.items():
            cid_str = str(cid)
            if cid_str not in merged:
                merged[cid_str] = name
    except Exception as e:
        _log.warning(f"_load_courier_names: kurier_ids fallback fail: {e}")
    try:
        with open(COURIER_NAMES_PATH) as f:
            names = json.load(f)
        for cid_str, name in names.items():
            merged[cid_str] = name
    except Exception as e:
        _log.warning(f"courier_names load fail: {e}")
    _courier_names_cache = merged
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


def _v326_help_text() -> str:
    """V3.26 hotfix CHANGE 4: pomoc dla operatora Telegram."""
    return (
        "🤖 Komendy Ziomka:\n"
        "\n"
        "▪️ /stop <imię>  lub  '<imię> nie pracuje'\n"
        "    — wyklucza kuriera do końca dnia\n"
        "▪️ /wraca <imię>  lub  '<imię> wraca'\n"
        "    — przywraca kuriera\n"
        "▪️ /pin <imię|cid>  lub  'pin <imię>'\n"
        "    — PIN kuriera do logowania w aplikacji\n"
        "▪️ /instrukcja_gps [imię]  lub  'instrukcja gps [imię]'\n"
        "    — pełna instrukcja instalacji aplikacji + ustawienia Android\n"
        "▪️ /dopisz <cid> <full_name> — atomic add nowego kuriera\n"
        "▪️ /status — pełny raport serwisów\n"
        "▪️ reset — czyści wszystkie wykluczenia\n"
        "\n"
        "Jako <imię> używaj formy z panelu (np. Adrian Cit, Bartek O., Mateusz O).\n"
        "Każdy confirmation pokazuje cid kuriera — sprawdzaj zgodność."
    )


def _to_warsaw_hhmm(dt_utc: datetime) -> str:
    return dt_utc.astimezone(WARSAW).strftime("%H:%M")


def _pickup_ready_warsaw(decision: dict, now_utc: datetime) -> Tuple[Optional[str], Optional[float]]:
    """Z pickup_ready_at → (HH:MM Warsaw, minuty od now). None gdy brak.

    MP-#10 (2026-05-08): malformed `pickup_ready_at` ISO string (np. panel daje
    pusty string, "0", "null" zamiast None, lub timestamp bez tz info odrzucony
    przez fromisoformat) skutkuje brakującą linią "Odbiór" w propozycji. Log
    warning z oid + iso value żeby operator widział co panel przesyła.
    """
    iso = decision.get("pickup_ready_at")
    if not iso:
        return None, None
    try:
        ready = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception as e:
        oid = decision.get("order_id") or decision.get("oid") or "?"
        _log.warning(
            f"_pickup_ready_warsaw: oid={oid} ISO parse fail "
            f"iso={iso!r} ({type(e).__name__}: {e}) → return (None, None)"
        )
        return None, None
    if ready.tzinfo is None:
        ready = ready.replace(tzinfo=timezone.utc)
    delta_min = (ready - now_utc).total_seconds() / 60.0
    return _to_warsaw_hhmm(ready), delta_min


def _parse_pickup_ready_prep_min(pickup_ready_at, *, oid: Optional[str] = None) -> float:
    """Parse pickup_ready_at (str ISO lub datetime) → prep_min (od now do ready).

    Zwraca 0.0 gdy: brak pickup_ready_at, malformed string, naive datetime z błędem.
    Używane przez build_keyboard / _build_keyboard_v2_grid dla button label tmin
    formula `max(travel_min, prep_min) → ceil/5 → clamp [5,60]`.

    MP-#10 (2026-05-08): unified silent-killer fix dla obu callsites. Wcześniej
    fallback prep_min=0.0 maskował malformed shadow_dispatcher serializacji
    (np. dt.isoformat() vs str → button "5min" zamiast realnego prep). Teraz log
    warning z oid + raw value żeby ujawnić upstream bug. Dedup po (cls, str)
    cap=50 entries dla peak resilience (nie spamuje przy regression burst).
    """
    if not pickup_ready_at:
        return 0.0
    try:
        if isinstance(pickup_ready_at, str):
            ready = datetime.fromisoformat(pickup_ready_at.replace("Z", "+00:00"))
        else:
            ready = pickup_ready_at
        if ready.tzinfo is None:
            ready = ready.replace(tzinfo=timezone.utc)
        return max(0.0, (ready - datetime.now(timezone.utc)).total_seconds() / 60.0)
    except Exception as e:
        seen = getattr(_parse_pickup_ready_prep_min, "_warned", set())
        key = (type(e).__name__, str(pickup_ready_at)[:40])
        if key not in seen and len(seen) < 50:
            _log.warning(
                f"_parse_pickup_ready_prep_min: oid={oid or '?'} parse fail "
                f"raw={pickup_ready_at!r} ({type(e).__name__}: {e}) → fallback 0.0"
            )
            seen.add(key)
            _parse_pickup_ready_prep_min._warned = seen
        return 0.0


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
            # V3.26 Bug A complete (2026-04-25): clarify km semantyka — distance jest
            # liczony od insertion anchor (chronologicznie poprzedni stop w bagu),
            # NIE direct od courier_pos. Z anchor restaurant name pokaż "X km do {Y}"
            # dla operator clarity (vs mylące "X km" sugerujące drive-to-drop).
            anchor_rest = c.get("v326_anchor_restaurant")
            if anchor_rest:
                sub.append(f"{km:.1f} km do {anchor_rest}")
            else:
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
    # V3.26 STEP 1 (R-11): rationale enrichment — gdy flag ON i metrics
    # zawiera v326_rationale (post-scoring builder), append top-3 factors.
    # MP-#10 (2026-05-08): defense-in-depth nie wolno krashnąć propozycji ALE
    # cisza maskuje malformed v326_rationale (LGBM training drift early signal).
    # Log warning once-per-process per error type (dedup na exception class).
    try:
        from dispatch_v2 import common as _C326
        if getattr(_C326, "ENABLE_V326_TRANSPARENCY_RATIONALE", False):
            rat = c.get("v326_rationale") or {}
            dlaczego_pl = rat.get("dlaczego")
            if dlaczego_pl:
                # Replace simple reasons-list z full rationale (richer info).
                return f"   💡 {dlaczego_pl}"
    except Exception as e:
        seen = getattr(_reason_line, "_warned_classes", set())
        cls = type(e).__name__
        if cls not in seen:
            _log.warning(
                f"_reason_line: rationale path fail ({cls}: {e}), "
                f"fallback do legacy reasons-list. cid={c.get('courier_id')}"
            )
            seen.add(cls)
            _reason_line._warned_classes = seen
    if not reasons:
        return ""
    return "   💡 " + " + ".join(reasons)


def _iso_to_warsaw_hhmm(iso_utc):
    """V3.17: ISO UTC → Warsaw HH:MM, None on failure (used by timeline formatter).

    MP-#10 (2026-05-08): timeline section drops linijki gdy plan zawiera malformed
    timestamp (np. shadow_dispatcher serializer regression). API stable (None on
    fail), ale dedup-warn żeby ujawnić cichy bug w plan.pickup_at /
    predicted_delivered_at JSON shape.
    """
    if not iso_utc:
        return None
    try:
        dt = datetime.fromisoformat(iso_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(WARSAW).strftime("%H:%M")
    except Exception as e:
        seen = getattr(_iso_to_warsaw_hhmm, "_warned_isos", set())
        # dedup po (cls, iso) żeby nie spammować ale złapać każdy unikalny bad input
        key = (type(e).__name__, str(iso_utc)[:40])
        if key not in seen and len(seen) < 50:
            _log.warning(f"_iso_to_warsaw_hhmm: parse fail iso={iso_utc!r} ({type(e).__name__}: {e})")
            seen.add(key)
            _iso_to_warsaw_hhmm._warned_isos = seen
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


def _district_safe(addr: Optional[str], city: Optional[str] = None) -> str:
    """V3.19i: safe district extraction. Returns '?' + log warning na exception
    (Lekcja #32 — no silent except).
    """
    if not addr:
        return "?"
    try:
        d = drop_zone_from_address(addr, city)
        if not d or d == "Unknown":
            return "?"
        return d
    except Exception as e:
        _log.warning(
            f"district extract failed addr={addr!r} city={city!r}: "
            f"{type(e).__name__}: {e}"
        )
        return "?"


def _route_section(decision: dict, best: dict) -> str:
    """V3.19i (2026-04-30): always-on TRASA + STRATEGY + POOL section.

    Zawsze emituje (przedtem skip dla solo). Format:
        📍 TRASA:
          Pickup: {restaurant} ({pickup_district})
          Drop:   {address} ({drop_district})

        📦 BAG CONTEXT (N active):
          1. {rest} → {drop_district} (drop ETA: {min} min)
          [...]

        ⚙️ STRATEGY: {strategy} | pool: {total} (feasible: {feasible})

    BAG CONTEXT block omitted gdy bag pusty.
    Districts: '?' fallback gdy extractor failuje (z log warning).

    V3.17: timeline format pre-empts gdy ENABLE_TIMELINE_FORMAT i dane dostępne
    (zachowane dla backward compat w bag-bundle paths).
    """
    if not ENABLE_TRANSPARENCY_ROUTE or not best:
        return ""

    pickup_addr = decision.get("restaurant") or "?"
    drop_addr = decision.get("delivery_address") or "?"
    pickup_d = _district_safe(pickup_addr)
    drop_d = _district_safe(drop_addr)

    lines = [
        "📍 TRASA:",
        f"  Pickup: {pickup_addr} ({pickup_d})",
        f"  Drop:   {drop_addr} ({drop_d})",
    ]

    bag_context = best.get("bag_context") or []
    if bag_context:
        lines.append("")
        lines.append(f"📦 BAG CONTEXT ({len(bag_context)} active):")
        for i, b in enumerate(bag_context, start=1):
            try:
                b_rest = b.get("restaurant") or "?"
                b_drop_d = _district_safe(b.get("delivery_address"))
                eta_min = b.get("eta_drive_min") or b.get("drop_eta_min")
                eta_str = f" (drop ETA: {int(round(float(eta_min)))} min)" if eta_min is not None else ""
                lines.append(f"  {i}. {b_rest} → {b_drop_d}{eta_str}")
            except Exception as e:
                _log.warning(
                    f"bag_context render failed entry={b!r}: "
                    f"{type(e).__name__}: {e}"
                )
                lines.append(f"  {i}. (render error)")

    plan = best.get("plan") or {}
    strategy = plan.get("strategy") or "?"
    pool_total = decision.get("pool_total_count")
    pool_feasible = decision.get("pool_feasible_count")
    if pool_total is not None and pool_feasible is not None:
        pool_str = f"pool: {pool_total} (feasible: {pool_feasible})"
    else:
        pool_str = "pool: ?"
    lines.append("")
    lines.append(f"⚙️ STRATEGY: {strategy} | {pool_str}")

    if ENABLE_TIMELINE_FORMAT and len(plan.get("sequence") or []) > 1:
        timeline = _build_timeline_section(decision, best)
        if timeline:
            lines.append("")
            lines.append(timeline)

    return "\n".join(lines)


# =============================================================================
# Mockup v2 (2026-05-07) — operator-friendly Telegram propozycja redesign
# =============================================================================
# Flag-gated via flags.json `PROPOSAL_FORMAT_V2` (default False, hot-reload).
# Format zaakceptowany przez Adriana 1:1 wg mockup #471167. Zachowuje legacy
# format_proposal() jako fallback gdy flag OFF (zero ryzyka regresji).
# =============================================================================

def _conf_line_v2(decision: dict) -> str:
    """Confidence bucket line z decision.auto_route + best_effort banner.

    Mapping (Adrian spec lock 2026-05-07):
        AUTO  → '🟢 Top 30% pewności — w trybie auto poszłoby samo.'
        ACK   → '🟡 Środek 40% — potrzebny szybki check.'
        ALERT → '🔴 Bottom 30% — wymaga decyzji.'
        None/legacy → ACK (default fallback dla legacy decisions bez Faza 7 field)

    best_effort=True → prepend '⚠️ Best effort — brak feasible kandydata.\n'.
    """
    auto_route = (decision.get("auto_route") or "").upper()
    best = decision.get("best") or {}
    best_effort = best.get("best_effort", False)

    if auto_route == "AUTO":
        line = "🟢 Top 30% pewności — w trybie auto poszłoby samo."
    elif auto_route == "ALERT":
        line = "🔴 Bottom 30% — wymaga decyzji."
    else:
        line = "🟡 Środek 40% — potrzebny szybki check."

    if best_effort:
        return "⚠️ Best effort — brak feasible kandydata.\n" + line
    return line


def _gps_marker_v2(pos_source: Optional[str]) -> str:
    """Pos_source marker dla candidate line — operational PL labels.

    Mapping pokrywa wszystkie 8 pos_source values w live shadow_decisions.jsonl
    (audit 2026-05-07 hotfix post #471182): gps / no_gps / pre_shift /
    last_assigned_pickup / last_picked_up_delivery / last_picked_up_recent /
    last_delivered / post_wave. Operacyjny styl PL (Adrian preference: krótko,
    co kurier robi) zamiast technicznych aliasów źródła pozycji.
    """
    if pos_source in (None, "", "gps"):
        return "📍GPS"
    if pos_source == "no_gps":
        return "❌brak GPS"
    if pos_source == "pre_shift":
        return "🆔pre-shift"
    if pos_source == "last_assigned_pickup":
        return "📍przy restauracji"
    if pos_source in ("last_picked_up_delivery", "last_picked_up_recent"):
        return "📍w trasie"
    if pos_source == "last_delivered":
        return "📍po dostawie"
    if pos_source == "post_wave":
        return "📍po fali"
    if pos_source in ("last_pickup", "last-pickup"):
        # Legacy alias (mockup v2 spec użył tej formy) — zachowane dla bw-compat
        return "📍last-pickup"
    return "❔?"


def _bag_emoji_v2(bag_n: int) -> str:
    """Bag count → emoji bucket (Adrian 2026-05-08): 0-1=🟢, 2-3=🟡, 4+=🔴."""
    if bag_n <= 1:
        return "🟢"
    if bag_n <= 3:
        return "🟡"
    return "🔴"


def _candidate_line_v2(idx: int, c: dict, is_winner: bool) -> str:
    """Mockup v2 candidate row.

    Format: '{idx}. {name} K-{cid} · {gps} · ETA {hhmm} · {bag_emoji} {bag_n}{ ← WYBRANY?}'
    """
    cid = str(c.get("courier_id") or "?")
    name = name_lookup(c.get("courier_id"), c.get("name"))
    gps = _gps_marker_v2(c.get("pos_source"))
    eta = c.get("eta_pickup_hhmm") or c.get("eta_drive_hhmm") or "—"
    bag_n = c.get("r6_bag_size") or c.get("bag_size_before") or 0
    try:
        bag_n = int(bag_n)
    except (TypeError, ValueError):
        bag_n = 0
    bag_emoji = _bag_emoji_v2(bag_n)
    suffix = " ← WYBRANY" if is_winner else ""
    return f"{idx}. {name} K-{cid} · {gps} · ETA {eta} · {bag_emoji} {bag_n}{suffix}"


def _pickup_extension_delta_min(decision: dict) -> Optional[int]:
    """Delta minut o ile Ziomek przedłużył deklarację restauracji.

    delta = pickup_ready_at (effective) − pickup_at_warsaw (raw restaurant declared)

    Source pickup_ready_at: pipeline value (post-extension if czas_kuriera set,
    else raw fallback). Source pickup_at_warsaw: payload raw, propagated by
    shadow_dispatcher._tick (commit pre-extension-route 2026-05-07).

    Returns None gdy któreś brak / parse fail. Returns int(round(...)) gdy oba.
    Caller pokazuje "(+N min)" tylko gdy delta > 0.
    """
    ready_iso = decision.get("pickup_ready_at")
    raw_iso = decision.get("pickup_at_warsaw")
    if not ready_iso or not raw_iso:
        return None
    try:
        ready = datetime.fromisoformat(str(ready_iso).replace("Z", "+00:00"))
        raw = datetime.fromisoformat(str(raw_iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if ready.tzinfo is None:
        ready = ready.replace(tzinfo=timezone.utc)
    if raw.tzinfo is None:
        raw = raw.replace(tzinfo=timezone.utc)
    return int(round((ready - raw).total_seconds() / 60.0))


def _route_lines_v2(decision: dict, best: dict, now_utc: datetime) -> list:
    """Wave-aware chronological trasa dla wybranego kuriera.

    Iteruje wszystkie stopy z best.plan.pickup_at + best.plan.predicted_delivered_at,
    sortuje po czasie. Każdy stop = "🍕|📍 HH:MM — {addr}{ ← TA?}" (🍕 odbiór, 📍 dostawa).
    Start line: "🚖 HH:MM — start ({pos_marker})".

    Adres source per oid:
      - decision.order_id → decision.restaurant / decision.delivery_address
      - inne oid (bag) → bag_context lookup po order_id

    Stop obecnej propozycji (decision.order_id) oznaczony "← TA" — od razu widać
    gdzie nowy order wpada w istniejącą trasę kuriera.

    Fallback: gdy plan.pickup_at + predicted_delivered_at oba puste/None →
    klasyczny 3-line layout (start/odbiór/dostawa) zachowany dla solo orderów
    bez plan-data (legacy behavior).
    """
    now_hhmm = _to_warsaw_hhmm(now_utc)
    # V3.28 ETAP 2 (2026-05-08): pre_shift kurier ma effective_start_at = shift_start
    # (clamp w feasibility/route_simulator). "Start" line trasy pokazuje shift_start
    # zamiast real now — eliminuje fikcyjny "10:31 — start" gdy kurier zaczyna 11:00.
    # Fallback do now gdy brak pola (legacy, post-shift, gps kurier).
    start_iso = (best or {}).get("effective_start_at")
    if start_iso:
        try:
            _sd = datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
            if _sd.tzinfo is None:
                _sd = _sd.replace(tzinfo=timezone.utc)
            now_hhmm = _to_warsaw_hhmm(_sd)
        except Exception:
            pass
    pos_marker = _gps_marker_v2((best or {}).get("pos_source"))
    cur_oid = str(decision.get("order_id") or "")
    cur_rest = decision.get("restaurant") or "?"
    cur_drop = decision.get("delivery_address") or "—"

    plan = (best or {}).get("plan") or {}
    pickup_at = plan.get("pickup_at") or {}
    delivered_at = plan.get("predicted_delivered_at") or {}

    # Build per-oid address map: bag_context (other orders) + current decision
    bag_ctx = (best or {}).get("bag_context") or []
    addr_map = {}  # oid → (restaurant, delivery_address)
    for it in bag_ctx:
        _oid = str(it.get("order_id") or "")
        if _oid:
            addr_map[_oid] = (
                it.get("restaurant") or "?",
                it.get("delivery_address") or it.get("drop_address") or "—",
            )
    addr_map[cur_oid] = (cur_rest, cur_drop)

    # Collect stops: list[(dt_utc, kind, oid, addr)]
    stops = []
    for oid, iso in pickup_at.items():
        if not iso:
            continue
        try:
            dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        rest, _ = addr_map.get(str(oid), ("?", "—"))
        stops.append((dt, "odbiór", str(oid), rest))
    for oid, iso in delivered_at.items():
        if not iso:
            continue
        try:
            dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        _, addr = addr_map.get(str(oid), ("?", "—"))
        stops.append((dt, "dostawa", str(oid), addr))

    if not stops:
        # Fallback do klasycznego 3-line layout (zachowuje legacy behavior dla
        # decyzji bez plan.pickup_at + predicted_delivered_at — np. solo orders
        # gdzie pipeline nie wystawił TSP planu).
        pickup_hhmm, pickup_in_min = _pickup_ready_warsaw(decision, now_utc)
        drop_eta_hhmm = _drop_eta_hhmm_v2(decision, best, pickup_in_min, now_utc)
        out = ["🗺 Trasa:", f"🚖 {now_hhmm} — start ({pos_marker})"]
        if pickup_hhmm is not None:
            out.append(f"🍕 {pickup_hhmm} — {cur_rest} ← TA")
        if drop_eta_hhmm is not None:
            out.append(f"📍 {drop_eta_hhmm} — {cur_drop} ← TA")
        return out

    stops.sort(key=lambda x: x[0])

    out = ["🗺 Trasa:", f"🚖 {now_hhmm} — start ({pos_marker})"]
    for dt, kind, oid, addr in stops:
        hhmm = dt.astimezone(WARSAW).strftime("%H:%M")
        icon = "🍕" if kind == "odbiór" else "📍"
        ta_marker = " ← TA" if oid == cur_oid else ""
        out.append(f"{icon} {hhmm} — {addr}{ta_marker}")
    return out


def _drop_eta_hhmm_v2(
    decision: dict, best: dict, pickup_in_min: Optional[float], now_utc: datetime
) -> Optional[str]:
    """Drop ETA HH:MM Warsaw. Priorytet:
    1. best.plan.predicted_delivered_at[order_id] — bag-aware plan-based ETA.
    2. fallback: pickup_ready + best.plan.drop_eta_min (lub eta_drive_min).
    3. None gdy brak danych (caller pominie linię trasy dostawy).
    """
    plan = (best or {}).get("plan") or {}
    oid = str(decision.get("order_id") or "")
    pred = plan.get("predicted_delivered_at") or {}
    iso = pred.get(oid)
    if iso:
        hhmm = _iso_to_warsaw_hhmm(iso)
        if hhmm:
            return hhmm
    drop_eta_min = (
        best.get("drop_eta_min")
        or plan.get("drop_eta_min")
        or best.get("eta_drive_min_to_drop")
    )
    if drop_eta_min is None:
        return None
    if pickup_in_min is None:
        return None
    try:
        total_min = float(pickup_in_min) + float(drop_eta_min)
    except (TypeError, ValueError):
        return None
    if total_min < 0:
        total_min = 0.0
    delivery_dt = now_utc + timedelta(minutes=total_min)
    return delivery_dt.astimezone(WARSAW).strftime("%H:%M")


def _reason_text_v2(
    best: dict,
    alts: list,
    restaurant: str,
    pickup_in_min: Optional[float],
    top1_eta_min: Optional[float],
) -> str:
    """Mockup v2 💡 reason composer — operational logic, NIE scoring.

    Hotfix 2026-05-07 post #471182: usunięty Priorytet 1 = v326_rationale
    (zwracał scoring breakdown "bliskość -11, timing +5, przewaga +122 vs X"
    co łamie regułę feedback_rules.md "Uzasadnienie wyboru — operational
    logic, NIE scoring. Zero słów: score, kara, feasible, scoring, ranking,
    pool"). Rule-based template ZAWSZE primary, bez fallback do rationale.

    Rule-based template (mockup style):
      - free + ETA == pickup_ready → 'Wolny od ręki, dojedzie dokładnie na gotowe danie z {rest}'
      - free + ETA > pickup_ready → 'Wolny od ręki, dojedzie {extra} min po gotowym daniu z {rest}'
      - bag>0 → 'Z {n} dowozem/dowozami w torbie, dotrze za {extra} min'
    Plus contrast vs najbliższy alt z bag>0 i delay >=10 min vs top1.
    Empty string gdy żaden case nie pasuje (caller pomija 💡 linię).
    """
    if not best:
        return ""

    bag_n = best.get("r6_bag_size") or best.get("bag_size_before") or 0
    try:
        bag_n = int(bag_n)
    except (TypeError, ValueError):
        bag_n = 0
    free_at = best.get("free_at_min")
    parts = []

    if bag_n == 0 and (free_at is not None and free_at <= 0):
        if (
            top1_eta_min is not None
            and pickup_in_min is not None
            and abs(top1_eta_min - pickup_in_min) < 1.0
        ):
            parts.append(f"Wolny od ręki, dojedzie dokładnie na gotowe danie z {restaurant}")
        elif top1_eta_min is not None and pickup_in_min is not None and top1_eta_min > pickup_in_min:
            extra = int(round(top1_eta_min - pickup_in_min))
            parts.append(
                f"Wolny od ręki, dojedzie {extra} min po gotowym daniu z {restaurant}"
            )
        else:
            parts.append(f"Wolny od ręki dla {restaurant}")
    elif bag_n > 0:
        word = "dowozem" if bag_n == 1 else "dowozami"
        if top1_eta_min is not None and pickup_in_min is not None:
            extra = max(0, int(round(top1_eta_min - pickup_in_min)))
            parts.append(f"Z {bag_n} {word} w torbie, dotrze za {extra} min")
        else:
            parts.append(f"Z {bag_n} {word} w torbie")

    # Contrast vs najbliższy alt z bag>0 i meaningful delay
    contrast_alt = None
    contrast_alt_extra = 0
    for a in alts[:2]:
        if not a:
            continue
        a_bag = a.get("r6_bag_size") or a.get("bag_size_before") or 0
        try:
            a_bag = int(a_bag)
        except (TypeError, ValueError):
            a_bag = 0
        a_travel = a.get("travel_min")
        if a_bag >= 1 and a_travel is not None and top1_eta_min is not None:
            try:
                delay = float(a_travel) - float(top1_eta_min)
            except (TypeError, ValueError):
                continue
            if delay >= 10:
                contrast_alt = a
                contrast_alt_extra = int(round(delay))
                break

    if contrast_alt is not None:
        a_bag = contrast_alt.get("r6_bag_size") or contrast_alt.get("bag_size_before") or 0
        try:
            a_bag = int(a_bag)
        except (TypeError, ValueError):
            a_bag = 0
        a_word = "dowóz" if a_bag == 1 else "dowozy"
        a_name = name_lookup(contrast_alt.get("courier_id"), contrast_alt.get("name"))
        parts.append(
            f"{a_name} ma już {a_bag} {a_word} w torbie i spóźni się {contrast_alt_extra} min"
        )

    return "; ".join(parts)


def _format_proposal_v2(decision: dict) -> str:
    """Mockup v2 layout (zaakceptowany 2026-05-07 przez Adriana 1:1).

    Layout (vs legacy format_proposal):
        🚖 {best_name} (K-{cid}) → {restaurant} → {drop_addr} ({drop_district})
        ⏱️ Odbiór: {pickup_hhmm}

        {conf_line}                                   ← Top 30/40/30% bucket

        👥 Kandydaci:
        1. {name} K-{cid} · {gps} · ETA {hhmm} · {bag_emoji} {n} ← WYBRANY
        2. ...
        3. ...

        💡 {reason_text}                              ← composer/rationale

        🗺 Trasa:
        • {now_hhmm} — start
        • {pickup_hhmm} — {restaurant} (odbiór)
        • {drop_eta_hhmm} — {drop_addr} (dostawa)
    """
    oid = decision.get("order_id", "?")
    rest = decision.get("restaurant") or "?"
    delivery = decision.get("delivery_address") or "—"
    best = decision.get("best") or {}
    alts = decision.get("alternatives") or []

    now_utc = datetime.now(timezone.utc)
    pickup_ready_hhmm, pickup_in_min = _pickup_ready_warsaw(decision, now_utc)

    # Etap 1 pickup-label (2026-05-08): linia "Odbiór" pokazuje faktyczny czas
    # dotarcia best kandydata (best.eta_pickup_hhmm), nie pickup_ready_at
    # (gotowość restauracji). Nawias = minuty od złożenia zamówienia. Fallback
    # do pickup_ready_at + (+N min) extension delta gdy brak best/eta_pickup
    # albo brak created_at (legacy events).
    best_pickup_hhmm = best.get("eta_pickup_hhmm") if best else None
    mins_since_creation = best.get("mins_since_creation") if best else None
    display_hhmm = best_pickup_hhmm or pickup_ready_hhmm

    best_name = name_lookup(best.get("courier_id"), best.get("name")) if best else "?"
    best_cid = str(best.get("courier_id") or "?") if best else "?"
    drop_district = _district_safe(delivery)

    lines = [
        f"🚖 {best_name} (K-{best_cid}) → {rest} → {delivery} ({drop_district})",
    ]
    if display_hhmm is not None:
        if best_pickup_hhmm is not None and mins_since_creation is not None:
            lines.append(f"⏱️ Odbiór: {display_hhmm} ({int(mins_since_creation)} min od złożenia)")
        else:
            ext_delta = _pickup_extension_delta_min(decision)
            if ext_delta is not None and ext_delta > 0:
                lines.append(f"⏱️ Odbiór: {display_hhmm} (+{ext_delta} min)")
            else:
                lines.append(f"⏱️ Odbiór: {display_hhmm}")
    lines.append("")
    lines.append(_conf_line_v2(decision))
    lines.append("")

    lines.append("👥 Kandydaci:")
    top3 = [best] + list(alts[:2])
    top3_nonempty = [c for c in top3 if c]
    for i, c in enumerate(top3, start=1):
        if not c:
            continue
        lines.append(_candidate_line_v2(i, c, is_winner=(i == 1)))
    lines.append("")

    top1_travel = best.get("travel_min") if best else None
    try:
        top1_travel = float(top1_travel) if top1_travel is not None else None
    except (TypeError, ValueError):
        top1_travel = None
    reason_text = _reason_text_v2(
        best=best,
        alts=alts,
        restaurant=rest,
        pickup_in_min=pickup_in_min,
        top1_eta_min=top1_travel,
    )
    if reason_text:
        lines.append(f"💡 {reason_text}")
        lines.append("")

    lines.extend(_route_lines_v2(decision, best, now_utc))

    return "\n".join(lines)


def _build_keyboard_v2_grid(
    order_id: str,
    candidates: Optional[list],
    pickup_ready_at: Optional[str] = None,
) -> list:
    """Mockup v2 keyboard — 2×2 grid mobile-friendly (Adrian feedback 2026-05-07
    post visual check: większy tap target dla kciuka, tylko 4 buttony, BEZ safety net).

    Layout:
        [✅ Akceptuj]   [🥈 Weź #2]
        [🥉 Weź #3]    [⏰ +10 min]

    Returns list[list[dict]] — 2 rows × 2 buttons (NIE single row × 4).
    Caller bezpośrednio wstawia do inline_keyboard, NO safety net append.

    Callbacks:
      - Akceptuj/Weź #2/Weź #3 → ASSIGN:{oid}:{cid}:{tmin} (compat z legacy router)
      - +10 min                → INNY:postpone_10min:{oid} (reuse INNY router)

    Skip pusty button-slot gdy mniej niż 3 candidates (vacuum slot zostaje pusty
    visualnie — slot zachowany strukturalnie żeby +10 min zostało prawym-dolnym).
    Postpone zawsze obecny w prawym-dolnym rogu (idempotent fallback gdy wszyscy
    kandydaci błędni).

    Tmin formula identyczna z legacy build_keyboard (V3.26 hotfix sync z
    compute_assign_time): tmin = ceil(max(travel_min, prep_min)/5)*5, clamp [5,60].
    """
    import math as _math

    # MP-#10: shared helper z log warning na malformed input (eliminuje silent killer).
    prep_min = _parse_pickup_ready_prep_min(pickup_ready_at, oid=order_id)

    labels = ["✅ Akceptuj", "🥈 Weź #2", "🥉 Weź #3"]
    cand_buttons = []
    for idx, c in enumerate((candidates or [])[:3]):
        if not c:
            continue
        cid = str(c.get("courier_id") or "")
        if not cid:
            continue
        tm_raw = c.get("travel_min") or 0.0
        try:
            tm_raw = float(tm_raw)
        except (TypeError, ValueError):
            tm_raw = 0.0
        needed = max(tm_raw, prep_min)
        time_min = max(5, min(60, int(_math.ceil(needed / 5.0) * 5)))
        cand_buttons.append({
            "text": labels[idx],
            "callback_data": f"ASSIGN:{order_id}:{cid}:{time_min}",
        })

    postpone_btn = {
        "text": "⏰ +10 min",
        "callback_data": f"INNY:postpone_10min:{order_id}",
    }

    # 2×2 grid layout. Pad cand_buttons up to 3 slots (jeśli mniej niż 3
    # kandydatów, slot zostaje pominięty — ale postpone zawsze prawy-dolny).
    row1 = cand_buttons[:2]
    row2 = cand_buttons[2:3] + [postpone_btn]
    return [row1, row2] if row1 else [row2]


def format_proposal(decision: dict) -> str:
    """[PROPOZYCJA] z top3 + pickup_ready + czas deklarowany per kandydat.

    Mockup v2 dispatcher (2026-05-07): jeśli flag PROPOSAL_FORMAT_V2 ON →
    delegate do _format_proposal_v2 (operator-friendly redesign 1:1 z mockup
    #471167). Default OFF = legacy format zachowany (zero regresji).

    Faza 7-AUTO-PROXIMITY shadow (2026-05-06): jeśli decision.auto_route == "AUTO",
    dodaj header line "🤖 PEWIEN — auto-przypisałbym {kurier} (margin +X)" przed
    standard body. Adrian decyzja: chce widoczność classifier verdykt w Telegramie
    przez tydzień shadow przed ewentualnym flagowaniem w prod.
    """
    if flag("PROPOSAL_FORMAT_V2", default=False):
        return _format_proposal_v2(decision)

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

    # Faza 7-AUTO-PROXIMITY: shadow header line (visible during shadow week — Adrian decyzja).
    # Pomijamy gdy auto_route != "AUTO" (ACK/ALERT default flow). Backward-compat:
    # brak pola w decision → skip (legacy proposals nie pokazują linijki).
    auto_route = (decision.get("auto_route") or "").upper()
    auto_route_context = decision.get("auto_route_context") or {}
    pewien_line: Optional[str] = None
    if auto_route == "AUTO":
        margin = auto_route_context.get("auto_route_score_margin")
        tier_best = auto_route_context.get("auto_route_tier_best")
        kurier = best.get("name") or best.get("courier_id") or "?"
        margin_part = f" (margin +{margin:.1f})" if isinstance(margin, (int, float)) else ""
        tier_part = f" [{tier_best}]" if tier_best else ""
        pewien_line = f"🤖 PEWIEN — auto-przypisałbym {kurier}{tier_part}{margin_part}"

    lines = [
        f"{header_tag} #{oid}",
        f"{rest} → {delivery}",
    ]
    if pewien_line is not None:
        lines.append(pewien_line)
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
    lines.append("TAK / INNY (powód) / KOORD")
    return "\n".join(lines)


def build_keyboard(
    order_id: str,
    candidates: Optional[list] = None,
    pickup_ready_at: Optional[str] = None,  # V3.26 hotfix: align z compute_assign_time
    decision: Optional[dict] = None,  # Backlog #12: dla F7AGREE buttons (auto_route field)
) -> dict:
    """Inline keyboard z przyciskami per-kandydat (F2.4).

    Rząd 1: do 3 przycisków ✅ {Imię} {tmin}min — callback ASSIGN:{oid}:{cid}:{tmin}.
        V3.26 hotfix 2026-04-24: tmin = ceil(max(travel_min, prep_min) / 5) * 5,
        clamp [5, 60]. ALIGNED z compute_assign_time logic. Previously used
        travel_min only → button label mismatch z text (np. oid=468163:
        label "5min" vs text "deklarujemy 09:17 ~20 min").
        Pusty jeśli candidates is None/[] lub brak valid courier_id.
    Rząd 2: INNY + KOORD. NIE usunięte — brak decyzji = auto-timeout (5 min).
    """
    import math as _math
    # V3.26 hotfix: compute prep_min (pickup_ready - now) dla label sync z assign logic
    # MP-#10: shared helper z log warning na malformed input (eliminuje silent killer).
    prep_min = _parse_pickup_ready_prep_min(pickup_ready_at, oid=order_id)
    rows = []
    # Mockup v2 (2026-05-07): 2×2 grid mobile-friendly = TYLKO 4 buttony, brak
    # safety net. Adrian feedback post visual check: "Tylko cztery przyciski,
    # resztę usuń." Layout:
    #     [✅ Akceptuj]   [🥈 Weź #2]
    #     [🥉 Weź #3]    [⏰ +10 min]
    # Early return — NIE doklejamy INNY 8-grid + KOORD (poprzedni "safety net"
    # plan został rejected po visual check; mockup 1:1 = strict 4-button).
    if flag("PROPOSAL_FORMAT_V2", default=False):
        # Tech-debt #21 (2026-05-08): F7AGREE row jest pomijany w V2 grid (strict
        # 4-button mockup design, Adrian rejected safety net rows). Gdy oba flags
        # ON jednocześnie, ostrzegamy raz per proces — F7AGREE metric NIE zbiera
        # się gdy V2 LIVE. Backlog #12 calibration window wymaga albo flip
        # PROPOSAL_FORMAT_V2=false na shadow week, albo ETL z shadow_decisions.jsonl.
        if (
            flag("FAZA7_AGREEMENT_BUTTONS_ENABLED", default=False)
            and not getattr(build_keyboard, "_f7agree_v2_warned", False)
        ):
            _log.warning(
                "F7AGREE_BUTTONS_ENABLED=True ignored — PROPOSAL_FORMAT_V2 ON "
                "(mockup v2 strict 4-button, tech-debt #21). Aby zbierać "
                "agreement metric: flip PROPOSAL_FORMAT_V2=false na shadow week."
            )
            build_keyboard._f7agree_v2_warned = True
        v2_rows = _build_keyboard_v2_grid(order_id, candidates, pickup_ready_at)
        return {"inline_keyboard": v2_rows}

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
        # V3.26 hotfix formula (aligned compute_assign_time):
        needed = max(tm_raw, prep_min)
        time_min = max(5, min(60, int(_math.ceil(needed / 5.0) * 5)))
        row1.append({
            "text": f"✅ {name} {time_min}min",
            "callback_data": f"ASSIGN:{order_id}:{cid}:{time_min}",
        })
    if row1:
        rows.append(row1)
    # V3.19i (2026-04-30): 8 structured reason buttons replacing single INNY.
    # Layout: 4 rows × 2 columns. Callback `INNY:{reason_code}:{order_id}`
    # (uppercase, ext. ASSIGN/KOORD precedent). Backward-compat: legacy
    # 2-segment INNY:{order_id} parsed jako reason_code='legacy_inny'.
    # Char limit Telegram 64: longest "INNY:wave_anticipation:469587" = 29 OK.
    reason_items = list(TG_REASON_CODES.items())
    for i in range(0, len(reason_items), 2):
        kb_row = []
        for code, label in reason_items[i : i + 2]:
            kb_row.append({
                "text": f"❌ INNY: {label}",
                "callback_data": f"INNY:{code}:{order_id}",
            })
        rows.append(kb_row)
    rows.append([
        {"text": "👤 KOORD", "callback_data": f"KOORD:{order_id}"},
    ])

    # Backlog #12 (2026-05-07): Faza 7 agreement buttons — extra row "AUTO/ACK/ALERT".
    # Tylko gdy flag enabled AND decision ma auto_route (Faza 7 LIVE shadow).
    # Adrian klika niezależnie od głównej akcji — log only, NIE finalizuje propozycji.
    if (
        decision is not None
        and decision.get("auto_route")  # Faza 7 LIVE = field present
        and flag("FAZA7_AGREEMENT_BUTTONS_ENABLED", default=False)
    ):
        f7_row = []
        for code, label in F7_AGREE_LABELS.items():
            f7_row.append({
                "text": label,
                "callback_data": f"F7AGREE:{code}:{order_id}",
            })
        rows.append(f7_row)

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
    """MP-#11 (2026-05-08): atomic JSONL append via core helper.

    Eliminuje race window dla konkurencyjnych write'ów do learning_log.jsonl
    (telegram_approver + panel_watcher PANEL_OVERRIDE). POSIX O_APPEND atomic
    dla writes ≤PIPE_BUF (4096B), shadow F7AGREE record rzadko ale długie
    rationale string + bag_context array może > 4KB → flock LOCK_EX gwarantuje
    serialization niezależnie od długości. ~6 callsites w pliku korzystają.
    """
    from dispatch_v2.core.jsonl_appender import append_jsonl
    append_jsonl(path, record)


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
    """Z decision record → minuty od teraz do pickup_ready_at (gotowość jedzenia).

    MP-#10 (2026-05-08): malformed ISO → None propagowany do format_proposal,
    który używa as fallback dla candidate line "deklarujemy" calc. Cisza =
    inconsistent UI gdy panel daje malformed timestamp. Log warn z oid + iso.
    """
    iso = decision.get("pickup_ready_at")
    if not iso:
        return None
    try:
        ready = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception as e:
        oid = decision.get("order_id") or decision.get("oid") or "?"
        seen = getattr(_prep_minutes_remaining, "_warned", set())
        key = (type(e).__name__, str(iso)[:40])
        if key not in seen and len(seen) < 50:
            _log.warning(
                f"_prep_minutes_remaining: oid={oid} parse fail "
                f"iso={iso!r} ({type(e).__name__}: {e}) → return None"
            )
            seen.add(key)
            _prep_minutes_remaining._warned = seen
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
        # Defense layer (Adrian decision 2026-05-07): firmowe konto Nadajesz.pl
        # (address_id=161) NIE wysyła propozycji do Telegram. Shadow side filter
        # zazwyczaj catch'uje wcześniej (verdict→SUPPRESSED_FIRMOWE_KONTO przed
        # shadow log write), ale ta warstwa jako belt-and-suspenders dla:
        # (a) edge cases gdy ktoś wpisze rec ze verdict=PROPOSE inną drogą,
        # (b) okres przejściowy przed shadow restart,
        # (c) future regression w shadow filter logic.
        # Hot-reload via flags.json — ENABLE_FIRMOWE_KONTO_TELEGRAM_PROPOSALS=true odwraca.
        _aid_raw = rec.get("address_id")
        try:
            _aid_int = int(_aid_raw) if _aid_raw is not None else None
        except (TypeError, ValueError):
            _aid_int = None
        if (_aid_int is not None
                and _aid_int in FIRMOWE_KONTO_ADDRESS_IDS
                and not flag("ENABLE_FIRMOWE_KONTO_TELEGRAM_PROPOSALS", False)):
            _log.info(
                f"PROPOSAL SUPPRESSED oid={oid} address_id={_aid_raw} "
                f"(firmowe konto, flag ENABLE_FIRMOWE_KONTO_TELEGRAM_PROPOSALS=false)"
            )
            continue
        text = format_proposal(rec)
        top_candidates = [rec.get("best")] + list((rec.get("alternatives") or []))[:2]
        kbd = build_keyboard(oid, candidates=top_candidates,
                              pickup_ready_at=rec.get("pickup_ready_at"),  # V3.26 hotfix
                              decision=rec)  # Backlog #12: dla F7AGREE buttons
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
    """/status raport — sprawdza is-active 4 long-running serwisów.

    MP-#10 (2026-05-08): wcześniej `except Exception: result[svc]=False` maskował
    distinct failure modes. Operator widział "❌ shadow" bez wiedzy CZY:
      - subprocess.TimeoutExpired (systemd zhang) → realny problem
      - FileNotFoundError (systemctl missing) → infra broken
      - PermissionError (sandbox) → config error
      - real "inactive" → service stop

    Każdy distinct case loguje warning z service name + cause. Status pozostaje
    False (operator widzi serwis jako problem), ale logi tłumaczą why dla post-mortem.
    """
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
        except subprocess.TimeoutExpired:
            _log.warning(f"_systemd_status: {svc} is-active TIMEOUT 5s (systemd zhang?)")
            result[svc] = False
        except FileNotFoundError as e:
            _log.warning(f"_systemd_status: {svc} systemctl binary missing ({e})")
            result[svc] = False
        except PermissionError as e:
            _log.warning(f"_systemd_status: {svc} permission denied ({e})")
            result[svc] = False
        except Exception as e:
            _log.warning(f"_systemd_status: {svc} unexpected {type(e).__name__}: {e}")
            result[svc] = False
    return result


def _mp15_get_schedule_age_min() -> Optional[float]:
    """MP-#15 (2026-05-08): schedule_age dla /status linii.

    Returns None gdy schedule_utils nie dostępne lub file missing. Defensive —
    /status nigdy nie crashnie z powodu MP-#15.
    """
    try:
        from schedule_utils import schedule_age_sec
        age = schedule_age_sec()
        if age is None:
            return None
        return age / 60.0
    except Exception as e:
        _log.warning(f"_mp15_get_schedule_age_min fail: {type(e).__name__}: {e}")
        return None


def _mp15_get_last_3_proposals() -> list[str]:
    """MP-#15 (2026-05-08): last 3 propozycje z learning_log dla /status.

    Każda linia format: "  • #oid → cid=X (Yacc good) ✓ accepted" lub
    "  • #oid → KOORD early_bird ⚠️" zależnie od action.

    Skip rare actions (TG_REASON, F7AGREE, /koniec, etc.) — pokazuje tylko
    proposals lifecycle (TAK/NIE/INNY/KOORD/TIMEOUT).

    Returns list of formatted lines, max 3.
    """
    try:
        target_actions = {"TAK", "NIE", "INNY", "KOORD", "TIMEOUT", "TIMEOUT_SUPERSEDED"}
        # Read learning_log tail efficiently — last ~50 lines should contain ≥3 proposals
        path = Path(LEARNING_LOG_PATH)
        if not path.exists():
            return []
        # Read tail with a reasonable byte cap to avoid loading huge files
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, 100_000)  # 100KB tail covers many entries
            f.seek(size - read_size)
            tail = f.read().decode("utf-8", errors="ignore")

        lines_raw = tail.splitlines()
        records: list[dict] = []
        for ln in reversed(lines_raw):  # newest first
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            action = rec.get("action") or rec.get("decision")
            if action not in target_actions:
                continue
            records.append(rec)
            if len(records) >= 3:
                break

        out_lines = []
        for r in records:
            oid = r.get("order_id") or r.get("oid") or "?"
            action = r.get("action") or r.get("decision") or "?"
            cid = r.get("courier_id") or r.get("cid") or r.get("actual_courier_id")
            tmin = r.get("time_min") or r.get("tmin")
            # Format icon per action
            if action == "TAK":
                icon = "✓ accepted"
                cid_label = f"cid={cid}" if cid else "cid=?"
                tmin_label = f" ({int(tmin)} min good)" if tmin else ""
                out_lines.append(f"  • #{oid} → {cid_label}{tmin_label} {icon}")
            elif action == "KOORD":
                out_lines.append(f"  • #{oid} → KOORD ⚠️")
            elif action in ("INNY",):
                reason = r.get("reason_code") or r.get("reason") or "manual"
                out_lines.append(f"  • #{oid} → INNY ({reason}) ❌")
            elif action == "NIE":
                out_lines.append(f"  • #{oid} → NIE ❌")
            elif action == "TIMEOUT":
                out_lines.append(f"  • #{oid} → TIMEOUT (auto-KOORD) ⏱️")
            elif action == "TIMEOUT_SUPERSEDED":
                out_lines.append(f"  • #{oid} → TIMEOUT_SUPERSEDED 🔄")
            else:
                out_lines.append(f"  • #{oid} → {action}")
        return out_lines
    except Exception as e:
        _log.warning(f"_mp15_get_last_3_proposals fail: {type(e).__name__}: {e}")
        return []


def _mp15_get_last_proposal_age_sec() -> Optional[float]:
    """MP-#15 (2026-05-08): wiek najnowszej shadow propozycji (proxy "shadow alive").

    Każdy shadow_decisions record może być >10KB (auto_route_context +
    alternatives + bag_context). Read tail 100KB (covers ~5-10 records) i skip
    leading partial line jeśli nie zaczyna się od `{`.
    """
    try:
        path = Path("/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl")
        if not path.exists():
            return None
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, 100_000)
            f.seek(size - read_size)
            tail = f.read().decode("utf-8", errors="ignore")
        lines = tail.splitlines()
        # Skip first line jeśli partial (read window start mid-record)
        if lines and not lines[0].startswith("{"):
            lines = lines[1:]
        # Parse newest record (last complete line z trailing newline)
        for ln in reversed(lines):
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
                ts = rec.get("ts")
                if not ts:
                    continue
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return (datetime.now(timezone.utc) - dt).total_seconds()
            except Exception:
                continue
        return None
    except Exception as e:
        _log.warning(f"_mp15_get_last_proposal_age_sec fail: {type(e).__name__}: {e}")
        return None


def format_status() -> str:
    """Build /status message body (F1.4a + MP-#15 enhancements per OPS §8.1)."""
    from dispatch_v2 import state_machine

    now_warsaw = datetime.now(WARSAW)
    today_start_utc = _today_warsaw_start_utc()

    try:
        stats = state_machine.stats()
    except Exception as e:
        # MP-#10 (2026-05-08): fallback do manual Counter było silent — gdy
        # state_machine.stats() crashuje (np. internal helper regression),
        # /status pokazywał wynik ale operator nie wiedział że primary path
        # broken. Log warning z exception type/repr żeby flagować w post-mortem.
        _log.warning(
            f"format_status: state_machine.stats() fail ({type(e).__name__}: {e}) "
            f"→ fallback do manual Counter"
        )
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

    # === MP-#15 (2026-05-08): operational health snapshot per OPS §8.1 ===
    # Schedule freshness, last_proposal_age (shadow alive), last 3 proposals.
    # Defensive: każda sekcja własny try/except — częściowy data lepszy niż zero.
    try:
        sched_age_min = _mp15_get_schedule_age_min()
        last_prop_age_sec = _mp15_get_last_proposal_age_sec()
        last_3 = _mp15_get_last_3_proposals()

        lines.append("")
        lines.append("Operational health:")
        if sched_age_min is not None:
            icon = "✓" if sched_age_min <= 30 else ("⚠️" if sched_age_min <= 60 else "❌")
            lines.append(f"{icon} schedule: {sched_age_min:.1f} min temu")
        else:
            lines.append("❌ schedule: file missing")
        if last_prop_age_sec is not None:
            if last_prop_age_sec < 60:
                lines.append(f"✓ shadow: last propozycja {int(last_prop_age_sec)}s temu")
            elif last_prop_age_sec < 600:
                lines.append(f"✓ shadow: last propozycja {int(last_prop_age_sec/60)}min temu")
            else:
                lines.append(f"⚠️ shadow: last propozycja {int(last_prop_age_sec/60)}min temu (cisza)")
        else:
            lines.append("⚠️ shadow: brak danych")

        if last_3:
            lines.append("")
            lines.append("Last 3 propozycje:")
            lines.extend(last_3)
    except Exception as e:
        _log.warning(f"MP-#15 /status enhancement fail: {type(e).__name__}: {e}")

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

    # V3.26 hotfix BUG 1: exclude/include keyword pre-check PRZED free-text proposal
    # flow. Bez tego "adrian cit nie pracuje" trafia do prefix-match i staje się
    # OPERATOR_COMMENT zamiast wykluczyć kuriera. Patrz /tmp/v326_panel_nick_exclusion_bug.
    text_lower_pre = text.lower()
    _v326_kw_hit = (
        any(kw in text_lower_pre for kw in manual_overrides.EXCLUDE_KEYWORDS)
        or any(kw in text_lower_pre for kw in manual_overrides.INCLUDE_KEYWORDS)
    )
    if _v326_kw_hit:
        action, response = await asyncio.to_thread(manual_overrides.parse_command, text)
        if response:
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {"chat_id": state["admin_id"], "text": response},
            )
        _log.info(f"v326_kw_pre_check action={action} text={text!r}")
        return

    if text.startswith("/"):
        cmd = text.split()[0].lower()
        # TASK B (2026-05-04) /koniec [cid] — manual extended shift termination.
        # Flag MANUAL_KONIEC_COMMAND_ENABLED (default False) → silent early exit.
        if cmd == "/koniec":
            reply = await asyncio.to_thread(
                _handle_koniec_command, state, msg, text,
            )
            if reply is not None:
                await asyncio.to_thread(
                    tg_request, state["token"], "sendMessage",
                    {"chat_id": state["admin_id"], "text": reply},
                )
            return
        # TB-3 (2026-05-05) /poprawa [cid] mirror /koniec — odwołaj "Nie przyjdzie".
        # Flag MANUAL_POPRAWA_COMMAND_ENABLED (default False) → silent early exit.
        if cmd == "/poprawa":
            reply = await asyncio.to_thread(
                _handle_poprawa_command, state, msg, text,
            )
            if reply is not None:
                await asyncio.to_thread(
                    tg_request, state["token"], "sendMessage",
                    {"chat_id": state["admin_id"], "text": reply},
                )
            return
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
        if cmd in ("/help", "/pomoc"):
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {"chat_id": state["admin_id"], "text": _v326_help_text()},
            )
            _log.info(f"/help responded to admin={from_id}")
            return
        # /stop /wraca /wrocil → manual_overrides (handled by parse_command)
        if text.startswith("/dopisz "):
            reply = _handle_dopisz_command(state, msg, text)
            if reply:
                await asyncio.to_thread(
                    tg_request, state["token"], "sendMessage",
                    {
                        "chat_id": msg["chat"]["id"],
                        "text": reply,
                        "reply_to_message_id": msg["message_id"],
                    },
                )
            return

        if cmd in ("/stop", "/wraca", "/wrocil"):
            action, response = await asyncio.to_thread(manual_overrides.parse_command, text)
            if response:
                await asyncio.to_thread(
                    tg_request, state["token"], "sendMessage",
                    {"chat_id": state["admin_id"], "text": response},
                )
            _log.info(f"slash override action={action} text={text!r}")
            return

        if cmd == "/pin":
            reply = await asyncio.to_thread(_handle_pin_command, text)
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {
                    "chat_id": msg["chat"]["id"],
                    "text": reply,
                    "reply_to_message_id": msg["message_id"],
                },
            )
            _log.info(f"/pin from={from_id} text={text!r}")
            return

        if cmd in ("/instrukcja_gps", "/gps", "/instrukcja"):
            reply = await asyncio.to_thread(_handle_gps_instruction_command, text)
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {
                    "chat_id": msg["chat"]["id"],
                    "text": reply,
                    "reply_to_message_id": msg["message_id"],
                    "disable_web_page_preview": True,
                },
            )
            _log.info(f"/instrukcja_gps from={from_id} text={text!r}")
            return
        return

    # NLP assistant — wolny tekst (F2.2)
    text_lower = text.lower().strip()

    # Naturalny fallback dla /pin i /instrukcja_gps (07.05.2026, sprint #5).
    # Strict guards anty-false-positive: max 4 słów, pierwsze musi być "pin"
    # lub "instrukcja". Slash form (/pin, /instrukcja_gps) handled w branch above.
    _words = text_lower.split()
    if 1 <= len(_words) <= 4:
        if _words[0] == "pin" and len(_words) >= 2:
            query = " ".join(text.split()[1:])
            reply = await asyncio.to_thread(_handle_pin_command, f"/pin {query}")
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {
                    "chat_id": msg["chat"]["id"],
                    "text": reply,
                    "reply_to_message_id": msg["message_id"],
                },
            )
            _log.info(f"natural pin from={from_id} text={text!r}")
            return
        if (
            len(_words) >= 2
            and _words[0] in ("instrukcja", "instrukcje")
            and _words[1] in ("gps", "gpsa", "gpsu")
        ):
            tail_words = text.split()[2:]
            tail = " ".join(tail_words)
            reply = await asyncio.to_thread(
                _handle_gps_instruction_command,
                f"/instrukcja_gps {tail}".strip(),
            )
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {
                    "chat_id": msg["chat"]["id"],
                    "text": reply,
                    "reply_to_message_id": msg["message_id"],
                    "disable_web_page_preview": True,
                },
            )
            _log.info(f"natural instrukcja_gps from={from_id} text={text!r}")
            return

    if any(w in text_lower for w in ["pomoc", "help", "komendy", "co umiesz"]):
        help_body = (
            "🤖 Ziomek rozumie:\n"
            "• 'Mykyta nie pracuje' — wyklucza kuriera do końca dnia\n"
            "• 'Mykyta wrócił' — przywraca kuriera\n"
            "• 'reset' — czyści wszystkie wykluczenia\n"
            "• 'kto pracuje' — lista kurierów na zmianie\n"
            "• 'ile zleceń' — statystyki dnia\n"
            "• /pin <imię|cid> — PIN kuriera (też 'pin Marcin')\n"
            "• /instrukcja_gps [imię] — pełna instrukcja onboardingu Android\n"
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
                # Comment Learning Path 1 (TECH_DEBT.md L113, fix 2026-04-30):
                # operator napisał free-form reply (np. "klient odwołał",
                # "wave drop", "deszcz lecial") — capture jako OPERATOR_COMMENT
                # do learning_log zamiast silent drop. Identyczna semantyka jak
                # ENABLE_TELEGRAM_FREETEXT_ASSIGN=False branch niżej (no assign,
                # pending stays for watchdog 5-min auto-KOORD).
                append_learning(state["learning_log_path"], {
                    "ts": now_iso(),
                    "order_id": matched_oid,
                    "action": "OPERATOR_COMMENT",
                    "ok": True,
                    "feedback": f"reply_freeform: {text}",
                    "decision": matched_rec.get("decision_record") or {},
                })
                await asyncio.to_thread(
                    tg_request, state["token"], "sendMessage",
                    {"chat_id": state["admin_id"],
                     "text": f"📝 Komentarz zapisany (#{matched_oid}): {text[:100]}"},
                )
                _log.info(f"OPERATOR_COMMENT (reply_freeform) oid={matched_oid} text={text[:80]!r}")
                return
            courier_name, time_min = parsed
            if time_min is None:
                time_min = compute_assign_time(dr_matched)
            if not ENABLE_TELEGRAM_FREETEXT_ASSIGN:
                # Adrian 2026-04-21: free-text no longer triggers real assign.
                # Log as OPERATOR_COMMENT (operator commentary = ground truth
                # for diagnosis), reply confirming receipt, exit without pop
                # (pending stays for watchdog auto-KOORD 5-min timeout).
                append_learning(state["learning_log_path"], {
                    "ts": now_iso(),
                    "order_id": matched_oid,
                    "action": "OPERATOR_COMMENT",
                    "ok": True,
                    "feedback": f"reply: {text}",
                    "decision": matched_rec.get("decision_record") or {},
                })
                await asyncio.to_thread(
                    tg_request, state["token"], "sendMessage",
                    {"chat_id": state["admin_id"],
                     "text": f"📝 Komentarz zapisany (#{matched_oid}): {text[:100]}"},
                )
                _log.info(f"OPERATOR_COMMENT (reply) oid={matched_oid} text={text[:80]!r}")
                return
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
        if not ENABLE_TELEGRAM_FREETEXT_ASSIGN:
            # Adrian 2026-04-21: free-text no longer triggers real assign.
            append_learning(state["learning_log_path"], {
                "ts": now_iso(),
                "order_id": latest_oid,
                "action": "OPERATOR_COMMENT",
                "ok": True,
                "feedback": f"free_text(latest): {text}",
                "decision": latest_rec.get("decision_record") or {},
            })
            await asyncio.to_thread(
                tg_request, state["token"], "sendMessage",
                {"chat_id": state["admin_id"],
                 "text": f"📝 Komentarz zapisany (#{latest_oid}): {text[:100]}"},
            )
            _log.info(f"OPERATOR_COMMENT (free-text) oid={latest_oid} text={text[:80]!r}")
            return
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

    # Free-text → manual courier overrides (fallthrough po keyword pre-check)
    action, response = await asyncio.to_thread(manual_overrides.parse_command, text)
    if not response:
        return
    await asyncio.to_thread(
        tg_request, state["token"], "sendMessage",
        {"chat_id": state["admin_id"], "text": response},
    )
    _log.info(f"override action={action} text={text!r}")


# ---- TASK B SHIFT NOTIFICATIONS callback handlers (2026-05-04) ----
#
# SHIFT_* callbacks bypass state["pending"] (proposal flow) — używają osobnego
# state file (shift_notifications confirmations.json). Sister module
# `dispatch_v2.shift_notifications` owns the worker; this file owns the
# Telegram-side handlers + templates.
#
# Naming convention: _handle_shift_*_callback są SYNC (wywoływane via
# asyncio.to_thread z handle_callback, żeby uniknąć blocking event loop
# podczas locked file write). Lazy imports defense-in-depth: jeśli sister
# module jeszcze niezbudowany, telegram_approver nadal startuje (pure import
# at module load time would crash whole bot).


def _shift_callback_answer(state: dict, cb: dict, text: str) -> None:
    """Helper — sync answerCallbackQuery z text feedback."""
    try:
        tg_request(
            state["token"], "answerCallbackQuery",
            {"callback_query_id": cb["id"], "text": text},
        )
    except Exception as e:
        _log.warning(f"shift_callback answer failed: {type(e).__name__}: {e}")


def _shift_today_iso() -> str:
    """Warsaw today w formacie YYYY-MM-DD."""
    return datetime.now(WARSAW).strftime("%Y-%m-%d")


def _shift_extract_name_from_key(bucket: dict, rec: dict) -> str:
    """Bucket keys: '{date}:{full_name}'. Find key matching rec by identity."""
    if not isinstance(bucket, dict):
        return "?"
    for key, val in bucket.items():
        if val is rec:
            if isinstance(key, str) and ":" in key:
                return key.split(":", 1)[1]
            return "?"
    return "?"


def _shift_format_scheduled_time(rec: dict) -> str:
    """Extract scheduled HH:MM dla alert messages.

    Worker zapisuje 'scheduled' jako ISO datetime; fallback na 'scheduled_time'
    HH:MM gdyby format zmienił się.
    """
    sched = rec.get("scheduled_time")
    if isinstance(sched, str) and sched:
        return sched
    sched_iso = rec.get("scheduled")
    if isinstance(sched_iso, str) and sched_iso:
        try:
            return datetime.fromisoformat(sched_iso).strftime("%H:%M")
        except Exception:
            return sched_iso
    return "?"


def _handle_f7agree_callback(state: dict, raw_payload: str, cb: dict) -> None:
    """Backlog #12 (2026-05-07): Faza 7-AUTO-PROXIMITY agreement metric.

    Callback format: F7AGREE:{decision}:{order_id} → po top-level split(":",1)
    raw_payload = "{decision}:{order_id}".

    Behavior: log-only side metric. Adrian wskazuje "to powinno być AUTO/ACK/ALERT"
    niezależnie od głównej akcji ASSIGN/INNY/KOORD. Brak editMessage — keyboard
    pozostaje, Adrian może kliknąć inne buttony.

    Logged jako action='F7AGREE' w learning_log.jsonl z polami:
      - human_route: AUTO/ACK/ALERT (z buttonu)
      - shadow_route: AUTO/ACK/ALERT (z decision_record.auto_route)
      - match: human_route == shadow_route
      - auto_route_context: classifier metadata (margin, tier, reason)

    Defense-in-depth: gdy pending entry expired (timeout 5 min) → log z shadow_route=None
    i toast "log only (proposal expired)". NIE crash.
    """
    parts = (raw_payload or "").split(":", 1)
    token = state["token"]
    if len(parts) != 2 or parts[0] not in F7_AGREE_LABELS:
        tg_request(token, "answerCallbackQuery",
                   {"callback_query_id": cb["id"], "text": "❓ malformed F7AGREE"})
        return
    human_route, oid = parts[0], parts[1]
    cb_user_id = str((cb.get("from") or {}).get("id", ""))

    # Lookup decision_record dla shadow_route. Może być None gdy pending expired.
    entry = state["pending"].get(oid)
    rec = (entry or {}).get("decision_record") or {}
    shadow_route = (rec.get("auto_route") or "").upper() or None
    auto_route_context = rec.get("auto_route_context") or {}
    match = (shadow_route == human_route) if shadow_route else None

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": "F7AGREE",
        "order_id": oid,
        "human_route": human_route,
        "shadow_route": shadow_route,
        "match": match,
        "auto_route_context": auto_route_context,
        "from_id": cb_user_id,
    }
    try:
        append_learning(state["learning_log_path"], record)
    except Exception as e:
        _log.warning(f"F7AGREE append_learning fail (non-blocking): {e}")

    if shadow_route:
        match_emoji = "✅" if match else "↔"
        toast = f"{match_emoji} log: {human_route} (system: {shadow_route})"
    else:
        toast = f"📝 log: {human_route} (proposal expired)"
    tg_request(token, "answerCallbackQuery",
               {"callback_query_id": cb["id"], "text": toast})


def _handle_shift_start_callback(state: dict, action: str, cid: str, cb: dict) -> None:
    """SHIFT_START_OK / SHIFT_START_NO — kurier potwierdza/odmawia start zmiany.

    Format callback: "SHIFT_START_OK:{cid}" lub "SHIFT_START_NO:{cid}".
    State: confirmations["start_notified"][today_iso][cid] -> rec.
    Idempotent: jeśli decision już ustawione, drugi click → "już zapisane".
    SHIFT_START_NO: dodatkowo wysyła alert do Bartka (admin chat fallback).
    """
    if not flag("SHIFT_NOTIFY_ENABLED", default=False):
        _shift_callback_answer(state, cb, "wyłączone")
        return
    try:
        from dispatch_v2.shift_notifications.state import (
            locked_write_confirmations,
            find_record_for_cid,
            append_learning_log,
        )
        from dispatch_v2.telegram import templates
    except Exception as e:
        _log.error(
            f"shift_start_callback import failed cid={cid} action={action}: "
            f"{type(e).__name__}: {e}"
        )
        _shift_callback_answer(state, cb, "❌ shift module unavailable")
        return

    today_iso = _shift_today_iso()
    feedback = "✅ ok"
    no_show_payload: Optional[Tuple[str, str]] = None  # (name, scheduled_time)
    try:
        with locked_write_confirmations() as conf:
            start_bucket = conf.setdefault("start_notified", {})
            rec = find_record_for_cid(start_bucket, today_iso, cid)
            if rec is None:
                feedback = f"⚠ Nie znaleziono cid={cid}"
            elif rec.get("decision") is not None:
                feedback = "ℹ już zapisane"
            else:
                if action == "SHIFT_START_OK":
                    rec["decision"] = True
                    rec["confirmed_for_shift"] = True
                    rec["confirmed_at"] = datetime.now(WARSAW).isoformat()
                    feedback = "✅ Potwierdzone"
                else:  # SHIFT_START_NO
                    rec["decision"] = False
                    rec["confirmed_for_shift"] = False
                    rec["declined_at"] = datetime.now(WARSAW).isoformat()
                    feedback = "❌ Odnotowane (Bartek powiadomiony)"
                    no_show_payload = (
                        _shift_extract_name_from_key(start_bucket, rec)
                        or f"cid={cid}",
                        _shift_format_scheduled_time(rec),
                    )
                try:
                    append_learning_log({
                        "event": "SHIFT_START_RESPONSE",
                        "cid": cid,
                        "action": action,
                        "ts": datetime.now(WARSAW).isoformat(),
                    })
                except Exception as e:
                    _log.warning(
                        f"append_learning_log failed cid={cid}: {type(e).__name__}: {e}"
                    )
    except Exception as e:
        _log.error(
            f"shift_start_callback state write failed cid={cid}: "
            f"{type(e).__name__}: {e}"
        )
        feedback = "❌ state write error"

    _shift_callback_answer(state, cb, feedback)

    if no_show_payload is not None:
        name, sched = no_show_payload
        try:
            from dispatch_v2.shift_notifications.telegram_send import (
                tg_send_text_with_keyboard,
            )
            alert_text = templates.format_alert_courier_no_show(name, sched)
            target_chat, route_label = _resolve_bartek_alert_target(state)
            # TB-1 fix (2026-05-05): signature corrected — text positional,
            # inline_keyboard=[] (info-only), chat_id kw. Pre-fix call używał
            # nieistniejących nazw chat_id=/text=/keyboard= co dawało TypeError
            # swallowed przez except (alert nigdy nie wysłany).
            ok = tg_send_text_with_keyboard(alert_text, [], chat_id=target_chat)
            _log.info(
                f"no_show alert cid={cid} route={route_label} "
                f"target={target_chat} ok={ok}"
            )
            if not ok and route_label == "bartek_dm":
                # Auto-fallback to group on DM failure (Bartek blocked bot etc.)
                fallback_chat = int(state["admin_id"])
                tg_send_text_with_keyboard(alert_text, [], chat_id=fallback_chat)
                _log.warning(
                    f"no_show alert DM failed cid={cid}, fell back to group {fallback_chat}"
                )
        except Exception as e:
            _log.error(
                f"no_show alert send failed cid={cid}: {type(e).__name__}: {e}"
            )

    _log.info(f"SHIFT_START callback action={action} cid={cid} → {feedback}")


def _handle_shift_reminder_callback(state: dict, action: str, cid: str, cb: dict) -> None:
    """SHIFT_REMINDER_OK / SHIFT_REMINDER_NO — reminder po brak odpowiedzi."""
    if not flag("SHIFT_NOTIFY_ENABLED", default=False):
        _shift_callback_answer(state, cb, "wyłączone")
        return
    try:
        from dispatch_v2.shift_notifications.state import (
            locked_write_confirmations,
            find_record_for_cid,
            append_learning_log,
        )
    except Exception as e:
        _log.error(
            f"shift_reminder_callback import failed cid={cid}: "
            f"{type(e).__name__}: {e}"
        )
        _shift_callback_answer(state, cb, "❌ shift module unavailable")
        return

    today_iso = _shift_today_iso()
    feedback = "✅ ok"
    try:
        with locked_write_confirmations() as conf:
            bucket = conf.setdefault("start_notified", {})
            rec = find_record_for_cid(bucket, today_iso, cid)
            if rec is None:
                feedback = f"⚠ Nie znaleziono cid={cid}"
            elif rec.get("decision") is not None:
                feedback = "ℹ już zapisane"
            else:
                if action == "SHIFT_REMINDER_OK":
                    rec["decision"] = True
                    rec["confirmed_for_shift"] = True
                    rec["confirmed_at"] = datetime.now(WARSAW).isoformat()
                    rec["confirmed_via_reminder"] = True
                    feedback = "✅ Potwierdzone"
                else:
                    rec["decision"] = False
                    rec["confirmed_for_shift"] = False
                    rec["declined_at"] = datetime.now(WARSAW).isoformat()
                    feedback = "❌ Odnotowane"
                try:
                    append_learning_log({
                        "event": "SHIFT_REMINDER_RESPONSE",
                        "cid": cid,
                        "action": action,
                        "ts": datetime.now(WARSAW).isoformat(),
                    })
                except Exception as e:
                    _log.warning(f"append_learning_log failed cid={cid}: {e}")
    except Exception as e:
        _log.error(
            f"shift_reminder_callback state write failed cid={cid}: "
            f"{type(e).__name__}: {e}"
        )
        feedback = "❌ state write error"

    _shift_callback_answer(state, cb, feedback)
    _log.info(f"SHIFT_REMINDER callback action={action} cid={cid} → {feedback}")


def _handle_shift_end_callback(state: dict, action: str, cid: str, cb: dict) -> None:
    """SHIFT_END_OK / SHIFT_END_EXT — kurier kończy zmianę albo zostaje (extends).

    SHIFT_END_EXT: zmiana extended do północy dnia bieżącego, NO follow-up
    question (per spec). /koniec [cid] command (manual) używa do późniejszego
    flip extended → ended.
    """
    if not flag("SHIFT_NOTIFY_ENABLED", default=False):
        _shift_callback_answer(state, cb, "wyłączone")
        return
    try:
        from dispatch_v2.shift_notifications.state import (
            locked_write_confirmations,
            find_record_for_cid,
            append_learning_log,
        )
    except Exception as e:
        _log.error(
            f"shift_end_callback import failed cid={cid}: {type(e).__name__}: {e}"
        )
        _shift_callback_answer(state, cb, "❌ shift module unavailable")
        return

    today_iso = _shift_today_iso()
    feedback = "✅ ok"
    try:
        with locked_write_confirmations() as conf:
            bucket = conf.setdefault("end_notified", {})
            rec = find_record_for_cid(bucket, today_iso, cid)
            if rec is None:
                feedback = f"⚠ Nie znaleziono cid={cid}"
            elif rec.get("decision") is not None:
                feedback = "ℹ już zapisane"
            else:
                now_w = datetime.now(WARSAW)
                if action == "SHIFT_END_OK":
                    rec["decision"] = True
                    rec["shift_ending_confirmed"] = True
                    rec["ended_at"] = now_w.isoformat()
                    feedback = "✅ Koniec zmiany potwierdzony"
                else:  # SHIFT_END_EXT
                    rec["decision"] = True
                    rec["shift_extended"] = True
                    rec["extended_until"] = today_iso + "T23:59:00"
                    rec["extended_at"] = now_w.isoformat()
                    feedback = "✅ Zmiana przedłużona (do północy)"
                try:
                    append_learning_log({
                        "event": "SHIFT_END_RESPONSE",
                        "cid": cid,
                        "action": action,
                        "ts": now_w.isoformat(),
                    })
                except Exception as e:
                    _log.warning(f"append_learning_log failed cid={cid}: {e}")
    except Exception as e:
        _log.error(
            f"shift_end_callback state write failed cid={cid}: "
            f"{type(e).__name__}: {e}"
        )
        feedback = "❌ state write error"

    _shift_callback_answer(state, cb, feedback)
    _log.info(f"SHIFT_END callback action={action} cid={cid} → {feedback}")


def _resolve_bartek_alert_target(state: dict) -> Tuple[int, str]:
    """TB-1 (2026-05-05): resolve target chat for SHIFT no-show alert.

    Returns (chat_id, route_label) where route_label ∈ {'bartek_dm','group_fallback'}.

    Routing logic:
      - flag COORDINATOR_DM_ROUTING_ENABLED=True AND BARTEK_USER_ID is positive int
        → DM Bartka (alert direct, bypass dispatch noise)
      - else → group fallback state['admin_id']

    BARTEK_USER_ID provided post-Bartek-/start (Adrian extracts from journalctl,
    sets flags.json key). Until then config NULL → fallback group, no behavior
    change vs pre-TB-1.
    """
    cfg = load_flags() or {}
    enabled = bool(cfg.get("COORDINATOR_DM_ROUTING_ENABLED", False))
    bartek_id = cfg.get("BARTEK_USER_ID")
    if enabled and isinstance(bartek_id, int) and bartek_id > 0:
        return bartek_id, "bartek_dm"
    return int(state["admin_id"]), "group_fallback"


def _handle_koniec_command(state: dict, msg: dict, text: str) -> Optional[str]:
    """TASK B (2026-05-04) /koniec [cid] manual termination of extended shift.

    Flag-gated (MANUAL_KONIEC_COMMAND_ENABLED, default False — silent early
    exit). Only KONIEC_AUTHORIZED_USER_IDS can issue. Authorizes via from.id
    NIE chat_id (chat is whole admin group; restrict to specific operators).

    Returns reply text (string) if command was handled (caller should send),
    or None if command did not apply / silently rejected.
    """
    if not flag("MANUAL_KONIEC_COMMAND_ENABLED", default=False):
        return None
    m = KONIEC_RE.match(text.strip())
    if not m:
        return None
    sender_id = (msg.get("from") or {}).get("id")
    if sender_id not in _authorized_user_ids():
        _log.warning(f"/koniec unauthorized sender_id={sender_id}")
        return None  # silent reject
    cid = m.group(1)
    try:
        from dispatch_v2.shift_notifications.state import (
            locked_write_confirmations,
            find_record_for_cid,
            append_learning_log,
        )
    except Exception as e:
        _log.error(f"/koniec import failed cid={cid}: {type(e).__name__}: {e}")
        return f"❌ shift module unavailable cid={cid}"

    today_iso = _shift_today_iso()
    reply = f"⚠ Nie znaleziono cid={cid} w end_notified dziś"
    try:
        with locked_write_confirmations() as conf:
            bucket = conf.setdefault("end_notified", {})
            rec = find_record_for_cid(bucket, today_iso, cid)
            if rec is None:
                reply = f"⚠ Nie znaleziono cid={cid} w end_notified dziś"
            elif not rec.get("shift_extended"):
                reply = f"⚠ cid={cid} nie ma shift_extended=true — /koniec niepotrzebny"
            else:
                now_w = datetime.now(WARSAW)
                rec["shift_ending_confirmed"] = True
                rec["shift_extended"] = False
                rec["terminated_via_koniec_at"] = now_w.isoformat()
                rec["terminated_by"] = str(sender_id)
                try:
                    append_learning_log({
                        "event": "SHIFT_KONIEC_MANUAL",
                        "cid": cid,
                        "ts": rec["terminated_via_koniec_at"],
                        "operator_id": str(sender_id),
                    })
                except Exception as e:
                    _log.warning(f"/koniec append_learning_log failed cid={cid}: {e}")
                reply = f"✅ cid={cid} koniec ustawiony"
    except Exception as e:
        _log.error(f"/koniec state write failed cid={cid}: {type(e).__name__}: {e}")
        reply = f"❌ state write error cid={cid}"
    _log.info(f"/koniec cid={cid} sender={sender_id} → {reply}")
    return reply


def _handle_new_courier_callback(state: dict, payload: str, cb: dict) -> None:
    """NEWCOURIER callback — skip or add (add not used yet, handled via /dopisz)."""
    from urllib.parse import unquote
    parts = payload.split(":", 1)
    if len(parts) != 2:
        _shift_callback_answer(state, cb, "❌ malformed NEWCOURIER")
        return
    sub_action, b64 = parts
    full_name = unquote(b64)
    if sub_action == "skip":
        _shift_callback_answer(state, cb, f"OK pominieto {full_name} dzisiaj")
        # Remove keyboard from original message
        try:
            tg_request(
                state["token"], "editMessageReplyMarkup",
                {
                    "chat_id": state["admin_id"],
                    "message_id": cb["message"]["message_id"],
                    "reply_markup": {"inline_keyboard": []},
                },
            )
        except Exception as e:
            # MP-#10 (2026-05-08): keyboard-clear fail było silent — buttony
            # zostały na message, kolejne kliki SKIP przez admin przepuszczane
            # do kolejki bez audit trail. Log error z full_name + msg_id żeby
            # operator widział lost audit (i mógł manually edycjnąć keyboard).
            msg_id = (cb.get("message") or {}).get("message_id", "?")
            _log.error(
                f"_handle_new_courier_callback skip: editMessageReplyMarkup fail "
                f"full_name={full_name!r} msg_id={msg_id} ({type(e).__name__}: {e}). "
                f"Keyboard NIE wyczyszczony — kolejne SKIP klikane będą jako noop."
            )
    elif sub_action == "add":
        _shift_callback_answer(state, cb, "Uzyj /dopisz <cid> <full_name>")
    else:
        _shift_callback_answer(state, cb, f"❌ unknown NEWCOURIER sub: {sub_action}")


def _handle_pin_command(text: str) -> str:
    """/pin <imie|cid> — zwroc PIN kuriera + cid + nazwe canonical.

    Best-effort, no auth gate (grupa ziomka i tak whitelisted po chat_id).
    Per Lekcja #79 — PIN broadcast w grupie (4 czlonkowie) jest akceptowalnym
    audit-trail compromise.
    """
    from dispatch_v2 import courier_info as _ci
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return (
            "❌ Użycie: /pin <imię> lub /pin <cid>\n"
            "Przykład: /pin Marcin   |   /pin 393"
        )
    query = parts[1].strip()
    name, cid, pin, ambig = _ci.resolve_courier_query(query)
    if ambig:
        return _ci.format_ambiguous_response(query, ambig)
    if name is None or cid is None:
        return _ci.format_not_found_response(query)
    return _ci.format_pin_response(name, cid, pin)


def _handle_gps_instruction_command(text: str) -> str:
    """/instrukcja_gps [imie|cid] — pelna instrukcja onboardingu Android GPS.

    Bez argumentu → template ogolny do skopiowania.
    Z argumentem → spersonalizowana wersja z imieniem + PIN-em
    (gotowa do forwardu kurierowi).
    """
    from dispatch_v2 import courier_info as _ci
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return _ci.format_gps_instruction(name=None, pin=None)
    query = parts[1].strip()
    name, cid, pin, ambig = _ci.resolve_courier_query(query)
    if ambig:
        return _ci.format_ambiguous_response(query, ambig)
    if name is None:
        return (
            f"❌ Nie znaleziono kuriera '{query}' — wysyłam template ogólny:\n\n"
            + _ci.format_gps_instruction(name=None, pin=None)
        )
    return _ci.format_gps_instruction(name=name, pin=pin)


def _handle_dopisz_command(state: dict, msg: dict, text: str) -> Optional[str]:
    """/dopisz <cid> <full_name> — atomic add new courier to roster."""
    user_id = (msg.get("from") or {}).get("id")
    if user_id not in _authorized_user_ids():
        return "❌ unauthorized"
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 3:
        return "❌ uzycie: /dopisz <cid> <full_name>"
    _, cid_str, full_name = parts
    if not cid_str.isdigit():
        return f"❌ cid musi byc liczba, dostalem: {cid_str}"
    cid = int(cid_str)
    if cid < 100 or cid > 9999:
        return f"❌ cid {cid} poza zakresem 100..9999"
    try:
        from dispatch_v2.courier_admin import add_new_courier
        result = add_new_courier(cid, full_name)
    except ValueError as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ blad: {type(e).__name__}: {e}"
    return (
        f"✅ Dopisany {result['full_name']} (cid {result['cid']})\n"
        f"Alias: {result['alias']}\n"
        f"PIN: `{result['pin']}` — przeslij temu kurierowi.\n"
        f"Zaktualizowane pliki: kurier_ids, kurier_full_names, kurier_piny, courier_tiers."
    )


def _handle_poprawa_command(state: dict, msg: dict, text: str) -> Optional[str]:
    """TB-3 (2026-05-05) /poprawa [cid] — odwołaj 'Nie przyjdzie' status.

    Mirror /koniec: flag-gated (MANUAL_POPRAWA_COMMAND_ENABLED, default False).
    Authorization via from.id ∈ KONIEC_AUTHORIZED_USER_IDS (same group as /koniec).

    Use case: kurier kliknął "Nie przyjdzie" w T-60 START callback (lub
    unconfirmed_default flipped na T-0), ale mimo wszystko przyszedł — koordynator
    musi to odwołać żeby Ziomek mógł go używać w propozycjach.

    Mutation w start_notified bucket:
      - decision: False → True
      - confirmed_for_shift: False → True
      - unconfirmed_default: → False (clear if was set)
      - reverted_via_poprawa_at: now_iso (Warsaw)
      - reverted_by: sender_id

    Idempotent: jeśli już True → "ℹ już potwierdzony" (no mutation).
    Format invalid → silent (mirror /koniec).
    Returns reply text or None (silent rejected).
    """
    if not flag("MANUAL_POPRAWA_COMMAND_ENABLED", default=False):
        return None
    m = POPRAWA_RE.match(text.strip())
    if not m:
        return None
    sender_id = (msg.get("from") or {}).get("id")
    if sender_id not in _authorized_user_ids():
        _log.warning(f"/poprawa unauthorized sender_id={sender_id}")
        return None  # silent reject
    cid = m.group(1)
    try:
        from dispatch_v2.shift_notifications.state import (
            locked_write_confirmations,
            find_record_for_cid,
            append_learning_log,
        )
    except Exception as e:
        _log.error(f"/poprawa import failed cid={cid}: {type(e).__name__}: {e}")
        return f"❌ shift module unavailable cid={cid}"

    today_iso = _shift_today_iso()
    reply = f"⚠ Nie znaleziono cid={cid} w start_notified dziś"
    try:
        with locked_write_confirmations() as conf:
            bucket = conf.setdefault("start_notified", {})
            rec = find_record_for_cid(bucket, today_iso, cid)
            if rec is None:
                reply = f"⚠ Nie znaleziono cid={cid} w start_notified dziś"
            else:
                full_name = _shift_extract_name_from_key(bucket, rec) or f"cid={cid}"
                if rec.get("decision") is True and rec.get("confirmed_for_shift") is True:
                    reply = f"ℹ {full_name} ({cid}): już potwierdzony, /poprawa niepotrzebny"
                elif rec.get("decision") is not False:
                    # decision=None (undecided) — /poprawa nie pasuje (use TAK button)
                    reply = (
                        f"⚠ {full_name} ({cid}): nie ma 'Nie przyjdzie' "
                        f"— /poprawa niepotrzebny"
                    )
                else:
                    now_w = datetime.now(WARSAW)
                    rec["decision"] = True
                    rec["confirmed_for_shift"] = True
                    rec["unconfirmed_default"] = False
                    rec["reverted_via_poprawa_at"] = now_w.isoformat()
                    rec["reverted_by"] = str(sender_id)
                    try:
                        append_learning_log({
                            "event": "SHIFT_NOTIFICATION",
                            "decision": "MANUAL_POPRAWA",
                            "cid": cid,
                            "ts": rec["reverted_via_poprawa_at"],
                            "operator_id": str(sender_id),
                        })
                    except Exception as e:
                        _log.warning(
                            f"/poprawa append_learning_log failed cid={cid}: {e}"
                        )
                    reply = (
                        f'✅ {full_name} ({cid}): status zmieniony z "Nie przyjdzie" '
                        f'na "Potwierdzony". Ziomek może go używać w propozycjach.'
                    )
    except Exception as e:
        _log.error(f"/poprawa state write failed cid={cid}: {type(e).__name__}: {e}")
        reply = f"❌ state write error cid={cid}"
    _log.info(f"/poprawa cid={cid} sender={sender_id} → {reply}")
    return reply


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

    # V3.19i (2026-04-30): INNY callback format "INNY:{reason_code}:{order_id}"
    # → po split(":",1) zmienna `oid` zawiera "{reason_code}:{order_id}".
    # Backward-compat: legacy 2-segment "INNY:{order_id}" → reason_code='legacy_inny'.
    inny_reason_code: str = ""
    if action == "INNY":
        parts = oid.split(":")
        if len(parts) == 2:
            inny_reason_code = parts[0]
            oid = parts[1]
            if (
                inny_reason_code not in TG_REASON_CODES
                and inny_reason_code != "legacy_inny"
            ):
                _log.warning(
                    f"unknown INNY reason_code={inny_reason_code!r} oid={oid} "
                    f"— treating as 'other'"
                )
                inny_reason_code = "other"
        elif len(parts) == 1:
            # legacy callback (pre-V3.19i) — 1 segment after top-level split
            inny_reason_code = "legacy_inny"
            _log.info(f"legacy INNY callback oid={oid} (no reason_code)")
        else:
            _log.warning(
                f"malformed INNY callback raw='INNY:{oid}' — treating as 'other'"
            )
            inny_reason_code = "other"
            oid = parts[-1]  # best-effort: trailing segment as oid

    # Security: weryfikacja chat_id + logowanie from_id (F2.2)
    # TASK B Phase 2 fix (2026-05-05): SHIFT_* callbacks mogą iść z DM
    # (worker target = ADRIAN_CHAT_ID_FALLBACK, NIE state["admin_id"] grupy).
    # Legacy ASSIGN/INNY/KOORD bez zmian — zawsze grupa.
    cb_chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
    cb_from_id = str((cb.get("from") or {}).get("id", ""))
    cb_from_name = (cb.get("from") or {}).get("first_name", "?")
    _SHIFT_TASKB_PREFIXES = ("SHIFT_START_", "SHIFT_END_", "SHIFT_REMINDER_")
    is_taskb_action = action.startswith(_SHIFT_TASKB_PREFIXES)
    cb_user_id_int = int(cb_from_id) if cb_from_id.isdigit() else None
    is_authorized = (
        cb_chat_id == str(state["admin_id"])
        or (is_taskb_action and cb_user_id_int in _authorized_user_ids())
    )
    if not is_authorized:
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

    # NEWCOURIER callback (ETAP B)
    if action == "NEWCOURIER":
        await asyncio.to_thread(_handle_new_courier_callback, state, oid, cb)
        return

    # TASK B SHIFT NOTIFICATIONS (2026-05-04): SHIFT_* callbacks NIE używają
    # state["pending"] (osobny state file: shift_notifications confirmations).
    # Short-circuit przed pending lookup, fork do dedicated handlers.
    if action in ("SHIFT_START_OK", "SHIFT_START_NO"):
        await asyncio.to_thread(
            _handle_shift_start_callback, state, action, oid, cb,
        )
        return
    if action in ("SHIFT_REMINDER_OK", "SHIFT_REMINDER_NO"):
        await asyncio.to_thread(
            _handle_shift_reminder_callback, state, action, oid, cb,
        )
        return
    if action in ("SHIFT_END_OK", "SHIFT_END_EXT"):
        await asyncio.to_thread(
            _handle_shift_end_callback, state, action, oid, cb,
        )
        return

    # TASK A CZASÓWKI PROACTIVE (2026-05-05): CZAS_* callbacks NIE używają
    # state["pending"] (osobny state file: czasowka_proposals_state.json).
    # Short-circuit przed pending lookup, fork do dedicated handlers.
    # raw oid z router = "{oid}:{cid}:{trigger_min}" — split w handlerze.
    if action in ("CZAS_TAK", "CZAS_NIE", "CZAS_CZEKAJ"):
        from dispatch_v2.czasowka_proactive import handlers as czas_handlers
        handler_map = {
            "CZAS_TAK": czas_handlers.handle_czas_tak,
            "CZAS_NIE": czas_handlers.handle_czas_nie,
            "CZAS_CZEKAJ": czas_handlers.handle_czas_czekaj,
        }
        await asyncio.to_thread(handler_map[action], state, action, oid, cb)
        return

    # Backlog #12 (2026-05-07): Faza 7 agreement metric buttons.
    # Callback format: F7AGREE:{AUTO|ACK|ALERT}:{order_id}.
    # Po split(":",1) `oid` zawiera "{decision}:{order_id}". Log only, NIE finalizuje
    # propozycji (brak editMessage). Adrian może kliknąć ASSIGN/INNY/KOORD niezależnie.
    if action == "F7AGREE":
        await asyncio.to_thread(_handle_f7agree_callback, state, oid, cb)
        return

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
        # V3.19i (2026-04-30): structured reason capture. Chosen-courier
        # follow-up DEFERRED do V3.19j. Adrian after click: panel-first
        # workflow continues, panel_watcher detect-uje finalny PANEL_OVERRIDE.
        reason_label = TG_REASON_CODES.get(inny_reason_code, inny_reason_code)
        ok = True
        feedback = f"🔄 INNY ({reason_label}) — zapisane, wybierz w panelu"
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
    # V3.19i (2026-04-30): include reason_code w INNY entries (action=INNY w
    # log_rec). Dodatkowy TG_REASON entry emit-owany po umbrella INNY entry —
    # downstream analyzery (Sprint 2) mogą filter po action='TG_REASON' bez
    # touching legacy INNY entries.
    if action == "INNY":
        log_rec["reason_code"] = inny_reason_code
        log_rec["proposed_courier_id"] = str((best or {}).get("courier_id") or "")
    append_learning(state["learning_log_path"], log_rec)
    if action == "INNY":
        try:
            tg_reason_rec = {
                "ts": now_iso(),
                "order_id": oid,
                "action": "TG_REASON",
                "reason_code": inny_reason_code,
                "operator": cb_from_name,
                "operator_id": cb_from_id,
                "proposed_courier_id": str((best or {}).get("courier_id") or ""),
                "proposed_courier_name": (best or {}).get("name"),
                "proposed_score": (best or {}).get("score"),
                "decision": rec,
            }
            append_learning(state["learning_log_path"], tg_reason_rec)
        except Exception as e:
            _log.warning(
                f"TG_REASON emit failed oid={oid} reason={inny_reason_code!r}: "
                f"{type(e).__name__}: {e}"
            )
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


async def _process_expired_pending(
    state: dict, oid: str, entry: dict, now: datetime, state_all: dict
) -> str:
    """Per-entry expired-handling shared między watchdog() a A2 startup scan.

    Returns: "SUPERSEDED" (status != "new" w state_machine — silent skip) lub
    "TIMEOUT" (real brak decyzji — Telegram alert + learning_log + remove).
    Idempotent: caller usuwa entry z `state["pending"]` po dispatch'u.

    A2 (audit STATE_OWNERSHIP F9 2026-05-08): wyciągnięte z watchdog() pętli
    żeby _startup_scan_pending_expired mógł reuse'ować tę samą logikę bez
    duplikacji (Lekcja #99).
    """
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
        return "SUPERSEDED"
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
    return "TIMEOUT"


async def _startup_scan_pending_expired(state: dict) -> dict:
    """A2 (audit STATE_OWNERSHIP F9 2026-05-08): force-process pending sieroty
    z `expires_at` w przeszłości NATYCHMIAST przy starcie, PRZED launch
    workers. Eliminuje window operatorskiej confusion gdy crash/restart
    zostawił expired pending — bez tego scanu czeka do pierwszego watchdog
    cycle (sleep=10s).

    Returns dict summary {total, expired, processed, superseded, timeout}.
    Defense-in-depth: state_machine load fail → empty state_all (treats all
    expired jako TIMEOUT path, real brak decyzji).
    """
    now = datetime.now(timezone.utc)
    expired = []
    for oid, entry in list(state["pending"].items()):
        try:
            exp = datetime.fromisoformat(entry["expires_at"])
        except Exception as _e:
            _log.warning(f"startup scan parse expires_at fail oid={oid}: {_e}")
            continue
        if now >= exp:
            expired.append(oid)
    summary = {
        "total": len(state["pending"]),
        "expired": len(expired),
        "processed": 0,
        "superseded": 0,
        "timeout": 0,
    }
    if not expired:
        _log.info(f"startup pending scan: total={summary['total']} expired=0")
        return summary
    state_all = {}
    try:
        from dispatch_v2 import state_machine
        state_all = state_machine.get_all()
    except Exception as _e:
        _log.warning(f"startup scan state_machine load fail: {_e}")
    for oid in expired:
        entry = state["pending"].get(oid)
        if not entry:
            continue
        try:
            action = await _process_expired_pending(state, oid, entry, now, state_all)
        except Exception as _e:
            _log.error(f"startup scan _process_expired_pending fail oid={oid}: {_e}")
            continue
        summary["processed"] += 1
        if action == "SUPERSEDED":
            summary["superseded"] += 1
        elif action == "TIMEOUT":
            summary["timeout"] += 1
    _log.info(
        f"startup pending scan: total={summary['total']} "
        f"expired={summary['expired']} processed={summary['processed']} "
        f"superseded={summary['superseded']} timeout={summary['timeout']}"
    )
    return summary


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
            await _process_expired_pending(state, oid, entry, now, state_all)
        await asyncio.sleep(10)


# ---- main ----

async def _shutdown_drain(state: dict) -> None:
    """MP-#10 (2026-05-08): final flush pending state przed exit. Idempotent.

    Eliminuje race window 50µs między state mutation (proposal_sender mutuje
    `state['pending']` po sendMessage) a save_pending (atomic write na disk).
    Bez drain SIGTERM mid-mutation = restart load_pending() zwraca state sprzed
    mutation → user klika ASSIGN → KeyError w handle_callback. Per audit
    TELEGRAM_APPROVER §2 P×I=8 (META audit twierdził 20, fact-checked do 8).

    Lekcja #32 — log success+fail context, NIGDY silent.
    """
    try:
        save_pending(state["pending_path"], state["pending"])
        _log.info(f"shutdown drain: pending={len(state['pending'])} flushed")
    except Exception as e:
        _log.error(f"shutdown drain FAIL ({type(e).__name__}: {e})")


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

    # A2 (audit STATE_OWNERSHIP F9): force-process expired sieroty PRZED gather.
    # Eliminuje window gdy crash/restart zostawił pending z expires_at w
    # przeszłości — bez tego scanu czeka do pierwszego watchdog cycle (10s).
    try:
        await _startup_scan_pending_expired(state)
    except Exception as _e:
        _log.error(f"startup scan FAIL ({type(_e).__name__}: {_e}) — kontynuuję normalny start")

    # MP-#10: try/finally drain — gathered tasks mogą rzucić CancelledError przy
    # SIGTERM; finally MUSI fire żeby zapisać pending przed exit.
    try:
        await asyncio.gather(
            shadow_tailer(state),
            proposal_sender(state),
            updates_poller(state),
            watchdog(state),
        )
    finally:
        await _shutdown_drain(state)


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
