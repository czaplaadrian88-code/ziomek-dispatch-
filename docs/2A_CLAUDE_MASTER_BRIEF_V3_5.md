# CLAUDE MASTER BRIEF V3.5 — Ziomek Dispatch (14.04.2026)

**Zmiana vs V3.4:** Faza 1 DONE. 6 serwisów live. GPS PWA na gps.nadajesz.pl. F1.1-F1.6 wdrożone. Pierwsze propozycje Telegram 13.04 o 23:05. Krok 0-4 wykonane. Startujemy tydzień 2.

---

## Kontekst projektu

**Adrian Czapla**, owner NadajeSz Białystok. Buduje **Ziomek** — autonomiczny AI dispatcher zastępujący koordynatora. Faza 1 DONE (13.04). Tydzień 2 startuje 20.04.

**Twoja rola:** strategic advisor + architect. Claude Code pisze kod w tmux 'backup' na Hetzner. Adrian copy/paste prompty do CC.

---

## Stan serwera — co działa NA PRODUKCJI

### Serwisy systemd (wszystkie active)

```bash
systemctl is-active dispatch-panel-watcher dispatch-sla-tracker dispatch-shadow dispatch-telegram dispatch-gps nginx
# Oczekiwany output: active active active active active active
```

| Serwis | Opis | Kluczowe |
|---|---|---|
| dispatch-panel-watcher | scraping panelu co 20s | watcher.log, cycle ~5000+ |
| dispatch-sla-tracker | monitoring SLA 35 min | sla_tracker.log, 0 violations dziś |
| dispatch-shadow | shadow dispatcher | shadow_decisions.jsonl, ~1500 decyzji |
| dispatch-telegram | bot @NadajeszBot | telegram_approver.log, /status działa |
| dispatch-gps | GPS PWA server | port 8766, gps_positions_pwa.json |
| nginx | reverse proxy HTTPS | 443→8766, HTTP→HTTPS redirect |

### Git — 17 commitów master

```
7af8ce1  F1.5: GPS PWA — https://gps.nadajesz.pl
535047c  F1.4c: courier ranking (manual only)
3afeae4  F1.4b: CRON_SCHEDULE.md
23bfa7d  F1.4b: daily briefing (manual only)
2649ac7  F1.4a: /status komenda Telegram
f7ff9eb  F1.3: enrichment propozycji (km, ETA, adres, imiona)
4b7d1b4  F1.2: courier_names.json lookup
2df098e  F1.1 followup: TECH_DEBT
dd73048  F1.1: Faza 1 core modules (shadow+telegram+pipeline+routing)
0f574c1  P0.5b: Security TIER 0 (5 fixów)
154fb08  docs: V3.4 + SECURITY_FIXES_TIER0 + FAZA_1_DECYZJA_ARCH
0c80dee  docs: CLAUDE.md V3.3 + CLAUDE_WORKFLOW.md
+ 5 commitów Fazy 0
```

### Cron aktywny (CRON_TZ=Europe/Warsaw)

```
0 6 * * *      fetch_schedule pre-shift
0 8 * * *      fetch_schedule post-shift
0 * * * *      git push origin master (backup)
```

Wyłączone (zastąpione /status on-demand): daily_briefing, courier_ranking.

---

## Struktura plików

### Repo: /root/.openclaw/workspace/scripts/dispatch_v2/

```
dispatch_v2/
├── common.py              # WARSAW, get_time_bucket, get_fallback_speed_kmh, HAVERSINE=1.37
├── config.json            # paths, telegram, MAX_BAG_TSP_BRUTEFORCE=5
├── osrm_client.py         # table/route/haversine — NIGDY None po P0.5
├── geocoding.py           # atomic write + LOCK_EX (P0.5b fix)
├── state_machine.py       # atomic write + retry FileNotFoundError (P0.5b fix)
├── courier_resolver.py    # GPS PWA merge (primary) + Traccar (fallback) — F1.5
├── panel_client.py        # fetch_panel_html + _open_with_relogin (P0.5b fix)
├── event_bus.py           # emit/get_pending/mark_processed
├── traffic.py             # NIE używać dla OSRM fallback (D17)
├── scoring.py             # 4 komponenty, wagi 0.30/0.25/0.25/0.20
├── route_simulator.py     # v1 — NIE dotykać (feasibility v1 używa)
├── route_simulator_v2.py  # v2 — greedy hybrid D19 ← AKTYWNY
├── feasibility.py         # v1 — NIE dotykać
├── feasibility_v2.py      # v2 — SLA-first ← AKTYWNY
├── dispatch_pipeline.py   # assess_order → PipelineResult ← AKTYWNY
├── shadow_dispatcher.py   # systemd loop, NEW_ORDER → log ← AKTYWNY
├── telegram_approver.py   # asyncio bot: buttons + /status + learning ← AKTYWNY
├── courier_ranking.py     # manual run (cron wyłączony)
├── daily_briefing.py      # manual run (cron wyłączony)
├── gps_server.py          # HTTPS PWA, PIN, rate limit ← AKTYWNY
└── docs/
    ├── CLAUDE.md           # master brief (aktualizuj do V3.5)
    ├── CLAUDE_WORKFLOW.md  # zasady pracy CC
    ├── SECURITY_FIXES_TIER0.md
    ├── FAZA_1_DECYZJA_ARCH.md
    ├── TECH_DEBT.md
    └── CRON_SCHEDULE.md
```

### Pliki stanu: /root/.openclaw/workspace/dispatch_state/

```
orders_state.json          # główny state (atomic write)
geocode_cache.json         # 808+ adresów (atomic write + LOCK_EX)
gps_positions.json         # legacy Traccar (klucze = imiona)
gps_positions_pwa.json     # PWA GPS (klucze = courier_id) ← PRIMARY
courier_names.json         # courier_id → imię ("207": "Marek")
courier_pins.json          # PIN → {courier_id, name} dla GPS PWA
kurier_ids.json            # imię → courier_id (44 kurierów)
kurier_piny.json           # legacy (4-cyfrowe, nie courier_id — nie używać do lookup!)
restaurant_meta.json       # prep_variance, waiting_time, extension
pending_proposals.json     # awaiting_reply per order_id
schedule_today.json        # harmonogram z Google Sheets
```

### Logi: /root/.openclaw/workspace/scripts/logs/

```
watcher.log                # TICK co 20s, NEW/ASSIGNED/PICKED_UP/DELIVERED
sla_tracker.log            # SLA OK/violation per delivery
shadow_decisions.jsonl     # każda decyzja shadow (JSONL)
telegram_approver.log      # bot events
courier_resolver.log       # GPS source per kurier

/root/.openclaw/workspace/dispatch_state/learning_log.jsonl
                           # decyzje Adriana TAK/NIE/INNY/KOORD od 13.04
```

---

## Decyzje techniczne (obowiązujące)

### D19 — Routing algorithm
- bag_after_add ≤ 3 → brute-force PDP-TSP (optimal, max 6 permutacji)
- bag_after_add ≥ 4 → greedy insertion O(N²) (~25 ewaluacji dla bag=5)
- OR-Tools → Faza 9

### D17 — Traffic
- NIE używać `traffic.traffic_multiplier()` dla OSRM fallback
- Fallback: `get_fallback_speed_kmh(dt_utc)` z common.py (4 buckety)

### Panel API (krytyczne)
- `time` = minuty od teraz jako integer (nie timestamp!)
- `czas_odbioru_timestamp` = Warsaw TZ
- `czas_odbioru < 60` = elastyk, `≥ 60` = czasówka → Koordynator (id=26)
- CookieJar nie thread-safe → edit-zamowienie calls SEQUENTIAL
- `_open_with_relogin` wrapper w panel_client (P0.5b fix)
- Re-login tylko w `fetch_order_details` (nie w fetch_panel_html — ma własny)

### SLA i scoring
- SLA 35 min liczony od picked_up_at (nie od pojawienia się ordera!)
- Scoring: dystans×0.30 + obciążenie×0.25 + kierunek×0.25 + czas×0.20
- SPEED_FACTOR = 0.85 (motocykle szybsze niż Google Maps)
- Max delivery window: 35 min per order
- Max bag size: 6 (feasibility), 5 (routing brute-force)

### GPS
- gps_positions_pwa.json primary (courier_id keys)
- gps_positions.json fallback (imię keys → lookup przez kurier_ids.json)
- GPS freshness: 5 min
- courier_resolver mergeuje oba automatycznie

---

## Infrastruktura sieciowa

| Zasób | Adres | Uwagi |
|---|---|---|
| Hetzner CPX22 | 178.104.104.138 | główny serwer, Ubuntu 24.04, UTC |
| Panel Rutcom | gastro.nadajesz.pl (45.141.2.243) | nie nasz serwer! |
| GPS PWA | gps.nadajesz.pl → 178.104.104.138 | Cloudflare DNS only |
| GitHub | czaplaadrian88-code/ziomek-dispatch- | private, deploy key, cron push |

### Let's Encrypt
- Cert: /etc/letsencrypt/live/gps.nadajesz.pl/
- Expiry: 2026-07-12, auto-renew OK
- Deploy hook: renew_hook = systemctl reload nginx

### Nginx
- Config: /etc/nginx/sites-available/gps-nadajesz
- Rate limit: 10r/m per IP na /gps
- HTTP 80 → HTTPS 443 redirect

---

## Telegram bot — komendy i flow

### /status (3-w-1 on-demand)
```
🟢 Ziomek status (HH:MM)
Serwisy: ✅ watcher ✅ tracker ✅ shadow ✅ telegram ✅ gps ✅ nginx
Ordery: assigned:X picked_up:X planned:X delivered:X
Fleet aktywny: N
Dziś: Delivered:X | Propozycje:X | Agreement: X/Y = Z%
Wczoraj: Delivered:X | Top: Marek (12d, SLA 100%) | Grzegorz W (9d, 89%)
```

### Propozycja format
```
[#465625] 19:26 → Waszyngtona 23a
Zapiecek, dekl. 19:37

🎯 Marek (0.87) — 2.1 km, ETA 19:38, bag 2→3
🥈 Grzegorz W (0.72, 3.4km) | Bartek O. (0.68, 4.0km)

✓ feasible=4 best=207

TAK / NIE / INNY / KOORD
```

### Callbacks
- TAK → gastro_assign subprocess z courier_name + time
- NIE → log do learning_log.jsonl
- INNY → ack (pełne flow TODO tydzień 2)
- KOORD → gastro_assign --koordynator (id=26)
- Timeout 5 min → auto-KOORD

### Security (TIER 1 fix #1, tydzień 2)
- Aktualnie: brak weryfikacji chat_id
- Planowane: whitelist chat_id ↔ courier_id

---

## Learning system

### Stan aktualny
- `learning_log.jsonl` zbiera decyzje od 13.04 (TAK/NIE/INNY/KOORD + context)
- Surowy materiał, zero automatycznej analizy

### Plan
**Poziom 2 (21.04.2026, ~3h):** learning_analyzer.py
- Warunek: min 200 decyzji w learning_log.jsonl
- Wzorce: bag_size vs rejection rate, restaurant preferences, kurier preferences
- Output: rekomendacje zmian wag scoringu

**Poziom 3 (miesiąc 2):** ML fine-tuning wag na historii decyzji

---

## TECH_DEBT aktywny

### TIER 1 (tydzień 2, ~6h łącznie)
- Telegram bot security: chat_id whitelist (2h)
- Rate limiting panel_watcher: backoff przy błędach (15 min)
- OSRM boundary interpolation bucketów (1h)
- delivery_address w NEW_ORDER payload: weryfikacja czy panel_watcher emituje (30 min)

### TIER 2 (tydzień 3+)
- courier_resolver fallback: last_delivered zamiast active bag przy bag=4 (P1)
- kurier_piny.json vs courier_id: różne ID spaces (4-cyfrowe vs 3-cyfrowe)
- GPS coverage monitoring: ile kurierów ma świeże GPS
- PWA GPS login przez numer telefonu (gdy panel udostępni phones endpoint)
- MAX_BAG_SIZE=4 za mało operacyjnie (podnieść do 6 w scoring)
- JSONDecodeError retry w state_machine (do TECH_DEBT po obserwacji logów)

### Odłożone do Fazy 9
- OR-Tools VRPTW (D19 decision)
- Or-opt / 2-opt local search dla greedy
- Sliding window 15 min (D9)

---

## Priorytety tygodnia 2 (20-26.04)

### Blok 1 — Agreement rate baseline (pon-wt)
1. Analiza pierwszych TAK/NIE z learning_log.jsonl
2. Sprawdzenie delivery_address w propozycjach (F1.4 followup)
3. Rate limiting panel_watcher (15 min, TIER 1 fix #2)

### Blok 2 — Auto-approve (śr, ~2h)
Gdy agreement rate >60% przez 3 dni z rzędu:
- score >0.90 + feasible=wszyscy + nie best_effort + nie czasówka → auto TAK
- 60s okno COFNIJ
- [AUTO #465999] Marek @ 19:38 → Grill Kebab ✓

### Blok 3 — Learning analyzer (czw 21.04, ~3h)
Po 200+ decyzjach w learning_log.jsonl.

### Blok 4 — Restimo API skeleton (pt, ~3h)
FastAPI na Hetzner, PostgreSQL, OAuth2 + /quote + /order.

### Blok 5 — Telegram security (opcjonalnie, ~2h)
Weryfikacja chat_id ↔ courier_id.

---

## Kamienie milowe

```
14.04  → pierwsze TAK live (dziś)
17.04  → agreement rate >60%
21.04  → learning analyzer v1
26.04  → auto-approve live (score >0.90)
30.04  → Restimo API skeleton
10.05  → Ziomek autonomiczny (auto-approve >80%)
31.05  → Restimo produkcja
```

---

## Start sesji (rytuał V3.5)

```bash
# 1. Workflow
cat /root/.openclaw/workspace/scripts/dispatch_v2/docs/CLAUDE_WORKFLOW.md | head -50

# 2. Stan systemu
bash /root/morning_brief.sh

# 3. Health check
systemctl is-active dispatch-panel-watcher dispatch-sla-tracker dispatch-shadow dispatch-telegram dispatch-gps nginx

# 4. Ostatnie decyzje
tail -3 /root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl | \
  python3 -c "import sys,json; [print(json.loads(l).get('verdict','?'), json.loads(l).get('best',{}).get('name','?')) for l in sys.stdin]"

# 5. Learning log count
wc -l /root/.openclaw/workspace/dispatch_state/learning_log.jsonl 2>/dev/null

# 6. Git state
cd /root/.openclaw/workspace/scripts/dispatch_v2 && git log --oneline | head -5
```

Potem: "Adrian, co priorytet dnia?"

---

## Środowisko techniczne

- Serwer: Hetzner CPX22, 178.104.104.138, Ubuntu 24.04, UTC
- Docker: openclaw-openclaw-gateway-1 (OpenClaw 2026.3.27)
- CC: Claude Code v2.1.104, Opus 4.6 1M, Claude Max, tmux 'backup'
- Repo: /root/.openclaw/workspace/scripts/dispatch_v2/ (17 commitów, master)
- Remote: git@github.com:czaplaadrian88-code/ziomek-dispatch-.git
- Telegram: admin 8765130486, @NadajeszBot (dispatch), @GastroBot (control)
- GPS: https://gps.nadajesz.pl, PIN z courier_pins.json
- Scripts host path: /root/.openclaw/workspace/scripts/
- Docker container path: /home/node/
- subprocess calls z gastro_scoring.py → użyj host path /root/
