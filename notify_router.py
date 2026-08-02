"""Centrum powiadomień — klasyfikacja + routing alertów Telegram.

ZAKRES (uzgodniony z Adrianem 2026-06-14):
- NIE dotyka strumienia propozycji KOORD (telegram_approver.proposal_sender).
  KOORD zostaje na głównym bocie bez zmian.
- Działa WYŁĄCZNIE na alertach idących przez telegram_utils.send_admin_alert
  (sla_tracker, parse_continuity_guard, observability.alert_onfailure,
  daily_briefing, courier_ranking, ...). Pojedynczy choke-point.

DWA ORTOGONALNE WYMIARY:
1. priority ∈ {high, low}  (LEGACY, bez zmian — decyduje głośny vs cichy).
2. category ∈ {technical, critical, info}  (od 2026-08-02 — decyduje KTÓRY kanał):
     - info      = cichy bot @DajeszBot + kafel panelu.
     - technical = kanał NAPRAW (infra/health).
     - critical  = główna grupa Adriana (biznes + fail-safe).

KLASYFIKACJA KATEGORII = SOURCE-PRIMARY (owner 02.08 — decyzja policy zamykająca
dwie rundy defektów keyword-precedence). ŹRÓDŁO decyduje deterministycznie:
  1. source ∈ _SOURCE_TECHNICAL → 'technical' (→ NAPRAW); TREŚĆ biznesowa z WĄSKIEGO
     zbioru _BUSINESS_ESCALATION ESKALUJE technical → 'critical' (NIGDY odwrotnie).
  2. source ∈ _SOURCE_CRITICAL  → 'critical'.
  3. treść biznesowa (wąski zbiór) → 'critical' — nawet przy nieznanym źródle i
     jawnym priority=low (biznesu nie da się schować).
  4. source nieznane/puste, nie-biznes: priorytet LOW → 'info'; inaczej → 'critical'.
  Wątpliwe/kolizja/nieznany HIGH → 'critical' (fail-safe do ownera). Mapy source i
  zbiór eskalacji są ZAHARDKODOWANE (nie z hot-configu) — to była przyczyna obu
  rund defektów. Treść eskaluje TYLKO w górę (technical→critical), NIGDY w dół.
  ⚠ Zbiór eskalacji jest WĄSKI (płatność/payment/P24/gateway, restauracja, nowy
  kurier) — świadomie BEZ szerokiego „zamówienie" (to był P1 regresji).

Klasyfikacja priority (high/low) — config notify_routing.json (hot-reload).

Gating — TRZY niezależne flagi (flags.json, wszystkie default OFF):
- ENABLE_NOTIFY_PRIORITY_ROUTING:     info → cichy bot + feed.
- ENABLE_NOTIFY_CHANNEL_SPLIT:        technical → kanał NAPRAW.
- ENABLE_NOTIFY_CHANNEL_SPLIT_SHADOW: dopisuje category do feedu bez routingu (obserwacja).
BYTE-PARITY: przy OFF/OFF/OFF (wszystkie trzy OFF) nowy classifier NIE JEST WYKONYWANY,
a feed = DOKŁADNIE legacy schema (ts/priority/source/text/sent_main/sent_silent).
category+sent_repairs dodawane TYLKO gdy split ON lub shadow ON.
Fail-open: transport kanału pobocznego zawiedzie → alert wraca na główną grupę.
Critical NIGDY nie jest odcinany (ani na cichy, ani na NAPRAW).

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
# Kanał NAPRAW (technical). Owner wpina wartości w telegram_bots.env. TOKEN opcjonalny
# (fallback na główny bot na osobną grupę); CHAT_ID wymagany. Bez CHAT_ID → _send_repairs
# zwraca False → fail-open na główną grupę (alert nie ginie).
_REPAIRS_ENV_PATH = "/root/.openclaw/workspace/.secrets/telegram_bots.env"
_MAIN_ENV_PATH = "/root/.openclaw/workspace/.secrets/telegram.env"
_REPAIRS_TOKEN_KEY = "REPAIRS_BOT_TOKEN"
_REPAIRS_CHAT_KEY = "REPAIRS_CHAT_ID"

# ── SOURCE-PRIMARY: mapy źródło→kategoria (HARDKOD, deterministyczne, owner 02.08) ──
# Źródła TECHNICZNE (infra/health) → kanał NAPRAW.
_SOURCE_TECHNICAL = frozenset({
    "alert_onfailure",
    "backup_sentinel",
    "data_alerts",
    "parser_health",
    "parser_health_layer3",
    "liveness_probe",
    "detector_419",
    "osrm_client",
    "state_panel_monitor",
})
# Źródła BIZNESOWO-KRYTYCZNE → główna grupa (owner). (Reszta biznesu i tak wpada
# w critical przez fail-safe / eskalację treści; ta mapa to jawna intencja.)
_SOURCE_CRITICAL = frozenset({
    "new_courier_pairing",
})
# TREŚĆ eskaluje technical→critical WYŁĄCZNIE na WĄSKI, wysokopewny zbiór biznesowy
# (owner 02.08). Case-insensitive (substring na .lower()). ⚠ NIGDY szerokie słowa
# typu „zamówienie/zamówieni" — to był P1 regresji. Eskalacja tylko w górę.
_BUSINESS_ESCALATION = (
    "płatnoś", "platnos", "payment", "p24", "gateway",
    "restauracj", "restaurant",
    "nowy kurier", "new courier", "new_courier",
)

# Cap rozrostu feedu: gdy przekroczy próg, trim do ostatnich N wpisów.
_FEED_MAX_BYTES = 4_000_000
_FEED_TRIM_KEEP = 2000

# Fallback config priorytetu (gdy plik notify_routing.json nie istnieje). Adrian
# tuninguje przez edycję JSON — hot-reload. Dotyczy TYLKO classify() (high/low);
# kategoria (technical/critical/info) jest source-primary i NIE z configu.
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
    source_priority map (np. daily_briefing → low niezależnie od treści).
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


def _is_business_escalation(low_text: str) -> bool:
    """Czy treść zawiera WĄSKI, wysokopewny sygnał biznesowy (eskalacja do ownera)."""
    return any(kw in low_text for kw in _BUSINESS_ESCALATION)


def classify_category(text: str, source: str | None = None,
                      priority: str | None = None) -> str:
    """Zwróć kanał: 'technical' | 'critical' | 'info'. SOURCE-PRIMARY (owner 02.08).

    Precedencja deterministyczna (mapy ZAHARDKODOWANE, nie z hot-configu):
      1. source ∈ _SOURCE_TECHNICAL → 'technical'; treść biznesowa (wąski zbiór)
         ESKALUJE → 'critical' (nigdy odwrotnie).
      2. source ∈ _SOURCE_CRITICAL  → 'critical'.
      3. treść biznesowa (wąski zbiór) → 'critical' — nawet przy nieznanym źródle
         i jawnym priority=low (biznesu nie da się schować; kill-test P0-2).
      4. source nieznane, nie-biznes: LOW → 'info'; inaczej → 'critical' (fail-safe).

    Treść eskaluje TYLKO w górę (technical→critical). Nieznane/kolizja/nieznany HIGH
    → 'critical' (do ownera). priority (opcjonalny) — high/low dla ścieżki nieznanego
    źródła; jeśli caller już policzył, przekaż dla spójności.
    """
    src = (source or "").strip()
    low_text = (text or "").lower()
    business = _is_business_escalation(low_text)

    # 1. Źródło techniczne → NAPRAW; treść biznesowa eskaluje do ownera.
    if src in _SOURCE_TECHNICAL:
        return "critical" if business else "technical"
    # 2. Źródło jawnie biznesowe → owner.
    if src in _SOURCE_CRITICAL:
        return "critical"
    # 3. Treść biznesowa → owner (nieznane źródło, także przy priority=low).
    if business:
        return "critical"
    # 4. Nieznane źródło, nie-biznes: LOW → info, inaczej fail-safe critical.
    pri = (priority or "").lower()
    if pri not in ("high", "low"):
        pri = classify(text, source)
    if pri == "low":
        return "info"
    return "critical"


def _append_feed(text: str, priority: str, source: str | None,
                 sent_main: bool, sent_silent: bool,
                 category: str = "", sent_repairs: bool = False,
                 include_split_fields: bool = False) -> None:
    """Dopisz wpis do feedu (kafel panelu czyta tail). Best-effort, nigdy nie
    wysadza ścieżki alertu.

    BYTE-PARITY (kill-test P1-5): przy OFF/OFF/OFF schemat = DOKŁADNIE legacy
    (ts/priority/source/text/sent_main/sent_silent). Klucze category/sent_repairs
    dodawane WYŁĄCZNIE gdy include_split_fields=True (split ON lub shadow ON)."""
    try:
        entry = {
            "ts": datetime.now(WARSAW).isoformat(timespec="seconds"),
            "priority": priority,
            "source": source or "",
            "text": (text or "")[:2000],
            "sent_main": sent_main,
            "sent_silent": sent_silent,
        }
        if include_split_fields:
            entry["category"] = category
            entry["sent_repairs"] = sent_repairs
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


def _send_repairs(text: str) -> bool:
    """Wyślij na kanał NAPRAW (technical). Bliźniak _send_silent: urllib-only,
    pytest-guard, best-effort. Zwraca True TYLKO gdy ok=True.

    CHAT_ID wymagany (osobny cel). TOKEN opcjonalny — brak → fallback na główny
    bot (TELEGRAM_BOT_TOKEN) postujący na osobną grupę napraw. Każdy brak/fail →
    False → route() robi fail-open na główną grupę (alert nie ginie)."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("ALLOW_TELEGRAM_IN_TEST"):
        log.warning("notify_router._send_repairs blocked (pytest)")
        return False
    from dispatch_v2 import telegram_approver
    env = telegram_approver._load_env(_REPAIRS_ENV_PATH)
    chat_id = env.get(_REPAIRS_CHAT_KEY, "")
    if not chat_id:
        log.warning(f"notify_router: brak {_REPAIRS_CHAT_KEY} — kanał napraw off (fail-open main)")
        return False
    token = env.get(_REPAIRS_TOKEN_KEY, "")
    if not token:
        # fallback: główny bot na osobną grupę napraw (owner nie musi nowego bota)
        main_env = telegram_approver._load_env(_MAIN_ENV_PATH)
        token = main_env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        log.warning("notify_router: brak tokenu kanału napraw — fail-open main")
        return False
    body = f"🔧 {text}"
    r = telegram_approver.tg_request(token, "sendMessage", {"chat_id": chat_id, "text": body})
    if not r.get("ok"):
        log.warning(f"notify_router: kanał napraw send fail: {r.get('error') or r.get('description')}")
        return False
    return True


def route(text: str, source: str | None = None, priority: str | None = None) -> bool:
    """Zaklasyfikuj + zrutuj alert. Zwraca True gdy GŁÓWNA GRUPA ma wysłać,
    False gdy alert przejęty przez kanał poboczny (cichy=info / napraw=technical).

    OFF/OFF/OFF (wszystkie 3 flagi OFF): nowy classifier NIE jest wykonywany, a feed
    = DOKŁADNIE legacy schema (kill-test P1-5). Routing sterowany KATEGORIĄ — critical
    ZAWSZE na główną grupę (nigdy silent/napraw). Transport poboczny fail/wyjątek →
    fail-open na główną grupę. Wołane z telegram_utils.send_admin_alert.
    """
    from dispatch_v2.common import flag

    low_routing_on = flag("ENABLE_NOTIFY_PRIORITY_ROUTING", default=False)
    channel_split_on = flag("ENABLE_NOTIFY_CHANNEL_SPLIT", default=False)
    shadow_on = flag("ENABLE_NOTIFY_CHANNEL_SPLIT_SHADOW", default=False)

    pri = (priority or "").lower()
    if pri not in ("high", "low"):
        pri = classify(text, source)

    sent_silent = False
    sent_repairs = False
    proceed_main = True
    category = ""

    # OFF/OFF/OFF → nowy classifier NIE jest osiągany (byte-parity, kill-test P1-5).
    if low_routing_on or channel_split_on or shadow_on:
        category = classify_category(text, source, priority=pri)
        if category == "info":
            if low_routing_on:
                try:
                    sent_silent = _send_silent(text)
                except Exception as e:  # noqa: BLE001 — transport info nie blokuje main
                    log.warning(f"notify_router: cichy bot exception — fail-open main: {e}")
                proceed_main = not sent_silent  # fail-open: odetnij dopiero po sukcesie
        elif category == "technical":
            if channel_split_on:
                try:
                    sent_repairs = _send_repairs(text)
                except Exception as e:  # noqa: BLE001 — transport napraw nie blokuje main
                    log.warning(f"notify_router: kanał napraw exception — fail-open main: {e}")
                proceed_main = not sent_repairs  # fail-open: odetnij dopiero po sukcesie
        # category == "critical": proceed_main pozostaje True — NIGDY silent/napraw.

    _append_feed(text, pri, source, sent_main=proceed_main, sent_silent=sent_silent,
                 category=category, sent_repairs=sent_repairs,
                 include_split_fields=(channel_split_on or shadow_on))
    return proceed_main
