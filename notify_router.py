"""Centrum powiadomień — klasyfikacja + routing alertów Telegram.

ZAKRES (uzgodniony z Adrianem 2026-06-14):
- NIE dotyka strumienia propozycji KOORD (telegram_approver.proposal_sender).
  KOORD zostaje na głównym bocie bez zmian.
- Działa WYŁĄCZNIE na alertach idących przez telegram_utils.send_admin_alert
  (sla_tracker, parse_continuity_guard, observability.alert_onfailure,
  daily_briefing, courier_ranking, ...). To jest pojedynczy choke-point dla
  ~512 WARNING/dzień + alerty infra + briefingi.

Routing:
- HIGH → główny bot Telegram (zachowanie bez zmian) + zapis do feedu (archiwum).
- LOW  → NIE idzie na główny bot; trafia na CICHY bot (@DajeszBot, asystent)
         + feed (kafel w gps.nadajesz.pl/admin).

Klasyfikacja: config dispatch_state/notify_routing.json (hot-reload). Fail-safe:
gdy tekst nie pasuje do żadnej listy → HIGH (NIGDY nie chowamy potencjalnie
ważnego alertu — lepiej jeden dzwonek za dużo niż przegapiona awaria).

Gating: flaga ENABLE_NOTIFY_PRIORITY_ROUTING (flags.json, default OFF).
- OFF: feed nadal zapisywany (additive, bezpieczny — kafel widzi dane od razu),
       ale NIC nie jest odcinane od głównego bota, cichy bot nie dostaje kopii.
       = pełne zachowanie legacy (zero production impact).
- ON:  LOW odcinane od głównego bota → cichy bot + feed.

Lekcje projektu: atomic writes (temp+fsync+rename dla trim), urllib-only (brak
requests w venv), pytest guard (Lekcja #75 — żaden realny send w testach).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

WARSAW = ZoneInfo("Europe/Warsaw")

_STATE_DIR = Path("/root/.openclaw/workspace/dispatch_state")
FEED_PATH = _STATE_DIR / "notify_feed.jsonl"
CONFIG_PATH = _STATE_DIR / "notify_routing.json"
_ASSISTANT_ENV_PATH = "/root/.openclaw/workspace/.secrets/assistant_telegram.env"

# Cap rozrostu feedu: gdy przekroczy próg, trim do ostatnich N wpisów.
_FEED_MAX_BYTES = 4_000_000
_FEED_TRIM_KEEP = 2000

# Fallback config (gdy plik notify_routing.json nie istnieje). Adrian tuninguje
# przez edycję pliku JSON — hot-reload, bez restartu.
_DEFAULT_CONFIG = {
    # HIGH wygrywa gdy KTÓRYKOLWIEK keyword pasuje (sprawdzane PRZED low).
    "high_keywords": [
        "onfailure", "awaria", "awarii", "padł", "padla", "failed", "fail (",
        "exit=", "result=", "severity: p0", "severity: p1", "🔴", "🚨",
        "nowy kurier", "sparuj", "/nowy", "niesparowany",
        "płatnoś", "platnos", "p24", "iban", "wypłat", "wyplat",
        "restauracj", "krytycz", "traceback", "exception",
    ],
    # LOW gdy żaden high nie pasował, a któryś low tak.
    "low_keywords": [
        "briefing", "podsumowanie", "statystyk", "raport dzienny", "raport eta",
        "divergence", "rozjazd", "ℹ", "info:", "warning", "ostrzeżenie",
        "kalibrac", "shadow", "heartbeat", "tick ok", "pre-warning",
        "ranking", "obserwac", "kandydat", "proximity",
    ],
    # Gdy nic nie pasuje — bezpiecznie HIGH.
    "default_priority": "high",
}

_config_cache: dict | None = None
_config_mtime: float = 0.0


def _load_config() -> dict:
    """Hot-reload notify_routing.json; fallback do _DEFAULT_CONFIG."""
    global _config_cache, _config_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except FileNotFoundError:
        if _config_cache is None:
            _config_cache = dict(_DEFAULT_CONFIG)
        return _config_cache
    if _config_cache is None or mtime > _config_mtime:
        try:
            with open(CONFIG_PATH) as f:
                loaded = json.load(f)
            # merge z defaultem (brakujące klucze → default)
            cfg = dict(_DEFAULT_CONFIG)
            cfg.update({k: v for k, v in loaded.items() if v is not None})
            _config_cache = cfg
            _config_mtime = mtime
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"notify_routing.json nieczytelny ({e}); fallback default")
            if _config_cache is None:
                _config_cache = dict(_DEFAULT_CONFIG)
    return _config_cache


def classify(text: str, source: str | None = None) -> str:
    """Zwróć 'high' lub 'low'. Fail-safe: nieznane → high.

    source (opcjonalny) pozwala callerowi wymusić priorytet przez config
    source_priority map (np. alert_onfailure → high niezależnie od treści).
    """
    cfg = _load_config()
    src_map = cfg.get("source_priority", {})
    if source and source in src_map:
        p = str(src_map[source]).lower()
        if p in ("high", "low"):
            return p
    low_text = (text or "").lower()
    for kw in cfg.get("high_keywords", []):
        if kw in low_text:
            return "high"
    for kw in cfg.get("low_keywords", []):
        if kw in low_text:
            return "low"
    dp = str(cfg.get("default_priority", "high")).lower()
    return "low" if dp == "low" else "high"


def _append_feed(text: str, priority: str, source: str | None,
                 sent_main: bool, sent_silent: bool) -> None:
    """Dopisz wpis do feedu (kafel panelu czyta tail). Best-effort, nigdy nie
    wysadza ścieżki alertu."""
    try:
        entry = {
            "ts": datetime.now(WARSAW).isoformat(timespec="seconds"),
            "priority": priority,
            "source": source or "",
            "text": (text or "")[:2000],
            "sent_main": sent_main,
            "sent_silent": sent_silent,
        }
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(FEED_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _maybe_trim_feed()
    except OSError as e:
        log.warning(f"notify_router: feed write fail: {e}")


def _maybe_trim_feed() -> None:
    """Cap rozrostu — trim do ostatnich _FEED_TRIM_KEEP wpisów (atomic rename)."""
    try:
        if FEED_PATH.stat().st_size <= _FEED_MAX_BYTES:
            return
        with open(FEED_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        keep = lines[-_FEED_TRIM_KEEP:]
        fd, tmp = tempfile.mkstemp(dir=str(_STATE_DIR), prefix=".notify_feed_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(keep)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, FEED_PATH)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError as e:
        log.warning(f"notify_router: feed trim fail: {e}")


def _send_silent(text: str) -> bool:
    """Wyślij na CICHY bot (asystent @DajeszBot). urllib-only przez
    telegram_approver.tg_request. Zwraca True gdy ok=True."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("ALLOW_TELEGRAM_IN_TEST"):
        log.warning("notify_router._send_silent blocked (pytest)")
        return False
    # lazy import — unika cyklu telegram_utils → notify_router → telegram_approver
    from dispatch_v2 import telegram_approver
    env = telegram_approver._load_env(_ASSISTANT_ENV_PATH)
    token = env.get("ASSISTANT_TELEGRAM_TOKEN", "")
    chat_id = env.get("ASSISTANT_TELEGRAM_ADMIN_ID", "")
    if not token or not chat_id:
        log.warning("notify_router: brak ASSISTANT_TELEGRAM_TOKEN/ADMIN_ID — cichy bot off")
        return False
    body = f"🔕 {text}"
    r = telegram_approver.tg_request(token, "sendMessage", {"chat_id": chat_id, "text": body})
    if not r.get("ok"):
        log.warning(f"notify_router: cichy bot send fail: {r.get('error') or r.get('description')}")
        return False
    return True


def route(text: str, source: str | None = None, priority: str | None = None) -> bool:
    """Zaklasyfikuj + zrutuj alert. Zwraca True gdy główny bot MA wysłać
    (HIGH lub flaga OFF), False gdy LOW przejęte przez cichy bot + feed.

    Wołane z telegram_utils.send_admin_alert. Zawsze zapisuje wpis do feedu.
    """
    from dispatch_v2.common import flag

    pri = (priority or "").lower()
    if pri not in ("high", "low"):
        pri = classify(text, source)

    flag_on = flag("ENABLE_NOTIFY_PRIORITY_ROUTING", default=False)
    sent_silent = False
    proceed_main = True

    if flag_on and pri == "low":
        sent_silent = _send_silent(text)
        proceed_main = False  # odetnij od głównego bota

    _append_feed(text, pri, source, sent_main=proceed_main, sent_silent=sent_silent)
    return proceed_main
