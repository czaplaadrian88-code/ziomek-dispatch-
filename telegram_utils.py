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

from dispatch_v2 import telegram_approver
from dispatch_v2.common import load_config

log = logging.getLogger(__name__)

_TELEGRAM_ENV_PATH = "/root/.openclaw/workspace/.secrets/telegram.env"


def send_admin_alert(text: str) -> bool:
    """Wysyłka prywatnej wiadomości do Adriana (cfg.telegram.admin_id).

    Returns True tylko gdy Telegram API zwrócił ok=True. Każdy fail point
    (brak tokena, brak admin_id, HTTP fail) logowany przez logger modułu.
    """
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
