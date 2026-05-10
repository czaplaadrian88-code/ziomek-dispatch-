# TASK D D.6 — Telegram Welcome Message Detailed Design

**Date:** 2026-05-05 (pre-TASK D sprint Cz 07.05)
**Author:** CC design agent (45-min time-box, Z2 quality)
**Status:** DESIGN ONLY — implementacja warunkowa od Adrian decision (Section 6)
**Mode:** READ-ONLY na prod kod, zero deploy
**Scope:** D.6 candidate — bot wysyła DM do nowego kuriera z PIN + login instructions po success migracji 3 store'ów (D.4 atomic write)
**Budget freed:** TASK D D.4 simplification (4-step → 3-step po Agent #3 audit `task_d_courier_api_audit.md`) wyzwoliło ~1.5h budget
**Decision pending Adrian:** implement Cz 07.05 lub defer V3.30 — Section 6 zbiera 5 unknowns + recommendation

---

## Section 1 — User flow (onboarding lifecycle)

Z perspektywy nowego kuriera (przykład: "Marcin Nowy", PIN auto-gen `7384`, cid=534 next available):

1. **Adrian add courier** — przez D.2 Telegram UI (`/dodaj_kuriera Marcin Nowy`) lub D.3 CLI (`/add_kurier`). Wymagane minimum: `name`, opcjonalnie tier/grafik. Przypisanie cid + PIN auto-gen po stronie dispatch_v2.
2. **D.4 atomic write 3 stores** — `kurier_ids.json` (name→cid) + `courier_tiers.json` (cid→tier) + `kurier_piny.json` (PIN→name). Per Lekcja #71: fcntl.LOCK_EX + temp+fsync+rename, all-or-nothing rollback przy fail któregokolwiek z 3.
3. **D.6 trigger (NEW)** — natychmiast post-D.4 success (commit point), bot próbuje wysłać DM do nowego kuriera. Pre-condition: kurier zna `chat_id` (patrz Section 2 discovery paths). Best-effort: D.6 fail NIE rollback'uje D.1-D.3 — filesystem state correct, welcome to UX nicety.
4. **Kurier odbiera DM** — Telegram push notification "🚀 Witaj w NadajeSz, Marcin!". Treść = PIN + APK link + login URL.
5. **Kurier pobiera APK + login** — klik link `https://gps.nadajesz.pl/apk/courier.apk`, install (Android side-load, "unknown sources" prompt), open app, wpisuje PIN `7384`. courier-api `/api/auth/select` zwraca Bearer token, sesja 30-day hard / 90min idle.
6. **courier-api auto-pickup** — service jest READ-ONLY consumerem `kurier_ids.json` + `kurier_piny.json` (per Agent #3 audit). Każdy `_load_json_safe` czyta świeży snapshot, ZERO restart wymagany. Nowy kurier dostępny w `/api/couriers` natychmiast.
7. **Pierwsza propozycja** — gdy kurier rozpocznie shift (zgodnie z grafikiem) i włączy GPS, dispatch_pipeline może go wybrać jako candidate (subject to V3.13 STRICT_COURIER_ID_SPACE + V3.14 BAG_INTEGRITY + R-04 tier).

**Edge cases (production realities):**
- **Kurier nie zrobił `/start` z @NadajeszBot** → Telegram API restriction: bot NIE może DM użytkownikowi który NIE inicjował konwersacji. `sendMessage` zwróci `403 Forbidden: bot can't initiate conversation with a user`. Patrz Section 2 Discovery (A) pre-onboarding flow.
- **Kurier blocked bot** → identyczny `403`. Nieodróżnialne od never-started state z server-side. Action: log `WELCOME_FAILED` + alert Adrian.
- **Kurier zalany messages / popup buried** → DM dostarczone, ale missed. Mitigation: drugi DM po 1h jeśli no `/start`-confirm? (overkill V3.30 backlog) Lub Adrian SMS fallback (Section 6 unknown #4).
- **Telegram username conflict** — name ≠ `@username` (Telegram nicki nie korelują z `kurier_ids.json` panel_name). Discovery wymaga `chat_id` int, NIE `@nick`.

---

## Section 2 — Telegram bot DM mechanika

### Telegram Bot API constraint

Bot nie może DM-nąć user'owi który nigdy nie wysłał wiadomości do bota. Dokumentacja Telegram: "A bot cannot initiate a conversation with a user." Konsekwencja: `chat_id` = `user_id` user'a istnieje TYLKO jeśli user przedtem zrobił `/start` (lub wysłał jakąkolwiek wiadomość/klikał inline-button bota).

### API call

```
POST https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage
Content-Type: application/json

{
  "chat_id": 123456789,
  "text": "<welcome body>",
  "parse_mode": "Markdown",
  "disable_web_page_preview": true
}
```

Response: `200 OK {ok:true, result:{...}}` lub `403 Forbidden {ok:false, description:"..."}`.

### Reuse istniejącej infrastruktury

`shift_notifications/telegram_send.py` już ma `tg_send_text_with_keyboard(text, chat_id, reply_markup=None)` (89 LoC, fire-and-forget POST, TEST_MODE flag, token resolution z 3 źródeł). D.6 sender importuje ten helper bezpośrednio — zero nowego HTTP client'a. Token resolution (env → secrets file) i logging pattern zostają unchanged.

### Discovery: jak znaleźć chat_id nowego kuriera?

**3 alternatywne paths:**

**(A) Pre-onboarding `/start` capture (RECOMMENDED, low effort):**
1. Adrian wysyła kurierowi link `https://t.me/NadajeszBot?start=onboard`.
2. Kurier klika → Telegram otwiera czat z botem → push button "START" → bot dostaje `update` z `from.id = user_id`, `from.first_name = "Marcin"`.
3. `telegram_approver.py` w `handle_message`: gdy `text=="/start"` AND `from_id` NIE w `KURIER_AUTHORIZED_USER_IDS` whitelist → save `{user_id, first_name, started_at_utc}` do `kurier_chat_ids_pending.json` (NEW file). Reply: "Witaj. Adrian doda Cię wkrótce. Zaczekaj na Twój PIN."
4. Adrian widzi pending entries w D.2 prompt (`/dodaj_kuriera`) — pre-filled suggestions `Marcin (chat_id=...)`.
5. Adrian klik [Tak, dodaj] → D.4 atomic + D.6 send.

**(B) Post-onboarding manual fallback:**
- Jeśli kurier nie ma chat_id w `kurier_chat_ids.json` ani `_pending`: D.6 sender → fallback DM Adriana z templatem "Wyślij PIN ręcznie {imie}: {pin}". Adrian copy-paste do SMS / WhatsApp.

**(C) QR code approach (V3.30 polish):**
- Adrian pokazuje QR z `https://t.me/NadajeszBot?start=onboard_{uuid}`. Kurier skanuje aparatem → Telegram opens → bot dostaje `/start onboard_{uuid}` z deep-link param. Bot kojarzy `uuid` z pending Adrian's intent. Bonus: sub-second latency vs Adrian sending link manualnie.

**Adrian decision (Section 6 #2):** path (A) jest minimum-viable, (C) defer.

---

## Section 3 — Welcome message format

### Proposed template (PL, mobile-readable, ~12 linii)

```
🚀 Witaj w NadajeSz, {imie}!

Twój login do panelu kuriera:
- Adres: https://gps.nadajesz.pl
- PIN: {pin}

Pobierz aplikację Android:
https://gps.nadajesz.pl/apk/courier.apk

Po instalacji wpisz PIN i zaloguj się.

Pytania → DM Adrian (Telegram).

Powodzenia! 💪
```

### Design rationale

- **Imię w 1. linii** — personalizacja, salience first.
- **PIN bold** (`*PIN: {pin}*` Markdown) — wzrokowy anchor, kurier wraca do tej linii.
- **Link APK direct download** — Android browsers honor MIME type, prompt install. NIE wymaga Google Play (NadajeSz nie ma listing).
- **Adrian DM CTA** — single point of contact w razie problemów. NIE bot reply (bot nie obsługuje free-text input od neuthorized users).
- **Emoji 🚀💪** — przyjazna onboarding tonacja zgodna z Adrian'owym stylem (Mailek CC-5 "głos Adriana").

### Customization options (Section 6 #3)

- Nadać dłuższy welcome z reference do Reguł Biznesowych? (overkill, kurier nie zrozumie context)
- Dodać "Zacznij od shift X" z grafiku? (cross-coupling z schedule cache, kruche)
- Skrócić do 5 linii bez "powodzenia"? (mniej friendly)

Recommendation: zachować obecny format, modyfikacje per Adrian feedback po pierwszych 2-3 real onboardings.

---

## Section 4 — Implementation design

### Files NEW

**`dispatch_v2/onboarding/__init__.py`** (3 LoC)
```python
"""TASK D onboarding helpers — D.6 Telegram welcome message (2026-05-07)."""
```

**`dispatch_v2/onboarding/welcome_message.py`** (~120 LoC)

```python
"""TASK D D.6 — Welcome message sender (2026-05-07).

Pure formatter + best-effort sender. Reuses
shift_notifications.telegram_send.tg_send_text_with_keyboard for HTTP.

Flag-gated via flags.json ENABLE_D6_WELCOME (default False).
Fallback path WELCOME_FALLBACK_TO_ADRIAN (default True).
"""
from __future__ import annotations

import json
import os
from typing import Optional, Tuple

from dispatch_v2.common import setup_logger, load_flags
from dispatch_v2.shift_notifications.telegram_send import (
    tg_send_text_with_keyboard,
)

LOG_DIR = "/root/.openclaw/workspace/scripts/logs/"
_log = setup_logger("onboarding.welcome", LOG_DIR + "onboarding.log")

CHAT_IDS_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_chat_ids.json"
ADRIAN_CHAT_ID = 8765130486

APK_URL = "https://gps.nadajesz.pl/apk/courier.apk"
PANEL_URL = "https://gps.nadajesz.pl"


def format_welcome_message(name: str, pin: str) -> str:
    """Pure formatter — zero I/O. Returns rendered message body."""
    first_name = name.split()[0] if name else "Kurierze"
    return (
        f"🚀 Witaj w NadajeSz, {first_name}!\n\n"
        f"Twój login do panelu kuriera:\n"
        f"- Adres: {PANEL_URL}\n"
        f"- PIN: *{pin}*\n\n"
        f"Pobierz aplikację Android:\n"
        f"{APK_URL}\n\n"
        f"Po instalacji wpisz PIN i zaloguj się.\n\n"
        f"Pytania → DM Adrian (Telegram).\n\n"
        f"Powodzenia! 💪"
    )


def _load_chat_id(name: str) -> Optional[int]:
    """Lookup chat_id w kurier_chat_ids.json by panel_name. None gdy missing."""
    try:
        if not os.path.exists(CHAT_IDS_PATH):
            return None
        with open(CHAT_IDS_PATH) as f:
            data = json.load(f) or {}
        chat_id = data.get(name)
        return int(chat_id) if chat_id else None
    except Exception as e:
        _log.warning(f"_load_chat_id({name}) fail: {type(e).__name__}: {e}")
        return None


def send_welcome(
    name: str,
    pin: str,
    courier_chat_id: Optional[int] = None,
    fallback_to_adrian: bool = True,
) -> Tuple[bool, str]:
    """Send welcome DM. Returns (sent_to_courier, status_string).

    Logic:
    1. Resolve chat_id: explicit arg > kurier_chat_ids.json[name] > None.
    2. If chat_id: tg_send_text_with_keyboard → on success log SENT_DIRECT.
    3. If no chat_id OR send fail: jeśli fallback_to_adrian → DM Adrian
       z fallback templatem; log FALLBACK_ADRIAN.
    4. learning_log entry (event=ONBOARDING_WELCOME_SENT|FAILED|FALLBACK).
    """
    flags = load_flags() or {}
    if not flags.get("ENABLE_D6_WELCOME", False):
        _log.info(f"send_welcome({name}): flag OFF, skip silent")
        return False, "FLAG_OFF"

    chat_id = courier_chat_id or _load_chat_id(name)
    body = format_welcome_message(name, pin)

    if chat_id:
        try:
            ok = tg_send_text_with_keyboard(text=body, chat_id=chat_id)
            if ok:
                _log.info(f"send_welcome({name}): SENT_DIRECT chat={chat_id}")
                _log_learning_event("ONBOARDING_WELCOME_SENT", name, chat_id)
                return True, "SENT_DIRECT"
        except Exception as e:
            _log.warning(f"send_welcome direct fail: {type(e).__name__}: {e}")

    if fallback_to_adrian and flags.get("WELCOME_FALLBACK_TO_ADRIAN", True):
        fallback_body = (
            f"⚠️ D.6 fallback — wyślij PIN ręcznie {name}:\n\n"
            f"PIN: {pin}\n"
            f"Treść (skopiuj):\n\n{body}"
        )
        ok = tg_send_text_with_keyboard(text=fallback_body, chat_id=ADRIAN_CHAT_ID)
        _log.info(f"send_welcome({name}): FALLBACK_ADRIAN ok={ok}")
        _log_learning_event("ONBOARDING_WELCOME_FALLBACK", name, ADRIAN_CHAT_ID)
        return False, "FALLBACK_ADRIAN"

    _log_learning_event("ONBOARDING_WELCOME_FAILED", name, None)
    return False, "FAILED_NO_FALLBACK"


def _log_learning_event(event: str, name: str, chat_id: Optional[int]) -> None:
    """Append learning_log JSONL — observability dla learning_analyzer."""
    # ... (atomic JSONL append, fcntl, mirror auto_koord pattern)
```

### Files MODIFIED

- **`dispatch_v2/migrations/migrate_couriers_2026-05-05.py`** — w `--apply` subcommand, post-success per-record (po D.4 atomic 3-store commit), wywołać:
  ```python
  from dispatch_v2.onboarding.welcome_message import send_welcome
  send_welcome(name=name, pin=pin, fallback_to_adrian=True)
  ```
  Best-effort path: jeśli `send_welcome` raise, log + continue (NIE rollback D.1-D.3).

- **`dispatch_v2/CLAUDE.md`** — dodać D.6 do Roadmap section (TASK D step list 4 → 5).

- **`flags.json`** — 2 nowe klucze:
  ```json
  {
    "ENABLE_D6_WELCOME": false,
    "WELCOME_FALLBACK_TO_ADRIAN": true
  }
  ```
  Defaults: D.6 OFF dopóki Adrian nie ACK po smoke test, fallback ON jeśli D.6 włączone.

- **`dispatch_v2/telegram_approver.py`** — `handle_message()` extension dla pre-onboarding (D.7 bundled scope, Section 6 #2):
  - Gate: `text == "/start"` AND `from_id` NIE w `KURIER_AUTHORIZED_USER_IDS` whitelist
  - Action: append `{from_id, first_name, started_at}` do `kurier_chat_ids_pending.json`
  - Reply: "Witaj. Adrian doda Cię wkrótce. Zaczekaj na Twój PIN."

### Files NEW (state)

- **`dispatch_state/kurier_chat_ids.json`** (NEW) — `{name: chat_id}` mapping. Atomic writes per Lekcja #71. Populated dwoma ścieżkami:
  1. Po `/start` migration z `kurier_chat_ids_pending.json` → `kurier_chat_ids.json` (gdy Adrian add).
  2. Manual seed Adrian jeśli kurier ma już istniejącą konwersację.
- **`dispatch_state/kurier_chat_ids_pending.json`** (NEW) — staging area przed Adrian add. Oczekiwany rozmiar: <50 entries (cleanup 30-day TTL przez janitor cron).

### Atomic transaction extension (post-D.4)

```
step 1: write kurier_ids.json (cid)            ← D.4 existing
step 2: write courier_tiers.json (tier)        ← D.4 existing
step 3: write kurier_piny.json (PIN)           ← D.4 existing
step 4 (NEW): send_welcome(...)                ← D.6 best-effort
```

**Failure semantics:**
- Step 1-3 fail → rollback wszystkie 3 (existing D.4 logic, NIE zmieniane).
- Step 4 fail (Telegram 403/timeout/format error) → log + continue, NIE rollback. State filesystem correct, kurier registered, GPS app działa. Welcome to UX nicety.
- Learning event `ONBOARDING_WELCOME_FAILED` z reason → Adrian widzi w EOD review jeśli >0 fails.

### Observability

- **journalctl tag** `dispatch_v2.onboarding.welcome` (per `_log` setup).
- **learning_log JSONL** event types: `ONBOARDING_WELCOME_SENT`, `ONBOARDING_WELCOME_FALLBACK`, `ONBOARDING_WELCOME_FAILED`. Reader extension w `learning_analyzer` (defer V3.30).
- **flags.json** snapshot per run (audit trail flag flips).

### Estimate

- Code: ~120 LoC `welcome_message.py` + ~30 LoC migration patch + ~20 LoC `telegram_approver` D.7 + ~5 LoC flags = **~175 LoC total**.
- Tests: ~150 LoC custom-runner pattern (10 tests).
- Effort: 1.5-2h (Cz 07.05).

---

## Section 5 — Tests

Custom-runner pattern (zgodny z `dispatch_v2/migrations/test_migrate_couriers_*.py`), zero pytest dependency. Plik: `dispatch_v2/onboarding/tests/test_welcome_message_d6.py`.

**10 tests:**

1. **`test_format_welcome_basic`** — `format_welcome_message("Marcin Nowy", "7384")` zawiera `"Marcin"`, `"7384"`, `APK_URL`, `PANEL_URL`. Imię z 1. tokenu (NIE full name).
2. **`test_format_welcome_emoji_markers`** — output ma `🚀` w 1. linii i `💪` w ostatniej. Mobile-readable: <500 bytes total.
3. **`test_format_welcome_apk_link`** — `https://gps.nadajesz.pl/apk/courier.apk` exactly w body. PIN bold (`*7384*` Markdown).
4. **`test_send_welcome_chat_id_known`** — mock `tg_send_text_with_keyboard` → True. Verify call args: `chat_id=123`, body contains `"7384"`. Return: `(True, "SENT_DIRECT")`.
5. **`test_send_welcome_chat_id_unknown_fallback_to_adrian`** — `kurier_chat_ids.json` empty + `fallback_to_adrian=True`. Mock TG: 1× call do `ADRIAN_CHAT_ID=8765130486`. Body: `"⚠️ D.6 fallback"`. Return: `(False, "FALLBACK_ADRIAN")`.
6. **`test_send_welcome_telegram_api_fail`** — mock `tg_send_text_with_keyboard` raise `urllib.error.HTTPError(403)`. Path: direct → fail → fallback Adrian. Return: `(False, "FALLBACK_ADRIAN")`. Log warning captured.
7. **`test_pending_chat_ids_capture_on_start`** — symulacja `telegram_approver.handle_message({text:"/start", from:{id:999, first_name:"Marcin"}})`. Verify: `kurier_chat_ids_pending.json` ma entry `{999: {first_name: "Marcin", ...}}`. Reply text contains `"Adrian doda Cię"`.
8. **`test_d6_flag_disabled_skip_silent`** — `ENABLE_D6_WELCOME=False` w flags.json. `send_welcome(...)` zero TG calls, return `(False, "FLAG_OFF")`. Test isolation: restore flags backup po teście.
9. **`test_d6_atomic_extension_failure_no_rollback`** — symulacja D.4 success + D.6 fail. Verify `kurier_ids.json` + `courier_tiers.json` + `kurier_piny.json` ZACHOWUJĄ nową entry mimo `send_welcome` exception. Migration logger ma WARNING entry.
10. **`test_d6_observability_log_entry`** — verify `_log_learning_event` zapisuje JSONL line do `onboarding.log` z fields `{event, name, chat_id, ts}`. Format compatible z learning_analyzer reader.

**Edge case follow-ups (defer V3.30 jeśli potrzebne):**
- Multi-line name w PIN format (PII safety).
- Telegram rate limit (1 msg/s/user) — burst onboarding 5 kurierów w 10s.
- Test-mode dry-run flag (`SHIFT_NOTIFY_TELEGRAM_TEST_MODE=1`) integration.

**Test runner:**
```bash
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.onboarding.tests.test_welcome_message_d6
# Expected output: 10/10 PASS w <2s
```

---

## Section 6 — Adrian decisions (5 unknowns)

### #1 — D.6 scope: implement Cz 07.05 lub defer V3.30?

- **Effort:** ~1.5-2h (estimate Section 4).
- **Pre-condition:** TASK D D.4 simplification freed budget (4-step→3-step po Agent #3 audit). Czwartek deploy budget realnie pomieści D.1-D.5 + D.6.
- **Argumenty implement now:** while context jest fresh (Sprawa #1 migration just LIVE 05.05 popołudniem), D.6 jest natural extension; zero blocker'ów (telegram_send.py infra istnieje, learning_log JSONL pattern istnieje).
- **Argumenty defer:** Faza 7 GO/NO-GO Pt 08.05 wymaga Adrian focus, D.6 = soft UX feature. V3.30 sprint mógłby bundle z innymi onboarding polish (D.7 QR, learning_analyzer reader).
- **Recommendation:** **implement Cz 07.05** (D.6 + bundled D.7 pre-onboarding /start capture). 2h budget mieści się w D-day window 4-5h.

### #2 — Pre-registration `/start` flow (D.7): bundle z D.6 lub osobny ticket?

- **Bundle (A):** D.6 + D.7 razem = comprehensive onboarding flow w jednym deploy. Effort total ~2.5h (D.6 1.5h + D.7 +1h). Zalety: zero manual chat_id entry, kurier-driven discovery.
- **Standalone (B):** D.6 tylko z manual fallback path → wszystkie pierwsze onboardings idą fallback Adrian → test rzeczywistej user experience. D.7 dopiero gdy Adrian zatwierdzi że pre-onboarding flow nie ma rough edges.
- **Recommendation:** **bundle (A)** — telegram_approver.py jest stable (zero zmian od TASK B Phase 1 deploy 04.05). D.7 patch surgical (~20 LoC), low risk.

### #3 — Welcome message template: OK z proposed format lub customize?

- **Default (Section 3):** PL, ~12 linii, emoji 🚀💪, PIN bold, APK direct.
- **Alternatywy:**
  - Krótszy 5-line bezludny "PIN: X. App: link. Pytania→Adrian." — mniej friendly, ale szybsze do skanu.
  - Dłuższy z reference do Reguł Biznesowych (R-DECLARED-TIME, R-35MIN-MAX) — overkill onboarding, kurier nie zrozumie.
  - Z linkiem do shift schedule (Google Sheets URL) — cross-coupling z schedule cache, kruche jeśli URL się zmieni.
- **Recommendation:** **proposed format**, iterate per Adrian feedback po pierwszych 2-3 real onboardings (5-10 dni post-deploy).

### #4 — Fallback path scope: jaki format Adrian DM gdy chat_id missing?

- **Minimum:** "⚠️ Wyślij PIN ręcznie {imie}: {pin}" + body do copy-paste (Section 4 implementation).
- **Rozszerzony:** dorzucić suggested SMS template z Adrian'owym numerem nadawcy: "SMS treść: 'Cześć {imie}, twój PIN do NadajeSz: {pin}. App: https://gps.nadajesz.pl/apk/courier.apk'". + WhatsApp deep-link `https://wa.me/?text=...`.
- **Bardzo rozszerzony:** auto-trigger fallback channels (Twilio SMS API integration, WhatsApp Business API). Effort 6-8h, scope creep.
- **Recommendation:** **rozszerzony** (SMS template + WhatsApp deep-link) — zero dodatkowy effort, +2 minutes Adrian copy-paste convenience. Bardzo rozszerzony defer V3.31+.

### #5 — Welcome retry semantics: API down handling?

- **Single + Adrian alert (A):** `send_welcome` 1× próba, fail → log + DM Adrian "D.6 send fail dla {imie} — sprawdź TG bot status". No automatic retry.
- **Retry 3× exp backoff (B):** 1s/3s/9s delays, total max 13s. Defensive vs transient API hiccup. Risk: blokuje migration `--apply` flow przez 13s/kurier × N kurierów.
- **Async retry queue (C):** main migration writes `welcome_retry_queue.json`, separate cron `dispatch-welcome-retry.timer` 5-min interval pociąga pending. Effort +2h, bardzo over-engineered dla edge case.
- **Recommendation:** **(A) single + Adrian alert.** Telegram API uptime jest wysoki (>99.9% historicznie); transient fails są rzadkie. Adrian DM alert + manual retry przez `--apply` re-run dla pojedynczego kuriera = pragmatic. Refaktoryzuj do (B) jeśli >5 fails/tydzień zaobserwowane post-deploy.

---

## Recommendation summary

**Implementuj D.6 Cz 07.05** (bundled D.7 pre-onboarding `/start` capture). Effort total ~2.5h, mieści się w czwartkowym D-day budget (4-5h). Default flag OFF dopóki Adrian nie ACK po smoke test (1-2 fake/test onboardings).

**Wytyczne deploy:**
1. ACK Adrian Section 6 decisions (5 unknowns) przed kodowaniem.
2. Patch workflow: backup → edit → py_compile → tests 10/10 → manual smoke (2 fake onboardings: 1 z chat_id known, 1 fallback Adrian) → flag flip → first real onboarding observation 1h.
3. Rollback path: `ENABLE_D6_WELCOME=False` w flags.json (zero restart, hot-reload via `load_flags` mtime check).
4. Tag chronologicznie: `task-d-d6-welcome-message-2026-05-07` po smoke + first real success.

**Risks (dwa):**
- Telegram rate-limit jeśli >1 onboarding/sekunda (peak migration scenario): low — kurierzy idą przez Adrian D.2 prompt sequentially.
- chat_id leak w logs: `kurier_chat_ids.json` jest niskiej wrażliwości (TG user_id != PII jednoznaczny), ale dorzucić `.gitignore` entry.

---

**File location:** `eod_drafts/2026-05-05/task_d_d6_welcome_message_design.md`
**Total length:** ~1500 słów (target met)
**Status:** DRAFT — pending Adrian review (Section 6 5 decisions).
