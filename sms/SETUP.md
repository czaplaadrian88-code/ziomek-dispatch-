# MP-#9 SMS heartbeat — Adrian setup steps

Per master plan TOP-15 #9 + audit OPERATIONAL_RESILIENCE R3. Eliminuje
chicken-egg "Telegram bot down → admin alert via Telegram = gone" przez
out-of-band SMS heartbeat.

## Stan obecny (post-deploy MP-#9 code 2026-05-08)

Code ZALOŻONY i tested z stub providerem (`SMS_PROVIDER=stub` writes do
`dispatch_state/sms_log.jsonl`). Provider OVH READY do production. Adrian
musi tylko:

1. Założyć OVH account (~10 min)
2. Wystawić SMS service + sender label (~5 min, validacja może być online ~24h)
3. Wstawić credentials do `.env` (~2 min)
4. Enable systemd timer (~30s)

Total: ~15-20 min Adrian focused time.

## Krok 1 — OVH account + API credentials

### 1a. Create account (skip jeśli masz)
1. https://www.ovh.com/auth/?action=createaccount
2. Verify email, complete profile (NIP firmy / dane osobowe).

### 1b. Generate API keys (createApp)
1. https://eu.api.ovh.com/createApp/
2. Application name: `ziomek-tg-heartbeat`
3. Description: `Out-of-band SMS alert for Telegram bot watchdog`
4. Submit → otrzymasz:
   - `Application Key (AK)`
   - `Application Secret (AS)`
   Zapisz je — Application Secret pokazany TYLKO RAZ.

### 1c. Generate Consumer Key (one-time per-user auth)

Run tego command (z AK z poprzedniego kroku):
```bash
APP_KEY="<your_application_key>"
curl -X POST https://eu.api.ovh.com/1.0/auth/credential \
  -H "X-Ovh-Application: $APP_KEY" \
  -H "Content-Type: application/json" \
  -d '{"accessRules":[{"method":"POST","path":"/sms/*"},{"method":"GET","path":"/sms/*"}],"redirection":"https://www.ovh.com/auth/api/loginSuccess"}'
```

Response zawiera `consumerKey` i `validationUrl`. Otwórz `validationUrl` w przeglądarce, zaloguj się do OVH, autoryzuj.

Po autoryzacji `consumerKey` jest LIVE.

## Krok 2 — Buy SMS pack + setup sender

### 2a. Buy SMS pack
1. https://www.ovh.pl/telekomunikacja/sms/zamow.xml — wybierz pack 100 lub 500 SMS (tani — €5/100 SMS).
2. Płatność → po ~5 min serwis aktywny.
3. Manager: https://www.ovh.com/manager/#/telecom/sms — zobacz `serviceName` (np. `sms-aa12345-1`).

### 2b. Sender label (Adrian validacja może trwać 24h online)
1. Manager → SMS → Senders → Add new
2. Label: `Ziomek` (max 11 znaków)
3. Type: `Branded` (transactional)
4. Submit. OVH validuje (online ~5 min, albo 24h manual review).

## Krok 3 — Add credentials do `.env`

Edit `/root/.openclaw/workspace/.env`:
```bash
# MP-#9 SMS heartbeat (out-of-band Telegram bot watchdog)
SMS_PROVIDER=ovh
OVH_SMS_ENDPOINT=ovh-eu
OVH_SMS_APP_KEY=<your_application_key>
OVH_SMS_APP_SECRET=<your_application_secret>
OVH_SMS_CONSUMER_KEY=<your_consumer_key>
OVH_SMS_SERVICE_NAME=sms-aa12345-1   # z managera
OVH_SMS_SENDER=Ziomek
SMS_TARGET_NUMBER=+48XXXXXXXXX        # twój numer Adrian
```

## Krok 4 — Smoke test (przed enable timer)

```bash
cd /root/.openclaw/workspace/scripts
set -a && source /root/.openclaw/workspace/.env && set +a
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.sms.ovh test "Ziomek MP-#9 smoke test"
```

Expected output: `send result: True`. Otrzymasz SMS na `SMS_TARGET_NUMBER` w ciągu ~10s.

Jeśli error — sprawdź:
- Wszystkie env vars set (`echo $OVH_SMS_APP_KEY` itp.)
- Sender validated w manager (zwykle wymaga 5 min — 24h)
- SMS pack ma pozostały kredyt
- Consumer key validated (otworzyłeś `validationUrl` po createApp?)

## Krok 5 — Install + enable systemd timer

```bash
cd /root/.openclaw/workspace/scripts
sudo cp dispatch_v2/systemd/dispatch-tg-heartbeat.service /etc/systemd/system/
sudo cp dispatch_v2/systemd/dispatch-tg-heartbeat.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dispatch-tg-heartbeat.timer

# Verify
systemctl status dispatch-tg-heartbeat.timer
journalctl -u dispatch-tg-heartbeat.service --since "2 min ago"
```

Expected: timer active, oneshot service runs every 60s, journal pokazuje
`tg_heartbeat: OK` (success path).

## Krok 6 — End-to-end test (live outage simulation)

**OSTROŻNIE: ten test wyłącza bota — zrób off-peak.**

```bash
# Zatrzymaj dispatch-telegram żeby getMe failował
sudo systemctl stop dispatch-telegram
# Wait 3 minutes (heartbeat tickuje co 60s, threshold=3)
sleep 200
# Sprawdź journal — alert powinien być sent
journalctl -u dispatch-tg-heartbeat.service --since "5 min ago"
# Restart bot
sudo systemctl start dispatch-telegram
# Wait 1 min — recovery SMS powinien przyjść
sleep 70
journalctl -u dispatch-tg-heartbeat.service --since "1 min ago"
```

Expected: 1 SMS "Ziomek Telegram bot DOWN — 3 consecutive failures" + 1 SMS
"RECOVERY — back online po Xmin outage".

NIE trzeba — getMe zaczyna failować natychmiast po stop, ale heartbeat ma
threshold 3 (180s = 3 min) żeby uniknąć false-positives przy network blip.

## Stan watchdog (debug)

```bash
cat /root/.openclaw/workspace/dispatch_state/tg_heartbeat_state.json
```

Pola:
- `consecutive_failures` — current streak
- `last_success_ts` — epoch ostatniego successful getMe
- `alert_sent_for_current_outage` — dedup flag (True = SMS już wysłany w current outage)
- `first_failure_ts` — kiedy current outage started
- `last_alert_ts` / `last_recovery_alert_ts` — audit trail

## Cost estimate

OVH SMS Polish carrier: ~0.04 PLN per SMS.
Worst case: 2 SMS per Telegram outage (1 entry + 1 recovery).
Realistic frequency: <1 outage/month → <0.10 PLN/miesiąc operational cost.

100-SMS pack (€5 = ~21 PLN) starcza na lata.

## Rollback

```bash
sudo systemctl disable --now dispatch-tg-heartbeat.timer
sudo rm /etc/systemd/system/dispatch-tg-heartbeat.{service,timer}
sudo systemctl daemon-reload

# Optional: switch to stub provider
sed -i 's/^SMS_PROVIDER=.*/SMS_PROVIDER=stub/' /root/.openclaw/workspace/.env
```

Code framework pozostaje deployed (no-op gdy timer disabled).
