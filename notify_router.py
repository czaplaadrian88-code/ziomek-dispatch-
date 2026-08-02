"""Centrum powiadomień — klasyfikacja + routing alertów Telegram.

ZAKRES (uzgodniony z Adrianem 2026-06-14):
- NIE dotyka strumienia propozycji KOORD (telegram_approver.proposal_sender).
  KOORD zostaje na głównym bocie bez zmian.
- Działa WYŁĄCZNIE na alertach idących przez telegram_utils.send_admin_alert
  (sla_tracker, parse_continuity_guard, observability.alert_onfailure,
  daily_briefing, courier_ranking, ...). To jest pojedynczy choke-point dla
  ~512 WARNING/dzień + alerty infra + briefingi.

DWA ORTOGONALNE WYMIARY KLASYFIKACJI (jeden kanoniczny owner: ten moduł + config):
1. priority ∈ {high, low}  (klasyfikacja LEGACY, bez zmian — decyduje głośny vs
   cichy): HIGH = głośny (główna grupa lub kanał napraw), LOW = cichy bot + feed.
2. category ∈ {technical, critical, info}  (NOWY wymiar — decyduje KTÓRY głośny
   kanał, wprowadzony 2026-08-02 na ACK Adriana):
     - info      = LOW nie-biznesowy (cichy bot @DajeszBot + kafel panelu).
     - technical = HIGH TECHNICZNY (awarie/OnFailure/service-down/liveness/
                   backup_sentinel/parser-health) → kanał NAPRAW.
     - critical  = HIGH BIZNESOWO-KRYTYCZNY (płatności/restauracje/nowy kurier)
                   → główna grupa Adriana (natychmiastowa interwencja).

PRECEDENCJA NIEKONFIGUROWALNA (patrz classify_category — kolejność zahardkodowana,
hot-config stroi tylko LISTY słów, NIGDY kolejność ani fail-safe):
  BIZNES-KRYTYCZNY (treść) > LOW→info > source_category > TECHNICAL (treść) >
  FAIL-SAFE 'critical'. Alertu biznesowego NIE da się zdegradować do NAPRAW ani
  schować na cichym bocie — ani przez source, ani przez jawny priority=low
  (kill-test P0-1/P0-2). Nieznany HIGH → 'critical' zahardkodowane (kill-test P0-3).

Klasyfikacja LIST słów: config dispatch_state/notify_routing.json (hot-reload).
Fail-safe classify(): tekst bez dopasowania → HIGH (nigdy nie chowamy alertu).

Gating — TRZY niezależne flagi (flags.json, wszystkie default OFF; niezależny rollback):
- ENABLE_NOTIFY_PRIORITY_ROUTING:     odcina info/LOW od głównego bota (→ cichy bot + feed).
- ENABLE_NOTIFY_CHANNEL_SPLIT:        odcina TECHNICAL od głównej grupy (→ kanał NAPRAW).
- ENABLE_NOTIFY_CHANNEL_SPLIT_SHADOW: NIE rutuje; dopisuje category do feedu dla
                                      obserwacji PRZED aktywacją (bez łamania byte-parity).
Każda flaga OFF = pełne zachowanie legacy dla swojego wymiaru (zero production impact).
BYTE-PARITY (kill-test P1-5): przy OFF/OFF (split i shadow OFF) feed = DOKŁADNIE legacy
schema (ts/priority/source/text/sent_main/sent_silent). category+sent_repairs dodawane
TYLKO gdy split ON lub shadow ON.
Fail-open: gdy transport kanału pobocznego (cichy/napraw) zawiedzie, alert wraca na
główną grupę — nigdy nie ginie. Biznes-krytyczny (critical) NIGDY nie jest odcinany.

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
# Kanał NAPRAW (technical). Owner wpina wartości w telegram_bots.env — patrz
# raport aktywacji. TOKEN opcjonalny (fallback na główny bot postujący na osobną
# grupę); CHAT_ID wymagany (osobny cel). Bez CHAT_ID → _send_repairs zwraca False
# → fail-open na główną grupę (alert nie ginie).
_REPAIRS_ENV_PATH = "/root/.openclaw/workspace/.secrets/telegram_bots.env"
_MAIN_ENV_PATH = "/root/.openclaw/workspace/.secrets/telegram.env"
_REPAIRS_TOKEN_KEY = "REPAIRS_BOT_TOKEN"
_REPAIRS_CHAT_KEY = "REPAIRS_CHAT_ID"

# Cap rozrostu feedu: gdy przekroczy próg, trim do ostatnich N wpisów.
_FEED_MAX_BYTES = 4_000_000
_FEED_TRIM_KEEP = 2000

# Fallback config (gdy plik notify_routing.json nie istnieje). Adrian tuninguje
# przez edycję pliku JSON — hot-reload, bez restartu. Klucze *_keywords /
# source_category są mergowane per-klucz z żywym JSON (brakujące → default),
# więc kategoryzacja działa od razu z tych defaultów, a JSON tylko dostraja LISTY.
# UWAGA: config stroi tylko LISTY — kolejność precedencji i fail-safe unknown→critical
# są zahardkodowane w classify_category (kill-test P0-1/P0-2/P0-3).
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
    # --- Wymiar KANAŁU (od 2026-08-02) — tylko dla HIGH. LOW zawsze = info. ---
    # BIZNES (critical) sprawdzany PRZED source i PRZED techniką (business wins,
    # precedencja zahardkodowana). Nieznany HIGH → critical (fail-safe HARDKOD).
    "critical_keywords": [
        "płatnoś", "platnos", "p24", "iban", "wypłat", "wyplat", "pobrani",
        "restauracj", "nowy kurier", "sparuj", "/nowy", "niesparowany",
        "krytycz", "klient", "zamówieni", "zamowieni",
    ],
    "technical_keywords": [
        "onfailure", "awaria", "awarii", "padł", "padla", "failed", "fail (",
        "exit=", "result=", "traceback", "exception", "liveness",
        "[ziomek liveness]", "service", "systemd", "backup", "backup_sentinel",
        "parser", "degraded", "osrm", "health", "watchdog", "timeout",
        "unit ", "restart", "disk", "oom",
    ],
    # Wymuszenie kategorii per źródło — tylko dla NIE-biznesowego HIGH; biznes-
    # krytyczna treść i tak wygrywa PRZED source (precedencja niekonfigurowalna).
    "source_category": {
        "alert_onfailure": "technical",
        "backup_sentinel": "technical",
        "data_alerts": "technical",
        "parser_health": "technical",
        "liveness_probe": "technical",
        "detector_419": "technical",
        "osrm_client": "technical",
    },
    # UWAGA: fail-safe „nieznany HIGH → critical" jest HARDKODOWANY w
    # classify_category (NIE z configu) — kill-test P0-3. Brak klucza default_category.
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


def classify_category(text: str, source: str | None = None,
                      priority: str | None = None) -> str:
    """Zwróć kanał: 'technical' | 'critical' | 'info'.

    PRECEDENCJA NIEKONFIGUROWALNA (kolejność zahardkodowana; hot-config stroi tylko
    LISTY słów, nie kolejność ani fail-safe):
      1. BIZNES-KRYTYCZNY (treść: płatności/restauracje/nowy kurier) → 'critical'
         — wygrywa PRZED source_category i PRZED jawnym priority=low. Alertu
         biznesowego nie da się zdegradować do NAPRAW ani schować na cichym bocie
         (kill-test P0-1/P0-2).
      2. LOW (nie-biznesowy) → 'info' (cichy bot + feed).
      3. source_category[source] (technical/critical) — tylko HIGH, nie-biznes.
      4. TECHNICAL po treści (awarie/OnFailure/service/parser/...) → 'technical'.
      5. FAIL-SAFE HARDKOD: HIGH bez dopasowania → 'critical' (Adrian widzi;
         NIGDY z configu — kill-test P0-3).

    priority (opcjonalny) — jeśli caller policzył już high/low, przekaż dla spójności.
    """
    cfg = _load_config()
    low_text = (text or "").lower()
    # 1. BIZNES-KRYTYCZNY wygrywa bezwarunkowo (przed source i przed degradacją LOW).
    for kw in cfg.get("critical_keywords", []):
        if kw in low_text:
            return "critical"
    # 2. Priorytet LOW (nie-biznesowy) → info.
    pri = (priority or "").lower()
    if pri not in ("high", "low"):
        pri = classify(text, source)
    if pri == "low":
        return "info"
    # 3. source_category — tylko HIGH, nie-biznes.
    cat_map = cfg.get("source_category", {})
    if source and source in cat_map:
        c = str(cat_map[source]).lower()
        if c in ("technical", "critical"):
            return c
    # 4. TECHNICAL po treści.
    for kw in cfg.get("technical_keywords", []):
        if kw in low_text:
            return "technical"
    # 5. FAIL-SAFE HARDKOD (nie z configu): nieznany HIGH → critical.
    return "critical"


def _append_feed(text: str, priority: str, source: str | None,
                 sent_main: bool, sent_silent: bool,
                 category: str = "", sent_repairs: bool = False,
                 include_split_fields: bool = False) -> None:
    """Dopisz wpis do feedu (kafel panelu czyta tail). Best-effort, nigdy nie
    wysadza ścieżki alertu.

    BYTE-PARITY (kill-test P1-5): przy OFF/OFF (split i shadow OFF) schemat = DOKŁADNIE
    legacy (ts/priority/source/text/sent_main/sent_silent). Klucze category/sent_repairs
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

    Routing sterowany KATEGORIĄ (nie surowym priority) — dzięki temu biznes-krytyczny
    (category=critical) ZAWSZE idzie na główną grupę: nigdy na cichy bot, nigdy na
    kanał napraw (kill-test P0-2). Bezpieczniki:
    - info→cichy / technical→napraw: odcięcie od main DOPIERO po potwierdzonym
      delivery; transport fail/wyjątek → fail-open na główną grupę.
    - feed: byte-parity legacy przy OFF/OFF; category+sent_repairs tylko gdy
      split ON lub shadow ON (kill-test P1-5).

    Wołane z telegram_utils.send_admin_alert. Zawsze zapisuje wpis do feedu.
    """
    from dispatch_v2.common import flag

    pri = (priority or "").lower()
    if pri not in ("high", "low"):
        pri = classify(text, source)
    category = classify_category(text, source, priority=pri)

    low_routing_on = flag("ENABLE_NOTIFY_PRIORITY_ROUTING", default=False)
    channel_split_on = flag("ENABLE_NOTIFY_CHANNEL_SPLIT", default=False)
    shadow_on = flag("ENABLE_NOTIFY_CHANNEL_SPLIT_SHADOW", default=False)

    sent_silent = False
    sent_repairs = False
    proceed_main = True

    if category == "info":
        if low_routing_on:
            try:
                sent_silent = _send_silent(text)
            except Exception as e:  # noqa: BLE001 — transport info nie może zablokować main
                log.warning(f"notify_router: cichy bot exception — fail-open main: {e}")
            proceed_main = not sent_silent  # fail-open: odetnij main dopiero po sukcesie
    elif category == "technical":
        if channel_split_on:
            try:
                sent_repairs = _send_repairs(text)
            except Exception as e:  # noqa: BLE001 — transport napraw nie może zablokować main
                log.warning(f"notify_router: kanał napraw exception — fail-open main: {e}")
            proceed_main = not sent_repairs  # fail-open: odetnij main dopiero po sukcesie
    # category == "critical": proceed_main pozostaje True — NIGDY silent/napraw.

    _append_feed(text, pri, source, sent_main=proceed_main, sent_silent=sent_silent,
                 category=category, sent_repairs=sent_repairs,
                 include_split_fields=(channel_split_on or shadow_on))
    return proceed_main
