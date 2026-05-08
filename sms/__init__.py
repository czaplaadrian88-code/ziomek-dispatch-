"""dispatch_v2.sms — out-of-band SMS provider abstraction (MP-#9, master plan TOP-15).

Eliminates chicken-egg "Telegram bot down → admin alert via Telegram = gone".
Used by dispatch_v2.tg_heartbeat watchdog: ≥3× consecutive Telegram getMe fail
→ SMS Adrian.

Provider selection via env `SMS_PROVIDER`:
  - `ovh` — real OVH SMS API (cheap ~0.04 PLN/SMS Polish carrier reliability,
    Adrian provisions OVH account — see SETUP.md)
  - `stub` — logs only (default; dev + tests)

Add new providers via subclassing `provider.SMSProvider` + register in `get_provider()`.
"""
from dispatch_v2.sms.provider import SMSProvider, get_provider, SMSDeliveryError

__all__ = ["SMSProvider", "get_provider", "SMSDeliveryError"]
