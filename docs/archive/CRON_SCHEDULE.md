# CRON_SCHEDULE.md — harmonogram cron na Hetzner Ziomek (13.04.2026)

**Cel:** single source of truth dla cron na produkcji, żeby można było odtworzyć
po rebuild serwera albo migracji.

**Ważne:** `CRON_TZ=Europe/Warsaw` musi być na pierwszej linii crontab — wszystkie
hour fields są interpretowane w Warsaw local time, DST-safe.

## Aktywne wpisy (stan 13.04.2026)

```cron
CRON_TZ=Europe/Warsaw

# --- FETCH SCHEDULE (Google Sheets → schedule_today.json) ---
# 06:00 — fetch pre-shift (przed 08:00 start kuriera)
# 08:00 — fetch post-shift (sanity po star zmiany)
0 6 * * * python3 /root/.openclaw/workspace/scripts/fetch_schedule.py >> /tmp/gastro_cron.log 2>&1
0 8 * * * python3 /root/.openclaw/workspace/scripts/fetch_schedule.py >> /tmp/gastro_cron.log 2>&1

# --- REBOOT HOOKS (legacy dispatch/GPS) ---
@reboot nohup python3 /root/gps_server.py > /tmp/gps_server.log 2>&1 &
@reboot sleep 30 && /root/fix_approvals.sh >> /tmp/gastro_cron.log 2>&1
@reboot sleep 15 && python3 /root/dispatch_control.py >> /tmp/dispatch_control.log 2>&1 &

# --- GIT PUSH HOURLY (F0 backup → GitHub) ---
# Co godzinę o :00 pushnij master na origin (ziomek-dispatch-)
0 * * * * cd /root/.openclaw/workspace/scripts/dispatch_v2 && git push origin master >> /root/backups/git_push.log 2>&1

# --- DAILY BRIEFING (F1.4b) — WYŁĄCZONE 14.04 (F1.6) ---
# Adrian preference: on-demand przez Telegram /status zamiast auto push.
# Skrypty zostają w repo jako manual option:
#   cd /root/.openclaw/workspace/scripts && TZ=Europe/Warsaw python3 -m dispatch_v2.daily_briefing morning
#   cd /root/.openclaw/workspace/scripts && TZ=Europe/Warsaw python3 -m dispatch_v2.daily_briefing evening

# --- COURIER RANKING (F1.4c) — WYŁĄCZONE 14.04 (F1.6) ---
# Zintegrowane w /status (sekcja "Top 3 wczoraj").
# Manual run:
#   cd /root/.openclaw/workspace/scripts && TZ=Europe/Warsaw python3 -m dispatch_v2.courier_ranking
```

## On-demand powiadomienia (F1.6 14.04)

Zamiast automatycznych Telegram pushów, **jedyne powiadomienie** to komenda
`/status` w `@NadajeszBot` (F1.4a). Format 3-w-1:

1. **Stan systemu** — serwisy (watcher/tracker/shadow/telegram) + ordery state
2. **Dziś** — delivered + propozycje + agreement rate
3. **Wczoraj** — delivered + propozycje + agreement + **top 3 kurierów** z rankingu
   (SLA% + gwiazdki, z `courier_ranking.compute_ranking`)

Uprawniony tylko `admin_id` z `config.json[telegram][admin_id]` (silent ignore
dla innych user_id).

## Dni tygodnia (Linux cron, 0-7)

| ID | Dzień |
|---|---|
| 0 | Niedziela (Sunday) |
| 1 | Poniedziałek |
| 2 | Wtorek |
| 3 | Środa |
| 4 | Czwartek |
| 5 | Piątek |
| 6 | Sobota |
| 7 | Niedziela (alias, rzadko używane) |

**Evening briefing pokrycie tygodnia:**
- `0 23 * * 0-4` → Sun, Mon, Tue, Wed, Thu (5 dni o 23:00)
- `59 23 * * 5,6` → Fri, Sat (2 dni o 23:59)
- **Razem: 7/7 dni** pokrytych, zero duplikatów, zero luk

## Dlaczego evening late w piątek/sobotę?

Operacja NadajeSz trwa dłużej w pt/sob (fala weekendowa dostaw po 22:00).
23:00 w te dni byłoby za wcześnie — briefing pokazałby niekompletne dane.
23:59 = "just before midnight", `format_evening()` i tak używa
`_today_range_utc()` = od 00:00 Warsaw dziś do `now`, więc pokazuje prawie
cały dzień (delivered, propozycje, agreement).

Niedziela wraca do 23:00 (operacja kończy się wcześniej).

## Wymagany plik env

- `/root/.openclaw/workspace/.secrets/telegram.env` — `TELEGRAM_BOT_TOKEN=...`
- `/root/.openclaw/workspace/scripts/config.json` — `telegram.admin_id` (int)

Bez tych plików `daily_briefing.py` crashuje z `RuntimeError` — cron log trafi
do `/root/backups/briefing.log` i należy go naprawić manual.

## Restore po rebuild / migracji

```bash
# 1. Upewnij się że kod jest zsynchronizowany z GitHub
cd /root/.openclaw/workspace/scripts/dispatch_v2 && git pull origin master

# 2. Zainstaluj cron (skopiuj zawartość z "Aktywne wpisy" wyżej do /tmp/cron_new.txt)
vim /tmp/cron_new.txt

# 3. Apply
crontab /tmp/cron_new.txt

# 4. Verify CRON_TZ na pierwszej linii
crontab -l | head -1
# Expected: CRON_TZ=Europe/Warsaw

# 5. Test briefing dry-run
cd /root/.openclaw/workspace/scripts && python3 -m dispatch_v2.daily_briefing evening --dry-run
```

## Historia zmian

| Data | Commit | Zmiana |
|---|---|---|
| 12.04.2026 | P0.5 (15493ea) | Pierwszy cron: hourly git push (F0 backup) |
| 13.04.2026 | F1.4b (23bfa7d) | Dodane briefing morning+evening + CRON_TZ=Warsaw |
| 13.04.2026 | F1.4b iteration | Update godzin: 08/22 → 09 + 23/23:59 weekend split |
| 13.04.2026 | F1.4c (535047c) | Dodany courier_ranking 23:30 daily |
| 13.04.2026 | F1.5 | `/etc/cron.d/certbot-renew` usunięty jako broken, renewal idzie przez `certbot.timer` systemd + pre/post/renew hooks w `/etc/letsencrypt/renewal/gps.nadajesz.pl.conf` |
| 14.04.2026 | F1.6 | Wyłączone 4 cron entries (morning/evening briefing + courier_ranking). Dane przeniesione do `/status` command w Telegram (3-w-1 on-demand). Kod zostaje jako manual option. |

## Open TECH_DEBT

- [ ] **P1 pre-existing fetch_schedule semantyka** — po dodaniu CRON_TZ jobs
  `0 6` i `0 8` fire teraz 06:00 + 08:00 Warsaw (wcześniej były 06:00 UTC =
  08:00 Warsaw + 08:00 UTC = 10:00 Warsaw). Shift -2h zaakceptowany jako case
  bug-fix (docstring autora mówił "06:00 Warsaw", wcześniej źle zapisane w UTC).
- [ ] **P2 cron logs rotation** — `/root/backups/briefing.log`,
  `/root/backups/git_push.log`, `/tmp/gastro_cron.log` rosną bez limitu.
  Dodać `logrotate` config albo cronjob truncating >100 MB.
- [ ] **P2 cron monitoring** — brak alerting gdy cron nie uruchomił się
  (np. crashed, crontab usunięty). Healthcheck via telegram_approver `/status`?
