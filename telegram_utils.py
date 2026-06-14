"""Minimalistyczny helper wysyłki Telegram dla procesów nie-botowych.

Reużywa istniejące helpery z telegram_approver (token load + tg_request HTTP)
i common.load_config (admin_id). Zero duplikacji logiki HTTP — jedyna wartość
tego modułu to enkapsulacja wzorca "load_env + load_config.admin_id + tg_request"
w jednym miejscu, żeby przyszli konsumenci (sla_tracker R6 pre-warning,
strategic_drop alert, restaurant violation notifier) nie powielali boilerplate.

NIE dotyka daily_briefing / courier_ranking — ich własne _send_telegram
zostają jak są (drobna duplikacja boilerplate bez priorytetu refactor).
"""
import logging
import os

from dispatch_v2 import telegram_approver
from dispatch_v2.common import load_config

log = logging.getLogger(__name__)

_TELEGRAM_ENV_PATH = "/root/.openclaw/workspace/.secrets/telegram.env"


def send_admin_alert(text: str, *, source: str | None = None,
                     priority: str | None = None) -> bool:
    """Wysyłka prywatnej wiadomości do Adriana (cfg.telegram.admin_id).

    Returns True tylko gdy Telegram API zwrócił ok=True. Każdy fail point
    (brak tokena, brak admin_id, HTTP fail) logowany przez logger modułu.

    Centrum powiadomień (2026-06-14): alert przechodzi przez notify_router,
    który klasyfikuje priorytet i — gdy flaga ENABLE_NOTIFY_PRIORITY_ROUTING
    ON — odcina LOW od głównego bota (LOW → cichy bot + kafel panelu). source/
    priority pozwalają callerowi wymusić klasyfikację (np. alert_onfailure →
    priority="high"). Domyślnie auto-klasyfikacja po treści. Flaga OFF =
    zachowanie legacy (główny bot dostaje wszystko; feed tylko archiwizuje).
    """
    # Z2 fix 2026-05-07 (Lekcja #75): refuse to send during pytest test execution.
    # PYTEST_CURRENT_TEST jest auto-ustawiany przez pytest per test, w produkcji nigdy.
    # Layer 1 defense-in-depth dla testów które forget to mock telegram (06.05 spam).
    # Opt-out dla testu wprost weryfikującego send: ALLOW_TELEGRAM_IN_TEST=1.
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("ALLOW_TELEGRAM_IN_TEST"):
        log.warning(f"send_admin_alert blocked (pytest context, set ALLOW_TELEGRAM_IN_TEST=1 to override): {text[:80]!r}")
        return True
    # Centrum powiadomień: klasyfikacja + routing (feed zawsze; LOW odcinane gdy flaga ON).
    try:
        from dispatch_v2 import notify_router
        proceed_main = notify_router.route(text, source=source, priority=priority)
        if not proceed_main:
            return True  # LOW przejęte przez cichy bot + feed; nie wysyłaj na główny bot
    except Exception as e:  # noqa: BLE001 — router nigdy nie blokuje ścieżki alertu
        log.warning(f"notify_router route fail (fallback: główny bot): {e}")
    env = telegram_approver._load_env(_TELEGRAM_ENV_PATH)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        log.error("telegram_utils.send_admin_alert: brak TELEGRAM_BOT_TOKEN w env")
        return False
    try:
        admin_id = str(load_config()["telegram"]["admin_id"])
    except (KeyError, TypeError, FileNotFoundError) as e:
        log.error(f"telegram_utils.send_admin_alert: brak admin_id w config: {e}")
        return False
    r = telegram_approver.tg_request(
        token, "sendMessage", {"chat_id": admin_id, "text": text}
    )
    if not r.get("ok"):
        log.error(
            f"telegram_utils.send_admin_alert: tg_request fail: "
            f"{r.get('error') or r.get('description')}"
        )
        return False
    return True
