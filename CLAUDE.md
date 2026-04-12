# CLAUDE.md — Ziomek V3.1 (dispatch_v2)

> **PIERWSZA INSTRUKCJA:** Zanim odpowiesz na JAKIEKOLWIEK pytanie albo napiszesz JAKIKOLWIEK kod, przeczytaj w tej kolejności wszystkie 4 pliki:
> 1. `docs/SKILL.md` (master V3.1 — architektura, 29 reguł, 12 faz)
> 2. `docs/TECH_DEBT.md` (bieżący stan, bugs, odłożone rzeczy)
> 3. `docs/FAZA_0_SPRINT.md` (plan dzisiejszy)
> 4. `docs/SYSTEM_FLOW.md` (flow end-to-end zlecenia)
>
> Następnie wykonaj rytuał startowy (sanity check produkcji). Dopiero potem proś o zgodę na działanie.

---

## Kontekst w 3 akapitach

**Adrian** jest ownerem **NadajeSz Białystok** — firma kurierska gastro-delivery. 30 kurierów, 55 restauracji, 1500-2000 orderów/tydz, revenue ~35-45k PLN/tydz transport + 70-90k GMV. Plan: ekspansja Warszawa + API dla aggregatorów (Restimo → Wolt Drive clone).

**Ziomek** to autonomiczny AI dispatcher który zastępuje ręczną pracę koordynatora. Decyzja per order: który kurier, jakim bagiem, w jakiej kolejności dostaw. Shadow mode (propozycje w Telegramie, Adrian akceptuje → Ziomek imituje koordynatora = sam loguje się do panelu i przypisuje) do uzyskania API Rutcom, potem autonomia.

**Ty (Claude Code)** pomagasz Adrianowi pisać, patchować i testować kod **na żywym serwerze produkcyjnym**. Pracujemy iteracyjnie: diagnoza → patch → test → restart. Zawsze backupy, zawsze testy przed restartem produkcji. Celem jest system **najsolidniejszy jakościowo, pancerny, skalowalny na wielomiastowość** — nie półśrodki, nie hacki, nie TODO bez kontekstu.

---

## Zasady współpracy (HARD)

- **Polski, konkretnie, bez lania wody.** Adrian nie znosi ogólnych zdań typu "powinniśmy rozważyć".
- **"Pytaj nie zgaduj"** — każde zgadywanie to 10-30 min debugu. Sprawdzaj realny kod (`grep`, `cat`, `sed -n`) przed patchem. Nie pisz funkcji z pamięci.
- **Weryfikuj empirycznie** — zanim napiszesz kod, sprawdź sygnatury, strukturę danych, zawartość plików.
- **Patchowanie małymi krokami:**
  1. `cp plik.py plik.py.bak-$(date +%Y%m%d-%H%M%S)`
  2. Python `str.replace` z `assert old in s` (nie `sed`, nie heredoki nad 50 linii)
  3. `python3 -m py_compile plik.py`
  4. Import check + test funkcjonalny
  5. Diff check (`diff bak current`)
  6. Restart serwisu **TYLKO** jeśli wymaga — i **TYLKO po zgodzie Adriana**
- **Dry-run z mockami przed restartem produkcji** — zawsze.
- **Tryb komunikacji:** krótko. Update "✅ X done, ⏳ Y w trakcie, szacunek N min". Długie analizy TYLKO przy realnych wątpliwościach architektonicznych.
- **Traktuj Adriana jak przedsiębiorcę** — patrz przez pryzmat przychodu, marży, wykonalności, prostoty wdrożenia, przewagi rynkowej. 2-5 najmocniejszych opcji zamiast 20 luźnych pomysłów.
- **Filozofia jakości:** najsolidniejsza opcja, pancerna, skalowalna na wielomiastowość. Nigdy hacki ani TODO bez kontekstu. Jeśli coś brzmi jak "na teraz" — zapisz w TECH_DEBT zamiast komentarza w kodzie.

## Hard NEVER

- Nie ruszaj produkcji bez `cp .bak-*` + testu
- Nie restartuj `systemd` bez py_compile + import check + **zgody Adriana**
- Nie reintroducuj `chromedp` — Python HTTP zostaje (D15 V3.1)
- Nie każ kurierowi czekać (D8) — R27 pickup window ±5 min
- Nie licz SLA od `now` dla assigned (D4) — od `picked_up_at`
- Nie używaj `jq` (brak w systemie) — użyj Python do JSON
- Nie dodawaj `tools.telegram`/`tools.exec.approval` do `openclaw.json` (crash gateway)
- Nie zapominaj Warsaw TZ dla panel timestamps
- Nie hardcoduj MAX_BAG_SIZE — zostało ODRZUCONE (zobacz stan P0.1 niżej)
- Nie zawieszaj orderów — zawsze best-effort + alert (R29)
- Nie implementuj premium SLA dzisiaj (D13 — odroczone do października 2026, jednolite SLA=35 min)
- Nie zaniżaj ETA kuriera dla restauracji "bo się spóźniają" — D16: mierzymy spóźnienia dla alertów, ale możemy wysłać kuriera +prep_variance min później (ochrona D8)

## Hard ALWAYS

- Czytaj SKILL.md + TECH_DEBT + FAZA_0_SPRINT + SYSTEM_FLOW na start
- Weryfikuj realny kod przed patchem
- Atomic writes: temp → fsync → rename
- Warsaw TZ: `from zoneinfo import ZoneInfo; WARSAW = ZoneInfo("Europe/Warsaw")`
- Update TECH_DEBT na koniec sesji
- Każda akceptacja Adriana w Telegramie = Ziomek loguje się i przypisuje sam (D15)

---

## STAN FAZY 0 (12.04.2026)

### ✅ P0.1 DONE — Dynamic MAX_BAG_SIZE (przeformułowane)

**Co zrobione:**
- `common.py` → `MAX_BAG_TSP_BRUTEFORCE = 5` + `MAX_BAG_SANITY_CAP = 8` (techniczne guardy, NIE biznesowe reguły)
- `scoring.py` i `feasibility.py` importują z `common` (jedno źródło prawdy)
- Stare `MAX_BAG_SIZE = 4` (scoring) i `= 6` (feasibility) USUNIĘTE
- `s_obciazenie` przepisane na linear decay: bag 0=100, 1=80, 2=60, 3=40, 4=20, 5+=0 (zamiast hard cutoff na 4)

**Filozofia (zaktualizowana podczas P0.1):**
> Wave size NIE jest biznesową regułą per tier. Wave size wynika z:
> - SLA 35 min per order (feasibility + TSP simulation)
> - Traffic multiplier (traffic.py)
> - Kurier + aktualna mapa
>
> Tier wpływa tylko na **scoring** (Faza 2 tier multiplier), NIE na hard limits.

**R3 V3.1 "hard cap 4 per tier" został ODRZUCONY** jako sprzeczny z D8 ("kurier zawsze w ruchu") i z obserwacji Adriana że Mateusz O w niedzielny peak (puste ulice) może mieć 5 orderów jeśli SLA się zmieści. **SKILL.md sekcja R3 wymaga aktualizacji na końcu dnia** → TECH_DEBT.

**Backup:** `*.bak-20260412-1020*`

### ✅ P0.2a DONE — time_penalty D4 (przez state_machine, nie scoring)

**Co zrobione:**
- `state_machine.py` → nowa funkcja `compute_oldest_picked_up_age_min(bag, now_utc)`
- Filtruje TYLKO `status == "picked_up"` (ordery `assigned` nie liczą się do SLA kuriera)
- Explicit `now_utc` parametr (caller musi podać, zero defaults) → deterministyczna, replay-able
- Parsuje 3 formaty timestampów (datetime/ISO/naive Warsaw panel)
- Raises ValueError dla `None` i naive datetime (fail fast)

**Decyzja architektoniczna:** scoring pozostaje **pure math** bez znajomości statusów/dat. Separacja odpowiedzialności pod wielomiastowość. Shadow dispatcher (Faza 1) woła `state_machine.compute_oldest_picked_up_age_min` PRZED `scoring.score_candidate`.

**Testy:** 12/12 PASS (pusty bag, tylko assigned, mixed, panel Warsaw, datetime obj, broken data, garbage input, determinism, Warsaw-ready caller)

**Backup:** `state_machine.py.bak-20260412-*`

### ⏸ P0.2b (R27 window) — ODROCZONE do Fazy 1

R27 wymaga `simulate_bag_route` zwracającego `predicted_arrival_at_pickup`. Obecny `route_simulator.py` v1 idzie do kosza (→ `route_simulator_v2` PDP-TSP w Fazie 1). Pisanie R27 dla v1 = martwy kod.

### ✅ P0.3 DONE — courier_resolver position priority (12.04)

**Co zrobione:**
- `common.py`: `parse_panel_timestamp` + `WARSAW` + `DT_MIN_UTC` (module-level, DRY dla wielomiastowości)
- `state_machine.py`: `_parse_picked_up_at` → wrapper na `common.parse_panel_timestamp`
- `courier_resolver.py`: docstring update + import + `_bag_sort_key` module-level + blok priorytetów

**Nowa reguła priorytetu:**
1. GPS fresh <5min (skip dziś)
2. Aktywny bag (picked_up > assigned, sorted by datetime malejąco) — iteracja gdy broken
3. last_delivered (fallback dla bag pusty/broken)
4. None

**Testy:**
- 9/9 P0.3 PASS (w tym test 7: broken newest + OK starszy → bierze OK + warning)
- 12/12 P0.2a regression PASS
- Live kurier 471: bug fix działa (był 'last_delivered', po patchu 'last_assigned_pickup' bo picked_up broken)

**Data quality discovery:** 12 kurierów produkcyjnie z broken delivery_coords → P0.4 krytyczny

**Backup:** `*.bak-20260412-112626` (3 pliki)

### ✅ P0.4 DONE — panel_watcher delivery_coords enrichment (12.04)

**Co zrobione:**
- `geocoding.py`: timeout parametryzowany w `_google_geocode` + `geocode()` (default 5s, watcher używa 2s)
- `panel_watcher.py`: import geocode top-level + `_diff_and_emit` inline geocoding delivery_address w NEW_ORDER
- `state_machine.py`: `delivery_coords` w NEW_ORDER upsert path

**Architektura:**
- Inline geocode w watcher hot path, timeout=2.0s (vs Google default 5s)
- Zero ThreadPoolExecutor — timeout u źródła w geocoding.py = zero race conditions, zero zombie threads
- Cache hit ~90% (237/262 unique delivery_addrs) = ~0ms
- Cache miss ~10% → Google API, max 2s
- Historical failure rate 0% (294/294 successful) = forward-fix only, retry NIE potrzebny

**Edge case handling:**
- geocode fail/timeout → delivery_coords=None + warning log
- P0.3 fall-through obsłuży orders bez delivery_coords (last_assigned_pickup albo last_delivered)

**Testy:**
- 9/9 P0.4 PASS (cache hit, timeout forced fail, cache hit ignoruje timeout, ev_payload stub)
- 3/3 P0.2a regression + P0.3 build_fleet_snapshot OK

**Live state przed restartem:** 29 aktywnych (3 planned, 17 assigned, 9 picked_up)

**Backup:** `*.bak-20260412-112626` (P0.3) + `*.bak-20260412-1219*` (P0.4)

### ✅ P0.5 DONE — OSRM haversine fallback + circuit breaker + traffic-aware speeds (12.04)

**Co zrobione:**
- common.py: FALLBACK_BASE_SPEEDS_KMH dict (5 bucketów), HAVERSINE_ROAD_FACTOR_BIALYSTOK=1.37,
  get_time_bucket(dt_utc), get_fallback_speed_kmh(dt_utc)
- osrm_client.py: circuit breaker state, hourly metrics, route()/table() zwracają
  zawsze dict/list z osrm_fallback+osrm_circuit_open+time_bucket flags.
  Timeout 5/10→3s konsekwentnie.
- tools/calibrate_road_factor.py: offline script (rerun przy ekspansji Warszawy,
  produkuje baseline JSON)

**Architektura (4 warstwy):**
- Traffic-aware fallback - 5 bucketów korków Białegostoku (weekday_rush 20 km/h,
  weekday_evening 24, weekend_evening 26, lunch_midday 28, off_peak 32) - oparte na
  realnym wzorcu korków, nie popycie
- Empirycznie skalibrowany HAVERSINE_ROAD_FACTOR_BIALYSTOK=1.37 - 206 delivered
  orders, median, walidacja fizyczna (długie trasy → 1.08 asymptotycznie)
- Circuit breaker - 3 consecutive failures → 60s skip OSRM, chroni watcher przed
  225s lagiem przy OSRM outage (75 calls × 3s timeout)
- Hourly metrics - log INFO co godzinę (total calls, fallback %, circuit opens),
  zamiast spam per-call warningów

**Kontrakt:**
- route() zawsze zwraca dict (nigdy None) - osrm_fallback:False gdy OSRM OK, True
  gdy fallback
- table() zawsze zwraca matrix (nigdy None) - pusta list tylko dla empty inputs
- Nowe pola: osrm_fallback, osrm_circuit_open, time_bucket (shadow dispatcher Fazy 1
  użyje)

**Testy:**
- 24/24 unit tests PASS (get_time_bucket 7, get_fallback_speed_kmh 2, haversine
  regression 1, mock timeout 3, mock 200 2, circuit breaker 5, table fallback 3,
  table empty 1)
- Live OSRM test: osrm_fallback:False, 5.6 min / 2.54 km (realistic)
- Regression: route_simulator + feasibility import OK

**Backup:** common.py.bak-20260412-135930, osrm_client.py.bak-20260412-135930

**Kalibracja baseline:** dispatch_state/calibration_20260412_baseline.json (state,
nie w git)

**Production:** NO RESTART required (osrm_client on-demand, shadow dispatcher Fazy 1
użyje fallbacka).

### ✅ P0.6 DONE — fetch_order_details recon (12.04)

**Co zrobione:** 10 sample orderów (statusy 2/3/5/7) → pełny dump pól
`zlecenie` (dict[50]) + `czas_kuriera` (top-level). Dump w
`/tmp/p06_order_details_sample.json`.

**DECYZJA:** `prep_ready_at` **nie istnieje** w panel API. Zero pól semantyki
"fizycznie gotowe w kuchni". Panel wie tylko deklarację (`czas_odbioru`) +
moment odbioru (`dzien_odbioru`) + doręczenia (`czas_doreczenia`).

**Bonus:** potwierdzona semantyka `czas_kuriera` = deklarowany przyjazd kuriera
do restauracji, ustawiany przez koordynatora (dropdown) lub kuriera
(jednorazowa ekstensja przy akceptacji). Kontrakt z restauracją ±5min liczy
się OD `czas_kuriera`. Historical `(czas_kuriera - czas_odbioru_timestamp)` per
restauracja = dodatkowy sygnał dla P0.7.

**Implikacja dla Fazy 1:** `prep_ready_at_estimate = czas_odbioru_timestamp +
prep_variance(restauracja)` — prep_variance policzyliśmy w P0.7.

**Backup:** N/A (zero kodu produkcyjnego, sama recon + dokumentacja)

**Commit:** `12285ef`

### ✅ P0.7 DONE — restaurant_meta.json gap-fill (12.04)

**Co zrobione:**
- **Nowy offline tool:** `/root/.openclaw/workspace/scripts/tools/gap_fill_restaurant_meta.py`
  (595 linii, stdlib-only, argparse --csv/--output/--dry-run, **POZA git repo**
  dispatch_v2 zgodnie z V3.2)
- **Wygenerowany:** `/root/.openclaw/workspace/dispatch_state/restaurant_meta.json`
  (115807 B = 113.1 KB, 68 restauracji)
- **Source:** `/tmp/zestawienie_all.csv` (9 plików panel CSV merged, 24007
  delivered orderów, 76 dni 2026-01-26 → 2026-04-12)

**Metryki per restauracja:**
- `prep_variance_min` (pickup_dt - czas_odbioru_timestamp)
- `waiting_time_sec` (oczekiwanie odbiór z CSV, z i bez zer)
- `extension_min` (czas_kuriera - czas_odbioru_timestamp)
- Flagi: `low_confidence`, `chronically_late`, `prep_variance_high`,
  `unreliable`, `critical` (z suppress dla low_confidence — zero
  false-positive alertów na <30 sample)
- Fallback: low_confidence dostaje `*_fallback_min/_sec` z fleet medians

**Fleet medians (z 57 restauracji sample_n≥30):**
- `prep_variance_median`: **13 min** — typowa restauracja deklaruje 13 min
  krócej niż realny prep
- `waiting_time_median_sec`: **0 s** — ekstensja koordynatora działa
- `extension_median_min`: **7 min** — typowo przedłuża o 7 min

**Biznesowe findings:**
- 62 active / 6 inactive (14-day window)
- **4 critical (>5% volume):** Grill Kebab 9.45%, Rany Julek 8.85%,
  Chicago Pizza 6.47%, Rukola Sienkiewicza 5.60% (razem 30.37% wolumenu)
- **19 prep_variance_high** (28% flotu deklaruje za krótko) — Aztek Tex-Mex
  worst case median=29 min
- **11 low_confidence** (sample_n<30) → fleet_median fallback aktywny
- **0 chronically_late** / **0 unreliable** po suppress — koordynator
  ekstensją kompensuje prep_variance (77.5% orderów zero wait)

**Kluczowy insight:** system koordynatora absorbuje prep_variance przez
ekstensję `czas_kuriera`. Faza 1 Ziomek musi replikować kompensację (bez tego
kurierzy będą czekać na jedzenie u 28% restauracji).

**Testy (7/7 etapów PASS):**
py_compile → dry-run → real run → clean struct verify → production rewrite
(auto-backup) → diff verify (276 linii, tylko timestamps) → readback sanity

**Backupy safety net w dispatch_state/:**
- `restaurant_meta.json.bak-PRE-P07-20260412-190421` (V3.1, 2081 B)
- `restaurant_meta.json.bak-20260412-192448` (3c smoke test, 123093 B)
- `restaurant_meta.json` (current clean struct, 115807 B)

**Production:** NO RESTART required. Meta dostępne dla Fazy 1
`route_simulator_v2`.

### ✅ P0.8 DONE — Final cleanup + meta integration note (12.04)

**Co zrobione:**
- Archive source CSV → `/root/archive/p07_source/` (10 plików, ~32 MB)
- Cleanup `/tmp` roboczych plików (p07_test.py, test_meta*, p06/p07
  diff/analysis/draft, demand_analysis_backup)
- Meta integration note dla Fazy 1 `route_simulator_v2` (w `docs/TECH_DEBT.md`):
  `pickup_ready_at = max(now, czas_odbioru_timestamp + prep_variance.median)`
  z fallback dla low_confidence (fleet medians 13/0/7)
- Final snapshots w `/root/backups/`:
  - `dispatch_v2_POST_FAZA_0_*.tar.gz` (440K)
  - `dispatch_state_POST_FAZA_0_*.tar.gz` (295K)
  - `tools_POST_FAZA_0_*.tar.gz` (26K)

**Production:** NO RESTART. Zero zmian w kodzie produkcyjnym dispatch_v2/.

---

## ✅ FAZA 0: 8/8 DONE (100%)

Commity:
- `602b476`  Initial commit
- `6d99416`  docs: Git workflow
- `d3ee6aa`  P0.3: courier position priority fix
- `214fe17`  P0.4: delivery_coords enrichment
- `15493ea`  P0.5: OSRM haversine fallback + circuit breaker
- `12285ef`  P0.6: RECON panel API (prep_ready_at nie istnieje)
- `bfd1dfc`  docs: DEMAND_ANALYSIS.md
- `57a5d34`  P0.7: gap_fill_restaurant_meta.py + restaurant_meta.json
- `[nowy]`   P0.8: Final cleanup + meta integration note

## ⏳ FAZA 1 START (po przerwie)

Shadow dispatcher — `route_simulator_v2` + `feasibility_v2` + `dispatch_pipeline`
+ `shadow_dispatcher` systemd + `telegram_approver`. Plan szczegółowy w CLAUDE.md
jest (docs/SKILL.md V3.1 sekcja Faza 1).

---

## Rytuał startowy (odpal to PIERWSZE)

```bash
# 1. Sanity check serwisów
systemctl is-active dispatch-panel-watcher dispatch-sla-tracker

# 2. Tail logów (ostatnie 3 linie każdy)
tail -3 /root/.openclaw/workspace/scripts/logs/watcher.log
tail -3 /root/.openclaw/workspace/scripts/logs/sla_tracker.log

# 3. State summary
python3 -c "
import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2 import state_machine
from collections import Counter
all_o = state_machine.get_all()
c = Counter(o.get('status','?') for o in all_o.values())
pu = sum(1 for o in all_o.values() if o.get('picked_up_at'))
print(f'State: {dict(c)}, with picked_up_at: {pu}')
"

# 4. Fleet snapshot
python3 -c "
import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2 import courier_resolver
fleet = courier_resolver.build_fleet_snapshot()
disp = courier_resolver.dispatchable_fleet(fleet)
print(f'Fleet: {len(fleet)}, dispatchable: {len(disp)}')
"

# 5. Recent shadow/learning (jeśli istnieją, po Fazie 1)
tail -5 /root/.openclaw/workspace/dispatch_state/shadow_decisions.jsonl 2>/dev/null
tail -5 /root/.openclaw/workspace/dispatch_state/learning_log.jsonl 2>/dev/null
```

Dopiero po tym pytaj: **"Adrian, lecimy P0.3?"**

---

## Runtime i dostęp

| Element | Wartość |
|---------|---------|
| Serwer | Hetzner CPX22, Ubuntu 24.04 |
| IP | 178.104.104.138 (UTC) |
| Runtime | OpenClaw 2026.3.27 w Docker |
| Kontener główny | `openclaw-openclaw-gateway-1` |
| Model AI | `openai/gpt-5.4-mini` primary, DeepSeek fallback |
| Telegram admin Adrian ID | 8765130486 |
| Bot dispatch | `@NadajeszBot` |
| Bot sterowanie | `@GastroBot` |
| Panel | `gastro.nadajesz.pl` (PHP/Laravel, agencja Rutcom) |
| Login sekrety | `/root/.openclaw/workspace/.secrets/panel.env` |

## Timezone (KRYTYCZNE)

- **Serwer = UTC**. Logi, `events.db`, `created_at` — wszystko UTC.
- **Panel używa Warsaw TZ** dla pól widocznych (`czas_odbioru_timestamp`, `dzien_odbioru`, `czas_doreczenia`). Format naive: `"2026-04-11 18:01:47"` (bez tzinfo, interpretować jako Warsaw).
- W kodzie: `from zoneinfo import ZoneInfo; WARSAW = ZoneInfo("Europe/Warsaw")`.
- Wielomiastowość (Warszawa) = ta sama TZ, zero refactoru. Ekspansja EU = refactor per city TZ (TECH_DEBT P2.3).

## Mapping statusów (`id_status_zamowienia`)

| ID | Status | Opis |
|----|--------|------|
| 2 | new | nowe, nieprzypisane |
| 3 | assigned | przypisane do kuriera |
| 5 | picked_up | kurier odebrał z restauracji |
| 7 | delivered | dostarczone |
| 8 | undelivered | cancelled by kurier |
| 9 | cancelled | anulowane |

`id_kurier = 26` = **Koordynator** (wirtualny bucket dla czasówek).

---

## Struktura katalogu
dispatch_v2/
├── CLAUDE.md                 ← JESTEŚ TU
├── docs/
│   ├── SKILL.md              ← master V3.1 (CZYTAJ PIERWSZY)
│   ├── TECH_DEBT.md
│   ├── FAZA_0_SPRINT.md
│   └── SYSTEM_FLOW.md
├── common.py                 ← ✅ P0.1
├── scoring.py                ← ✅ P0.1 (pure math)
├── feasibility.py            ← ✅ P0.1 (v1 do kosza w Fazie 1)
├── state_machine.py          ← ✅ P0.2a
├── courier_resolver.py       ← ⏳ P0.3
├── panel_watcher.py          ← ⏳ P0.4
├── osrm_client.py            ← ⏳ P0.5
├── route_simulator.py        ← 🗑️ v1, do zastąpienia w Fazie 1
├── geocoding.py
├── geometry.py
├── traffic.py
├── event_bus.py
├── .bak-                   ← backupy (ignoruj w searchu)
└── pycache/

## Sekrety i logi

- Sekrety: `/root/.openclaw/workspace/.secrets/` (panel.env, gmaps.env, traccar.env)
- Logi: `/root/.openclaw/workspace/scripts/logs/` (watcher.log, sla_tracker.log, sla_log.jsonl)
- State: `/root/.openclaw/workspace/dispatch_state/` (orders_state.json, events.db, geocode_cache.json, restaurant_coords.json, restaurant_meta.json, kurier_piny.json)

## Systemd units aktywne

- `dispatch-panel-watcher.service` — 20s cycle ✅
- `dispatch-sla-tracker.service` — 10s cycle ✅

---

## Przy każdej zmianie aktualizuj

1. **TECH_DEBT.md** — co odłożone, dlaczego, priorytet P0/P1/P2
2. **CLAUDE.md sekcja STAN FAZY 0** — dopisz kolejny ✅ Pn.X DONE z szczegółami
3. **Git (jeszcze nie zainicjowany, TECH_DEBT P2)** — na razie backupy `.bak-TIMESTAMP`

## Eskalacja do Adriana (Telegram: 8765130486)

- Production down
- Ziomek zaproponował absurd (guaranteed SLA violation)
- Data quality alarm (>10 orderów >90 min w dniu)
- Critical partner SLA spadł >10pp w 3 dni
- Fleet utilization <60% przez >4h
- Agreement rate <60% przez 3 dni (Ziomek przegrywa z intuicją = problem scoringu)
- **Każda prośba o restart systemd** — Adrian akceptuje LUB odrzuca

---

## Git workflow (OBOWIĄZKOWY)

Repo zainicjowane 12.04.2026, commit bazowy `602b476`.

**Przed każdym patchem:**
cd /root/.openclaw/workspace/scripts/dispatch_v2
git status

**Po każdym udanym patchu (py_compile OK + testy PASS):**
git add <zmienione pliki> docs/TECH_DEBT.md CLAUDE.md
git commit -m "Pn.X: <co zrobione>
Files: <pliki>
Tests: N/N PASS
Backup: <plik.py.bak-TIMESTAMP>
Production: active"

**Rollback trzy poziomy:**
1. Jeden plik: `git checkout <plik.py>`
2. Wszystko niecommitowane: `git reset --hard HEAD`
3. Totalna katastrofa: `bash /root/backups/ROLLBACK_NOW.sh`

Backupy `.bak-TIMESTAMP` nadal robione (double safety).
