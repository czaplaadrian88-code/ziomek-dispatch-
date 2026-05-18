# ⚠️ STATUS — plik STATYCZNY od 2026-05-10 (snapshot, adnotacja 2026-05-18)

Bieżący stan projektu — sprinty, backlog, lekcje, „co robić teraz" — jest w katalogu **`memory/`** (`/root/.claude/projects/-root/memory/`): `sprint_timeline.md`, `tech_debt_backlog.md`, `lessons.md`. Ten plik = **reference historyczny** (konsolidacja Plików Wiedzy #1-#13, 04.05.2026). Reguły operacyjne / infra / Panel API niżej dalej obowiązują; sekcje „Current state" i historie wersji = zamrożone.

---

# ZIOMEK / NadajeSz — MASTER KNOWLEDGE FILE
**Wersja:** v1.0 (konsolidacja Pliki Wiedzy #1-#13 + Plan Pracy)
**Data:** 04.05.2026
**Owner:** Adrian Czapla <ac@nadajesz.pl>
**Cel:** jeden plik źródłowy dla CC i Adriana, eliminuje potrzebę kontekstu z chata.

---

## SPIS TREŚCI

I. CURRENT STATE (post-pivot 03.05.2026)
II. REFERENCE OPERATIONAL (filozofia, hard rules, kurierzy, dzielnice)
III. REFERENCE TECHNICAL (infra, panel API, paths, telegram)
IV. ML PIPELINE STATUS (Faza 0-7)
V. LESSONS LEARNED (#1-#70 skondensowane)
VI. ROADMAP (Tydzień 1-4 + Q3-Q4 + 2027+)
VII. SPRINT HISTORY ARCHIVE (1 paragraf per sprint)
VIII. ANULOWANE / WYCOFANE (decisions log)
IX. METADATA + POINTERS

---

# CZĘŚĆ I — CURRENT STATE (post-pivot 03.05.2026)

## I.1 TL;DR strategiczny

Adrian Czapla buduje **Ziomek** — autonomous rule-based dispatcher dla NadajeSz Białystok (food delivery, ~30 kurierów, ~40 restauracji, 180-300 orderów/dzień). 03.05.2026 strategic pivot: **odrzucenie ML-first approach** (Faza 7 LGBM PRIMARY CANCEL), pivot do **rule-based autonomy** (Faza 7-AUTO-PROXIMITY). Cel: zwolnić Adriana i Bartka z dispatch koordynacji w 2-3 tygodnie, żeby Adrian budował MVP własnej aplikacji + Bolt Food integration, a Bartek został Managerem Gastro (sales/growth focus, comp model performance-based).

**Cel po Tygodniu 4 (~30.05):** Ziomek autonomous 90%+, dispatch 1-2h Bartek/dzień zamiast 8h.

## I.2 ZASADY KARDYNALNE Z1+Z2+Z3 (formalizacja 30.04, NIENEGOCJOWALNE)

**Z1 — Autonomia jako primary goal**
> Dopóki Ziomek nie pracuje samodzielnie, każda godzina pracy przybliża go do autonomii. Niezależnie od dnia i pory.
>
> **Implikacja:** peak windows, weekendy, wieczory = wszystko fair game. Lekcja #34 (peak blackout) trwale wycofana 30.04. Lekcja #23 (max 6h sesja) wycofana 01.05.

**Z2 — Jakość ponad szybkość ZAWSZE**
> Jeśli coś może się zepsuć, albo robimy łaty na systemie, cofamy się o krok, szukamy przyczyny, żeby system nie był łatany, ale budowany na lata.
>
> **Implikacja:** każdy fix wymaga root cause understanding. Quick patches zakazane. Diagnose-not-rollback discipline (walidowana wielokrotnie). Walidowane dramatycznie 02.05 — 5h architectural fix vs 5min regex change = 3-5 lat zysku.

**Z3 — Buduj na lata, nie łata**
> Każda decyzja architektoniczna patrzy 3-5 lat horyzont (Warsaw, Restimo, Wolt Drive, SaaS multi-tenant), nie tylko "działa dziś".
>
> **Implikacja:** każdy nowy kawałek kodu ma być scalable, defendable, observable. Anti-pragmatic shortcuts (no `--break-system-packages`, no hardcoded values dla speed). Dedicated venv per moduł.

**Tension resolution:** Z1 (velocity tygodniowa) ≠ presja Z2/Z3 (velocity per-decyzja). Zwiększamy ilość godzin (Z1), NIE zwiększamy presji na każdą lokalną decyzję.

## I.3 Architektura post-pivot (od 03.05 wieczór)

**Stary plan (do 03.05):** "Behavioral Cloning + Hard Rules + Continuous Learning" — Faza 5-11 ML-driven autonomy.

**Nowy plan (od 03.05):** **Rule-based autonomy + observability stack + recovery infrastructure.**

| Faza | Status | Co |
|---|---|---|
| Phase 1 LIVE | DEPLOYED 03.05 | 9 fixów resilience + observability + czasówki |
| Phase 2 NEXT (Tyg 1-3) | START 04.05 | Faza 7-AUTO-PROXIMITY 30% → 70% → 100% autonomy |
| Phase 3 (Tyg 4+) | DESIGN | Bolt Food integration, MVP własna aplikacja design phase |
| Phase 4 (Q3-Q4 2026) | PLAN | MVP LIVE + nadajesz.pl marketing campaign + franczyza prep |
| Phase 5 (2027+) | STRATEGIC | franczyza scale + multi-city operations |

## I.4 Faza 7-AUTO-PROXIMITY (Etap 0 LIVE shadow od 06.05.2026 20:27 UTC)

**Status:** rule-based classifier shadow-only LIVE; calibration tydzień → Etap 2-3 (Telegram countdown + 30% flip ~15.05).

**Co to jest:** V3.27 baseline (proximity + R-rules + tier scoring) jako PRIMARY decision engine, autonomy gate progression 30% → 70% → 100% przez 2-3 tygodnie. **NIE ML, NIE bundle optimization.**

**Architecture (Etap 0 LIVE):**
- `auto_proximity_classifier.py` (280 LOC pure function): `classify_auto_route(result, fleet_snapshot, now, flags, order_event) → (route, reason)`. Routes: `AUTO` | `ACK` | `ALERT`.
- T1/T2/T3 thresholds w `DEFAULT_THRESHOLDS` (override-able via flags). 6 conditions C1-C6. 11 edge cases (czasowka, best_effort, solo_fallback, shift_end_edge, mass_fail, parser_degraded, frozen_window).
- `PipelineResult.auto_route` ('ACK' default) + `auto_route_reason` + `auto_route_context` dict; `_classify_and_set_auto_route` helper z defense-in-depth (classifier exception → fallback ACK + warning log).
- `shadow_dispatcher._serialize_result` emit auto_route fields top-level w shadow log JSON.
- `telegram_approver.format_proposal` linijka "🤖 PEWIEN — auto-przypisałbym {kurier} [{tier}] (margin +X)" gdy `decision.auto_route='AUTO'`.
- Hard rules zachowane (R-35MIN-MAX, R-DECLARED-TIME, R-SCHEDULE-AWARE).
- **Rollback (5s, hot-reload):** flag `AUTO_PROXIMITY_SHADOW_ONLY=false` → classifier returns ACK na wszystko.

**Threshold table T1 (gate 30% LIVE):**
- min_pool_feasible=2, min_score_margin=15.0 *(placeholder)*, tiers=(gold, std+), min_score=50.0, strict_gps=False

**Adrian decyzje 2026-05-06:**
- A1: GPS off OK + 5min po shift_start → synthetic position BIALYSTOK_CENTER (flag `ENABLE_AUTO_PROXIMITY_POST_SHIFT_5MIN`, default OFF)
- B-A: `gastro stop`/`gastro start` Telegram cmd wyłącza tylko AUTO (Etap 2)
- C-Y: czasówki KOORD T-60/T-50/T-40 = osobny track (Sprint A+B 06.05, NIE w spec Faza 7)
- ANULUJ uprawnienia: Adrian + Bartek (Etap 2)
- Czasówki ZAWSZE → ACK w T1 (Bartek wave-line)
- ALERT ZAWSZE human gate (no auto-KOORD T1-T3)

**Roadmap kolejnych etapów:**
| Etap | Trigger | Wysiłek |
|---|---|---|
| Etap 1 — Calibration (~13.05) | 7-day shadow obs, distribution analysis | 1-2h |
| Etap 2 — Telegram UX (~14-15.05) | Etap 1 ACK; auto_assign_executor coroutine + ANULUJ + 60s countdown + gastro stop/start cmd | 3-4h |
| Etap 3 — 30% LIVE (~15.05 Pt off-peak) | Etap 2 ACK + AUTO rate 25-35% + ALERT <5% + Adrian explicit "flip" | 1h |
| Etap 4 — 70% (~21.05) | T1 prod 5-day + override <15% | 1h |
| Etap 5 — 100% non-edge (~28.05) | T2 prod 5-day + override <8% | 1h |

## I.5 Status produkcji (stan 04.05.2026)

**LIVE Services (5/5 active):**
- `dispatch-shadow` (LGBM v1.1 LIVE shadow, ENABLE_LGBM_SHADOW=1)
- `dispatch-panel-watcher` (V3.28 motion-aware tuned, motion>=4)
- `dispatch-telegram` (V3.19i 2-part UX, TRASA + 8 reason buttons + KOORD)
- `dispatch-monitor-419` (continuous watching)
- `dispatch-czasowka.timer` (Fix 8 deployed, ALE empirycznie nie generuje propozycji — Track B Tydzień 1)

**Production state (post-sprint 06.05 wieczór):**
- USE_V2_PARSER=1, parser_version=v2
- known_ids_window_size=1482 (rolling 7-day)
- PARSER_STUCK_MOTION_THRESHOLD=4 (default, env tunable)
- LGBM_SHADOW log lines: visible per decision; pierwszy real prediction post 5 dni 100% fallback (oid=471122 06.05 16:53 UTC)
- AUTO_PROXIMITY shadow LIVE od 06.05 20:27 UTC — classifier liczy + Telegram pokazuje "🤖 PEWIEN" linijkę
- Czasówki schedule fix LIVE od 06.05 19:46 UTC — `dispatchable_fleet()` używany w czasowka_scheduler
- T-60/T-50/T-40 trigger active w `flags.json["CZASOWKA_TRIGGERS_MIN"]: [60, 50, 40]`
- Tests: 111/111 PASS (21 classifier + 6 integration + 71 czasówka + 13 schedule)
- Last commits: `bbb36fa` (parser_health snapshot consistency), `579f282` (active_ids structural fix), `3ce489e` (telegram pytest guard), `ea5df8b` (test mock fixture), `eb85b53` (Faza 7 agreement buttons), `d925a5b` (KONIEC hot-reload), `0aecbab` (replay_failed fleet fix), `03a4bdf` (parser_stuck false-neg test), `14b4e70` (Faza 7 Etap 0), `69223b3` (czasowka fleet fix)
- Last tags: `parser-health-snapshot-active-2026-05-07`, `parser-health-active-ids-2026-05-07`, `telegram-utils-pytest-guard-2026-05-07`, `parser-health-test-tg-mock-2026-05-07`, `faza7-agreement-buttons-2026-05-07`, `koniec-authorized-flags-hot-reload-2026-05-07`
- Branch: `sprint-07-05-event-bus-opcja-c` (**17 commits ahead** `master@10c754d`); master merge gate 10.05
- **Post 07.05 noc parser_health structural fix:** `active_ids = order_ids - closed_ids` (closed_ids dostarczany przez parser via DOM marker `data-idkurier` missing). CHECK 2 (DELTA) + CHECK 3 (STUCK) + `get_health_snapshot` używają active_*. CHECK 1 ZERO_OUTPUT zostaje na orders_in_panel. Eliminuje root cause spamu 17/dzień (panel zwraca all-today's IDs, order_ids plateauje wieczorem). Plus 3-warstwowa obrona Telegram leak z testów (PYTEST_CURRENT_TEST guard L1 + conftest.py L2 + per-file fixture L3). Tests 13/13 PASS + 22/22 Layer3 regression.

**Continuous monitoring overnight active:**
- Layer 2 motion-aware monitor (motion>=4 threshold)
- Layer 3 cross-validation (KnownIdsWindow auto-expire 7-day)
- Layer 4 endpoint :8888
- 6 auto-rollback triggers (Telegram alert + manual restart human-in-loop)

## I.6 CSV YoY findings (03.05.2026 vs 04.05.2025)

**Volume crisis confirmed:**
- 306 vs 640 orderów = **-52.2% YoY**
- 7 272 vs 14 031 zł = **-48.2% revenue**
- Avg order: 22 → 24 zł (+8.1%)

**Lunch vs Dinner shift (KRYTYCZNE dla MVP):**
- Lunch peak (11-14): -64% (najgorsze)
- Dinner peak (17-20): -35% (lżejsze)
- **Implikacja:** dinner = real defensive base, lunch market lost macro
- **MVP MUSI być dinner-focused** (UX, marketing, push notifications)

**Restaurant churn — MAIN DRIVER:**
- 18 restauracji GONE (-179 orderów)
- 10 SEVERE DROP (-120 orderów)
- 7 NEW (+29 orderów)
- 4 GROWING (Ogniomistrz +167%, Retrospekcja +25%, Bar Eljot +100%, Epic Pizza +100%)
- **Recovery target:** 50% z GONE+SEVERE = +150 orderów/dzień = ~450/dzień Q3 (target prawie zrobiony tylko retention!)

**Bundle pattern (rewizja LGBM):**
- 2025: 86.2% bundle ratio, avg size 3.6
- 2026: 65.8% bundle ratio, avg size 2.4
- Bundling ISTNIEJE w 66% przypadków mimo Z.B 0% — **timing artifact** (LGBM_SHADOW pool_size snapshot w decision moment, kurier dostaje bag potem)
- LGBM Faza 5.2 retrain = HOLD (investigation Tydzień 2-3, decyzja Tydzień 3)

## I.7 Bartek strategic input (03.05 wieczór)

**5 strategic moves Bartek zidentyfikował:**
1. Pozyskać nowe restauracje (sales)
2. Konwertować restauracje z własnymi kierowcami → outsource do NadajeSz
3. Bolt Food integration (draft umowy ready)
4. YoY data analysis (kto urósł, komu wyparowało) — CSV done 03.05
5. MVP własna aplikacja do zamawiania

**Bartek nowa rola:**
- Manager Gastro (pełnoetatowy, end-to-end odpowiedzialność)
- Performance bonus: **0.50 PLN per order delta above baseline 300/dzień**, cap 8000 PLN/m
- Implementation: Tydzień 1 conversation, Tydzień 2 formal draft, Tydzień 4 active

**Recovery campaign Tydzień 1 priorytety (per CSV):**
- Mama Thai Bistro (-64%, dawniej #1) — Bartek meeting next week
- Restauracja Kumar's (-81%) — sprawdzić
- Gęba w Niebieskim (GONE) — Bartek już rozmawiał, follow-up
- Szklanki Talerze (GONE) — Bartek wysyła dane do kalkulacji
- Picobello (GONE, 18 orderów potencjał)

---

# CZĘŚĆ II — REFERENCE OPERATIONAL

## II.1 Dispatch philosophy FILOZ-1..5 (Bartek+Adrian ground truth)

### FILOZ-1: Wave size matrix 3×3 (tier × pora dnia)

Nie stały limit. Matrix per tier × pora. Wariancja zależy też od korków i aktualnego volume.

| Tier | Off-peak | Normal | Peak |
|---|---|---|---|
| Gold | 2-4 | 3-4 | 3-6 (Gabriel cap=4) |
| Standard+ | 2-3 | 2-4 | 3-5 |
| Standard | 2 | 3 | 3-4 |

**Peak:** 11-14 LUB 17-20 Warsaw. Może się zmieniać (wieczór/lunch/15-18) zależnie od dnia.
Gold tier Sunday peak: 5-6 orders/wave, do 12 w bagu (2 waves). Hard limits blokują top performers systemicznie — dynamic cap target: `courier_tier × day_of_week × peak_hour`.

### FILOZ-2: Immediate assign default
> "Każde zlecenie staramy się dodać od razu. Przy pikach gdy nie nadążamy, po 2-4 min wrzucamy 3 zleceń naraz i to też wtedy staramy się po linii, odbiory ustawić żeby kurier nie musiał zawracać."

### FILOZ-3: Drop-zone filter SR bundles
> "Z jednej kuchni nie zawsze jest to najlepsze połączenie, bo czasami masz dwa zlecenia o podobnym czasie na to samo osiedle, a restauracje są obok siebie np. Raj, 350stopni i Grill Kebab, lub Mama Thai, Kaczorowskiego Rukola i Chinatown, więc lepiej nie brać wszystkiego tylko zbudować bundle pod adresy doręczeń."

Drop-side bundling > pickup-side bundling. Scoring: "same restaurant × drop_proximity_factor (0.0-1.0)" zamiast czystego "same restaurant +25".

### FILOZ-4: Wave anticipation / max 2 fale
> "Liczymy mniej więcej kiedy kurier skończy i łączymy mu już kolejne zlecenia, żeby odbierał partiami/falami. Nie robimy więcej niż 2 fale."

Bartek anticipation interleave 33% (Bartek 30.1%, Krystian 38.8%, Mateusz 33.5%, Gabriel 30.6%) vs Standard 20.5%. Gap +12.5pp. Fix: `wave_continuation_bonus`. gap<0 → +30, 0-10min → linear decay 30→0, >10min → 0.

### FILOZ-5: Directional awareness
> "Kierunek liczymy po szybkości danej drogi. Czasami kurier jedzie dużo km, ale akurat będzie na obwodnicy Białegostoku ul. Andersa i wtedy dajemy mu dalszy dowóz bo wiemy, że szybko dojedzie."

Defer (BUG-3 not confirmed na 40k, wymaga real GPS).

## II.2 Hard Rules LIVE

**HARD CONSTRAINT (never violated):**
- **R-35MIN-MAX** — max delivery 35 minut. Jedyny pierwotny hard warunek.
- **R-DECLARED-TIME** — `czas_kuriera ≥ czas_odbioru_timestamp` zawsze. Coordinator nigdy nie deklaruje przed restauracją.
- **R-SCHEDULE-AWARE V3.24-A** (LIVE od 22.04) — kurier feasible tylko w aktywnej zmianie. Cid bez mapping → HARD REJECT. Dropoff > shift_end+5min → HARD. Pickup > shift_end → HARD.
- **R-RESTAURANT-WAIT** — alert >20min waiting (target tighten 20→10 min, planned).
- **V3.28 PARSER-RESILIENCE Layer 1-4** (LIVE od 02.05) — universal regex `\d{5,7}` + ParserHealthMonitor (motion>=4 threshold) + KnownIdsWindow (rolling 7-day) + property-based tests + HTTP health endpoint :8888.

**SOFT (gradient, nie threshold):**
- **R-NO-WASTE** gradient table: gap 0 to −5min = +30 (ideal) → <−60min = −30; waste 0–5min = +30 → >45min = −30. Żaden threshold nie eliminuje z poolu — gradient only.
- **R-BUFFER-OK**: 5–15min preferred, do 40min jeśli najlepszy kandydat.
- **Bag caps SOFT gradient** — R1 spread, R8 pickup_span hard_cap.

**Priority order:** 1. overlap, 2. proximity, 3. R4, 4. tier, 5. bag.

## II.3 21 reguł — katalog (z Q&A 22.04, finalizacja status)

### CRITICAL (V3.25 zaimplementowane)
- **R-01 SCHEDULE-HARDENING** ✅ LIVE — Hard rejects: cid_unknown, pickup_post_shift, dropoff_post_shift+5min. Pre-shift gradient.
- **R-02 COURIER-SYNC + DISTRICTS** ✅ LIVE — Szymon Sadowski cid=522, Kuba OL→Std+, Krystian inactive.
- **R-03 TELEGRAM-OPS-PARSER** ✅ LIVE — `/zwolnij /zostaje /wraca /pauza` na grupie.
- **R-04 NEW-COURIER-CAP** — v1 ABANDONED, **v2.0 peak-quality philosophy LIVE** (01.05): peak_speed_med, on_time_rate, p90 latency. Gold tier: peak_speed_med ≤ 14 min.

### HIGH (V3.26 zaimplementowane / w toku)
- **R-05 SPEED-MULTIPLIER** — tier-based eta multiplier (fast=0.85, normal=1.0, slow=1.20).
- **R-06 MULTI-STOP-TRAJECTORY** — angle diff z bag route.
- **R-07 PICKUP-COLLISION** — gap <15min + diff restaurant = HARD REJECT.
- **R-08 PICKUP-EXTENSION-NEGOTIATION** — RESTAURANT_EXTENSION_TOLERANCE table. **STATUS: ANULOWANE 24.04** (Adrian explicit).
- **R-09 WAVE-GEOMETRIC-VETO** — wave_continuation veto jeśli km_from_last_drop > 3.0.
- **R-10 FLEET-LOAD-BALANCE** — bag balance z fleet_avg ±1.
- **R-11 TRANSPARENCY-RATIONALE** — `"dlaczego": "<top 3 factors>"` w Telegram.

### MEDIUM (V3.27+ status mixed)
- **R-12** restaurant-holding-detection — **STATUS: ANULOWANE 24.04**.
- **R-13** dedicated-courier — DEDICATED_COURIER_MAP +120 (Kacper Sa ↔ Sioux).
- **R-14** natural-wave-continuation — gap ∈[-2,+2] min = +20.
- **R-15** match-source-attribution — pole `"match_source"`.
- **R-16** recent-delivery-decrement — delivered w 10min → fresh_pos -5min.
- **R-17** tier-dynamic — quarterly re-tier z Adrian ACK.
- **R-18** districts-complete-sync — normalizacja ulic.

### LOW (post Q4)
- **R-19** late-evening-simple-mode — po 21:00 simplified scoring.
- **R-20** post-wave-pos-downgrade — wave ≥3 stops, pos confidence.
- **R-21** extended-shift-awareness — pending bag post-shift = auto /zostaje.

## II.4 4 BUGI fundamentalne (z Pliku #1, 21.04)

### BUG-1: Drop-zone vs Same-restaurant bundling
**Mylna hipoteza:** "Bartek robi drop-zone, Ziomek SR." **Rzeczywistość:** obaj robią SR (43% vs 33%), ale **Bartek WYBIERA SR z drop-clustering**. Standard bierze SR ślepo.

**Fix:** mnożnik × bonus_l1: same zone=1.0, adjacent=0.5, distant=0.0, Unknown=0.0 defensive.

### BUG-2: Wave anticipation
**Confirmed:** interleave% gold 33% vs std 20.5%, gap +12.5pp.
**Fix:** `wave_continuation_bonus`. gap<0 → +30, 0-10min → linear decay 30→0, >10min → 0.

### BUG-3: Directional awareness
**NOT CONFIRMED** na 40k (haversine proxy questionable). Defer — wymaga real GPS tracks.

### BUG-4: Tier×pora bag cap matrix
**Match 10/12 cells z ground truth Adriana.** Intuicja Bartka empirycznie potwierdzona.
**Fix:** courier_tiers.json keyed po cid, 3 sekcje bag/speed/bundle. Gabriel cap_override=4.

## II.5 Kurierzy current state (04.05.2026 post CSV YoY)

### 2026 active (~18 unique w sample dnia)

**Gold (manual only, peak_speed_med ≤ 14 min):**
| cid | Imię | peak_speed_med | Notatki |
|---|---|---|---|
| 123 | Bartek O | 13.2 | Benchmark, 28 orders/dzień avg, bundle 46.5%, OPW p90=5 |
| 413 | Mateusz O | 13.6 | Intentional part-time co-coordinator (R-04 v2.0 fix to się rozumie) |
| 179 | Gabriel J | 14.0 | cap_override peak=4/normal=4/off_peak=3, 19-20 orders/dzień |
| 61 | Krystian | INACTIVE | Permanent OFF od Q&A 22.04 |

**Standard+ (post R-04 v2.0 promotions 01.05):**
- Adrian R (400) — original
- Jakub OL/Kuba (370) — same person, original Std+ od 23.04 bump
- **Promoted 01.05:** Paweł SC (376), Michał K (393), Adrian Cit (457), Kacper Sa (502), Dariusz M (509)

**Top 2026 performers (z CSV):**
- Michał K — 22 orders/dzień
- Mateusz L — 22 orders/dzień
- Mateusz Bro — 21 orders/dzień
- Dariusz M (509) — 21 orders/dzień
- Andrei K, Michał Rom, Jakub OL — Std+ tier

**New (recently onboarded):**
- Szymon Sadowski (cid=522) — confirmed Q&A 22.04 (NIE Grzegorz Rogowski jak CC Faza A źle zmapował)
- Grzegorz Rogowski (cid=500) — generalization confirmed (90.03% post-promotion)

**Slow:**
- Artsem Km (504) — p90=2, bundle 19.4%
- Łukasz B (511) — p90=2, bundle 20.1%
- Michał Li (508) — p90=3 ale max=4, bundle 16.6% (paradoks)

**Special:**
- Albert Dec (cid=414) — V3.24-A handles (LIVE od 22.04, hack `COURIER_414_BLACKLIST_UNTIL` removed 23.04)
- Koordynator (cid=26) — virtual courier, holding bucket dla czasówek

### 2026 GONE (z 2025 top 10)
Krystian, Mateusz O (active ale part-time), Michał Tok, Adrian N, Mykyta K, Aleksander G, Marek, Patryk, Gerald C — wymaga investigation czemu odeszli (Tydzień 2-3 retention analysis).

## II.6 Białystok Districts — 28 oficjalnych + 4 outside-city

### 28 oficjalnych osiedli (info.bialystok.pl)
Centrum, Białostoczek, Sienkiewicza, Bojary, Piaski, Przydworcowe, Młodych, Antoniuk, Jaroszówka, Wygoda, Piasta I, Piasta II, Skorupy, Mickiewicza, Dojlidy, Bema, Kawaleryjskie, Nowe Miasto, Zielone Wzgórza, Starosielce, Słoneczny Stok, Leśna Dolina, Wysoki Stoczek, Dziesięciny I, Dziesięciny II, Bacieczki, Zawady, Dojlidy Górne.

### 4 outside-city (z operational adjacency)
- **Choroszcz** — adj: Bacieczki
- **Wasilków** — adj: Jaroszówka, Sienkiewicza
- **Kleosin** — adj: Ignatki-osiedle, Nowe Miasto, Kawaleryjskie
- **Ignatki-osiedle** — adj: Kleosin, Nowe Miasto, Kawaleryjskie

### Final adjacency map (~74 par, manually approved by Adrian)

**Śródmieście:**
- Centrum ↔ Przydworcowe, Piaski, Bojary, Mickiewicza, Piasta II, Sienkiewicza, Dojlidy
- Bojary ↔ Centrum, Piasta I, Piasta II, Sienkiewicza, Mickiewicza, Skorupy
- Piaski ↔ Centrum, Mickiewicza, Przydworcowe
- Mickiewicza ↔ Centrum, Dojlidy, Kawaleryjskie, Piaski, Piasta II, Skorupy, Bojary, Dojlidy Górne
- Sienkiewicza ↔ Wygoda, Bojary, Centrum, Białostoczek, Wasilków, Jaroszówka

**E/SE Dojlidy:**
- Dojlidy ↔ Skorupy, Mickiewicza, Dojlidy Górne, Centrum
- Dojlidy Górne ↔ Dojlidy, Mickiewicza
- Skorupy ↔ Dojlidy, Mickiewicza, Piasta I, Piasta II, Bojary
- Piasta I ↔ Bojary, Piasta II, Skorupy, Wygoda, Jaroszówka
- Piasta II ↔ Bojary, Mickiewicza, Centrum, Piasta I, Skorupy, Wygoda, Jaroszówka

**S/SW Kawaleryjskie:**
- Kawaleryjskie ↔ Nowe Miasto, Mickiewicza, Bema, Kleosin, Ignatki-osiedle
- Nowe Miasto ↔ Kawaleryjskie, Bema, Kleosin, Ignatki-osiedle
- Przydworcowe ↔ Centrum, Bema, Piaski
- Bema ↔ Przydworcowe, Kawaleryjskie, Nowe Miasto, Starosielce, Leśna Dolina, Zielone Wzgórza, Słoneczny Stok

**N/NE Jaroszówka/Wygoda/Białostoczek:**
- Wygoda ↔ Jaroszówka, Sienkiewicza, Piasta I, Piasta II
- Jaroszówka ↔ Wygoda, Wasilków, Sienkiewicza, Piasta I, Piasta II
- Białostoczek ↔ Sienkiewicza, Antoniuk, Zawady, Dziesięciny I, Dziesięciny II

**N/NW Antoniuk/Bacieczki cluster:**
- Antoniuk ↔ Młodych, Bacieczki, Wysoki Stoczek, Białostoczek, Leśna Dolina, Zielone Wzgórza
- Młodych ↔ Antoniuk, Słoneczny Stok, Wysoki Stoczek, Leśna Dolina, Bacieczki, Zielone Wzgórza
- Bacieczki ↔ Zawady, Antoniuk, Leśna Dolina, Wysoki Stoczek, Choroszcz, Młodych, Zielone Wzgórza, Słoneczny Stok
- Wysoki Stoczek ↔ Antoniuk, Młodych, Bacieczki, Dziesięciny I, Dziesięciny II, Zawady
- Zawady ↔ Bacieczki, Białostoczek, Wysoki Stoczek, Dziesięciny I, Dziesięciny II
- Dziesięciny I ↔ Dziesięciny II, Białostoczek, Wysoki Stoczek, Zawady
- Dziesięciny II ↔ Dziesięciny I, Białostoczek, Wysoki Stoczek, Zawady

**W Starosielce/Zielone Wzgórza cluster:**
- Starosielce ↔ Zielone Wzgórza, Leśna Dolina, Słoneczny Stok, Bema
- Leśna Dolina ↔ Starosielce, Bacieczki, Słoneczny Stok, Młodych, Antoniuk, Zielone Wzgórza, Bema
- Słoneczny Stok ↔ Leśna Dolina, Młodych, Starosielce, Zielone Wzgórza, Bacieczki, Bema
- Zielone Wzgórza ↔ Starosielce, Leśna Dolina, Bacieczki, Słoneczny Stok, Młodych, Antoniuk, Bema

**Centrum-Dojlidy** dodane jako adjacent mimo geograficznej odległości (Adrian: "często łączymy jak ktoś jedzie z centrum w stronę Dojlid").

### drop_proximity_factor
- same zone = 1.0
- adjacent (pair w BIALYSTOK_DISTRICT_ADJACENCY) = 0.5
- distant (brak w adjacency) = 0.0
- Unknown (ulica spoza 28 osiedli) = 0.0 defensive

**Symmetry test enforced pre-commit.**

## II.7 5-step mental model dispatchera (Q&A 22.04)

```
STEP 1 (FILTER operational): Kto fizycznie dostępny?
  - W grafiku? (Sheets + overrides)
  - Zwolniony? (/zwolnij)
  - Extension shift? (/zostaje)
  - Nowy + bag>=2? (HARD SKIP)

STEP 2 (FILTER fizyczne): Kto może to fizycznie zrobić?
  - Pickup collision >15min w bagu
  - Czas dotarcia + speed multiplier
  - Post-shift dropoff (>shift_end+5)

STEP 3 (FILTER jakościowe): Extension akceptowalny?
  - 0-10 min silent OK
  - 10-30 min akceptowalny jeśli brak lepszego
  - 30+ reject lub KOORD

STEP 4 (SCORING multi-criteria):
  - Trajectory match (po drodze) — NAJSILNIEJSZY
  - Geographic proximity
  - Bag load balance
  - Tier reliability
  - Wave continuation gap
  - Natural wave extension vs new direction
  - Extension penalty
  - Match source attribution

STEP 5 (DECISION + margines):
  - Top-1 wygrywa
  - "Wahanie" = 2 kandydatów równi → intuicja
  - Recent delivery decrement (kurier właśnie dowiózł = -5min fresh pos)
```

**Stan Ziomka (przed pivot):** robi ~50% kroku 4. Kroki 1-3 prawie nie istnieją. **V3.25-V3.27 sprinty pokryły STEP 1-3 + częściowo STEP 4-5.**

## II.8 Operational rules — rules of thumb

- **Czasówka = prep ≥60min** (`czas_odbioru` field, panel goes to Koordynator id=26 as holding bucket)
- **Elastyk = prep <60min** (coordinator declares arrival via 5-60min dropdown, restaurant gets callback with that time)
- **`czas_kuriera` (HH:MM, top-level)** — declared courier arrival time at restaurant (source: coordinator dropdown OR one-time courier extension on acceptance). **To jest contract commitment.**
- **Margines kontraktowy:** ±5 min od declared `czas_kuriera`. Kurier >5min late = NadajeSz contract breach. Restauracja >5min late (kurier waits) = restaurant breach → R16/R17 alert + `restaurant_violations.jsonl`.
- **Peak hours:** Lunch 11-14 Warsaw, Dinner 17-20 Warsaw (Z1 wycofało peak blackouts od 30.04)
- **Post-close volume:** 0-3 orderów/h
- **Adrian + Bartek** oboje koordynują i są kurierami; Bartek daje zlecenia "żeby każdy miał co robić i ile jest w stanie"


---

# CZĘŚĆ III — REFERENCE TECHNICAL

## III.1 Infrastruktura

**Serwer:** Hetzner CPX32, 4 vCPU/8GB AMD EPYC Genoa, IP 178.104.104.138, Ubuntu 24.04, UTC.
- Upgrade z CPX22 wykonany 27.04.2026 (post-Big-Bang sprint)
- OSRM Docker `osrm-server`, image ghcr.io/project-osrm/osrm-backend, MLD algorithm, port 5001, mapa Podlaskie
- `docker update --restart=unless-stopped osrm-server` (persistence po reboot)

**Python venvs (Z3 dedicated per moduł):**
- `/root/.openclaw/venvs/dispatch/` — Python 3.12.3, główny dispatch
- `/root/.openclaw/venvs/ml_data_prep/` — Python 3.12.3, ML pipeline

**Code paths:**
```
/root/.openclaw/workspace/scripts/dispatch_v2/   — Ziomek code główny
/root/.openclaw/workspace/scripts/ml_data_prep/  — ML pipeline (Faza 0-5)
  ├── data/datasets/v2.0/  — Faza 3 + Faza 4 outputs
  └── models/v1.1/  — Faza 5.1 LGBM Ranker LIVE
    ├── lgbm_ranker.txt
    ├── encoders.pkl
    ├── feature_columns.json (42 cols)
    └── manifest.json (schema_version 1.1)
/root/.openclaw/workspace/scripts/  — schedule_utils.py (T3)
```

**State files (`/root/.openclaw/workspace/dispatch_state/`):**
- `learning_log.jsonl` — wszystkie learning signals (PANEL_OVERRIDE, ASSIGN_DIRECT, REPLY_OVERRIDE, OPERATOR_COMMENT)
- `events.db` — event_bus state (SQLite)
- `schedule_today.json` — V3.24-A cache (T3 hot-refresh, TTL 10 min)
- `courier_tiers.json` — R-04 v2.0 LIVE tiers
- `kurier_piny.json` — auth (PIN per kurier)
- `kurier_ids.json` — courier name → cid mapping
- `manual_overrides_excluded.json` — `/zwolnij` storage (cid-based od V3.26)
- `manual_overrides_extended.json` — `/zostaje` storage
- `restaurant_violations.jsonl` — R16/R17 alerts
- `rule_weights.json` — adaptive penalties R1/R5/R8 (auto-calibration by learning_analyzer po 50+ TAK/NIE)

**Logs (`/root/.openclaw/workspace/scripts/logs/`):**
- `watcher.log`
- `dispatch.log`
- `czasowka.log`

**Health endpoint:** http://localhost:8888/health/parser (V3.28 Layer 4, JSON z anomaly_reason, parser_version, known_ids_window_size)

**Systemd services LIVE:**
- `dispatch-shadow.service` — main dispatcher
- `dispatch-panel-watcher.service` — V3.28 motion-aware
- `dispatch-telegram.service` — V3.19i bot
- `dispatch-monitor-419.service` — continuous watching 3 sources
- `dispatch-czasowka.timer` (1-min interval) — czasówki scheduler
- `dispatch-sla-tracker.service`
- `dispatch-cod-weekly.timer` — COD weekly
- `dispatch-daily-accounting.service`
- `courier-api.service` — port 8767 FastAPI

**Legacy cron (DISABLED 22.04):** `gastro_trigger`, `gastro_koordynator`.

**Git:** github.com/czaplaadrian88-code/ziomek-dispatch- (Adrian Czapla <ac@nadajesz.pl>)

## III.2 Panel Rutcom + API (gastro.nadajesz.pl)

### Endpoints
- **Detail endpoint:** `POST /admin2017/new/orders/edit-zamowienie`, body `_token + id_zlecenie`, returns `{"zlecenie":{...}}`
- **Login:** session-based, CSRF tokens required, **CookieJar NOT thread-safe** — `edit-zamowienie` calls muszą być sequential
- **Cache TTL discovered:** 20 min (NIE 22 jak hipoteza)
- **Pre-warm:** `panel_client.login(force=True)` na startup → 87% latency reduction (6748ms cold → 841ms warm)

### Critical field mappings
- **`czas_odbioru_timestamp`** = Warsaw time (NIE UTC) — actual pickup, updated when coordinator changes
- **`created_at`** = UTC (suffix Z)
- **`czas_odbioru`** = int, minutes of prep time:
  - **<60 = elastyk** (coordinator declares arrival via 5-60min dropdown)
  - **≥60 = czasówka** (goes to Koordynator id=26 as holding bucket; `czas_odbioru_timestamp` = hard restaurant declaration)
- **`czas_kuriera`** (top-level, HH:MM) = declared courier arrival time at restaurant. Contrast ≠ `Do odbioru` column. **Contract commitment.**
- **`id_status_zamowienia`:**
  - 2 = new/unassigned
  - 3 = en route
  - 4 = waiting at restaurant
  - 5 = picked up
  - 6 = delayed
  - 7 = delivered
  - 8 = not collected (cancelled by courier)
  - 9 = cancelled
  - **Panel watcher ignores 7, 8, 9.**
- **`id_kurier`** — `26 = Koordynator virtual courier dla czasówek`
- **`time` parameter w assign endpoint** = integer minutes from now (NIE timestamp, NIE HH:MM string)

### Counter rollover lesson (02.05.2026 timebomb)
**Bug:** panel_client.py:223 hardcoded regex `r"id:\s*(46\d{4})"` — timebomb od V3.19f / Initial commit (4-5 lat).
**Trigger:** 01.05 ~20:06 ID rolnął 469999 → 470000, regex stuck, Ziomek silent 16h.
**Fix V3.28:** universal pattern `\d{5,7}` (Layer 1) + 4-layer defense-in-depth.
**Lesson:** każdy parser/extractor wymaga property-based parametric tests (8+ prefixes × N lengths).

## III.3 V3.28 Parser Resilience — 4 Layer Defense Architecture (LIVE od 02.05)

### Layer 1: parse_panel_html v2 (universal regex)
- Universal `\d{5,7}` pattern (zamiast hardcoded 46\d{4})
- Pure regex stdlib (zero deps, Z3 win)
- Cross-source delta check (DOM vs JS)
- Real production proof: 350+ vs v1 stuck 180

### Layer 2: ParserHealthMonitor (motion-aware anomaly detection)
- 4 checks: STUCK / ZERO_OUTPUT / DELTA_SPIKE / ASYMMETRY
- Per-severity cooldown (5 min critical / 30 min warning)
- Atomic state save (temp+fsync+rename)
- **Motion-aware adaptive STUCK detection** (Lekcja #66, ETAP 1)
- **Motion threshold tuned >=4** (default, env tunable `PARSER_STUCK_MOTION_THRESHOLD`)

### Layer 3: KnownIdsWindow + cross_validate
- Rolling 7-day window (1352 IDs bootstrap → 1482 organic growth)
- 4 set checks: ASSIGNED_ORPHAN / PACKS_LEAK / REST_ORPHAN / CLOSED_ORPHAN
- UPLIFT mechanism (orphan_size > 5 → critical)

### Layer 4: Tests + observability
- 35 property-based tests (8 prefixes × 3 lengths = 24 parametric + 11 edge)
- 22 integration tests (Layer 2/3 + motion-aware + threshold tuning)
- HTTP health endpoint :8888 (stdlib http.server, zero deps)

**Total: 95/95 V3.28 tests pass (post-TICKET-1 motion threshold tuning).**

## III.4 Telegram Bot Reference

**Grupa "Grupa ziomka"** — chat_id `-5149910559`, 4 członków. Bot service: `dispatch-telegram`.

**Bots:**
- **Dispatch bot:** @NadajeszBot (token `8571778075:AAE3lidXU9ijX2FNPpJRxd5DyfIu6FTwYp0`, chat ID `8765130486`)
- **Control bot:** @GastroBot (token `8770101598:AAF-x2srerA7ES-fzlehd7SY4kn5gUUcvsQ`) — handles `/gastrostop`, `/gastrostart`, `/dispatstat`

**Format obecny (V3.19i 2-part, LIVE od 30.04):**
- Part 1: TRASA + score + reason summary
- Part 2: 8 reason buttons + KOORD button
- Buttons: zielony (zatwierdź) / INNY (manual override) / KOORD (czasówka) / 8 reason codes

**Settings:**
- `ENABLE_TELEGRAM_FREETEXT_ASSIGN=False` (free-text NIE assignuje, idzie do `OPERATOR_COMMENT` w learning_log)
- Single-order approval flow (NIE batch)

**Format Tydzień 1 NEW (planowany pod Faza 7):**
1. **AUTO ASSIGNED** (informacyjny, 60s override window)
2. **WYMAGA ACK** (low-confidence)
3. **ALERT** (critical/degraded/edge cases)

**Telegram learning signals (do `learning_log.jsonl`):**
- `PANEL_OVERRIDE` (coordinator chose different courier via panel directly) — **PRIMARY training signal**
- `ASSIGN_DIRECT` (per-courier Telegram button click)
- `REPLY_OVERRIDE` (free-text override)
- `OPERATOR_COMMENT` (free-text na grupie)
- `TG_REASON` (8 reason buttons V3.19i — secondary signal, low adoption confirmed)

**Telegram ops parser (V3.25 R-03, LIVE):**
- `/zwolnij <imię>` — wyklucz kuriera (manual_overrides_excluded.json)
- `/zostaje <imię> <czas>` — extension shift (manual_overrides_extended.json)
- `/wraca <imię>` — usuń z exclusions
- `/pauza <imię>` — temporary pause
- Auth: Adrian + Bartek telegram_ids
- Fuzzy match threshold 0.85, case-insensitive
- TTL: do 3:00 Warsaw (end of operational day)

**Cardinal rules:**
- **Telegram NIGDY restart bez explicit ACK w czacie**
- Restart serwisów peak (11-14, 17-20) tylko jeśli Adrian explicit OK (Z1 wycofało blackout)

## III.5 Courier App / GPS

**APK:** `https://gps.nadajesz.pl/apk/courier.apk`
- Package: `pl.nadajesz.courier`
- Stack: Kotlin + Compose
- Auth: PIN z `kurier_piny.json`, UUID token, auto-logout 90min

**Backend:**
- FastAPI on port 8767, SQLite WAL
- Dual-write `gps_positions_pwa.json`
- Service: `courier-api.service`

**Ports:**
- 8765 = legacy Traccar
- 8766 = PWA gps_server
- 8767 = courier-api
- 8888 = V3.28 health endpoint

**Admin panel:** `https://gps.nadajesz.pl/panel` (admin/nadajesz2026)
- Stack: HTMX + Tailwind + Leaflet + SSE 5s

## III.6 Schedule Integration V3.24-A (LIVE od 22.04)

**Sheet ID:** `1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8`
**gid:** `533254920`
**TTL:** 10 min hot-refresh (T3 LIVE od 01.05, eliminuje 8h freeze)
**Cron fallback:** 06:00 + 08:00 Warsaw (legacy, T3 supersedes)

**V3.24-A mechanizm:** dla każdego kandydata Ziomek czyta grafik i sprawdza:
- Czy kurier ma aktywną zmianę w momencie decision_ts?
- Pickup < shift_start - tolerance 30min → PRE_SHIFT penalty gradient
- Pickup > shift_end → POST_SHIFT hard reject (V3.25 R-01 podniosło z soft do hard)
- Dropoff > shift_end + 5min → extension penalty 0/-10/-50/-100/-200 dla 5/15/30/45/60 min
- Dropoff > shift_end + 60min → hard reject
- **Cold start zone:** kurier <30min od start zmiany = candidate z soft penalty

**Pickup clamp:** jeśli kurier aktywny ale shift_start > pickup_ready_at, Ziomek clamp'uje pickup do shift_start.

## III.7 Czasówka scheduler V3.24-B (LIVE, ALE empirical issue)

**Mechanizm progresywnej selektywności:**
- 60min do pickup: proponuj tylko ideal match (km ≤ 1.0 AND drop_proximity ≥ 0.5)
- 50min: proponuj good match (km ≤ 2.0 OR drop_proximity ≥ 0.5)
- 40min: proponuj każdego feasible
- <40min: force assign — sub-optimal acceptable

**Early morning gate 9:10 Warsaw** — przed 9:10 nie emit (większość kurierów dopiero zaczyna).

**STATUS 04.05:** Fix 8 deployed 03.05 ALE empirycznie nie generuje propozycji (cały dzień 03.05 = 1 KOORD, expected 5-15). **Investigation TRACK B Tydzień 1 priority.**

## III.8 ML Pipeline status (Faza 0-7)

### LIVE
- **Faza 0+1+2 v2** ✅ — `address_cache.json` (9,464 buildings, 96.9% coverage), `world_state.parquet` (43,610 rows, 95.6% high quality), `available_pool.parquet` (402,749 pairwise pairs)
- **Faza 3 Pairwise Dataset v1.0** ✅ READY — 399,361 pairs / 5.5 mc, 36 cols base schema
- **Faza 4 Feature Engineering v2.0** ✅ READY — 78 cols × 6 feature groups (distance, districts, time, bag, bag-districts, pool-context). DistrictReverseLookup (kd-tree z 8.6K nodes), 92% OSRM coverage, 5.5 mc data window (Listopad 2025 → Maj 2026)
- **Faza 5.1 LGBM Ranker v1.1** ✅ LIVE — NDCG@5=0.852, pairwise accuracy 88.45%, training time 5.7 sec, 164 trees, 42 features (post-Lekcja #59 features-jako-balast cleanup)
- **Faza 6 LGBM Shadow** ✅ LIVE od 01.05 — `ENABLE_LGBM_SHADOW=1`, dispatch-shadow service, 6 fallback paths defense-in-depth (all_bag_zero, lgbm_error, feature_compute_error, latency_timeout, model_not_loaded, OOV encoder), inference 31ms per decision

### CANCEL/HOLD
- **Faza 7 LGBM PRIMARY** ❌ CANCEL 03.05 (kosmetyczna zmiana, LGBM zwraca fallback do V3.27 baseline w 100% przypadków przy pool_size=0)
- **Faza 5.2 retrain LGBM v1.2** 🟡 HOLD — investigation timing artifact (LGBM_SHADOW pool_size snapshot vs assignment time), decyzja Tydzień 3
- **Faza 7-AUTO-PROXIMITY** ✅ GO 04.05 — V3.27 baseline jako PRIMARY, autonomy gate 30%/70%/100%
- **Faza 8 ALT Explorer** ❌ CANCEL — zbędne dla rule-based autonomy
- **Faza 10 A/B Test 30% Peak** ❌ CANCEL — zastąpione liniowym progression Faza 7

### Critical features (per #469834 diagnoza, gdyby wracać do ML)
- `deliv_spread_km` (high priority — was missing, root cause #469834)
- `drop_district_adjacency_to_bag_drops` (boolean)
- `pickup_district_known` (data quality flag)
- `bag_drops_district_set` (set of districts)
- `last_drop_district` (for R06-classic)


---

# CZĘŚĆ IV — ML PIPELINE STATUS (Faza 0-7) — patrz III.8

(Skondensowane w sekcji III.8 — ML pipeline jest sekcją techniczną, nie operacyjną.)

---

# CZĘŚĆ V — LESSONS LEARNED (#1-#70 skondensowane)

## V.1 Lekcje techniczne fundamenty (#1-#9, plik #1)

**#1 — Parse wrapper invisible data loss.** Każdy parse wrapper musi logować unhandled top-level keys. Invisible data loss > verbose logs. (Lesson z `panel_client.fetch_order_details` dropping `czas_kuriera` przez F2.*)

**#2 — Flag bez konsumenta = bug.** Nowa flaga → grep że kod ją czyta. Niezaimplementowana → suffix `_PLANNED`. Audit przed flipem w shadow.

**#3 — Serializer LOCATION A + B discipline.** Każde nowe pole scoring/feasibility → 4 miejsca: `_serialize_candidate` LOCATION A, inline best LOCATION B, integration test na learning_log entry, learning_analyzer readers.

**#4 — CC self-contradiction po długiej sesji.** W sesji 10h+ CC zaczyna zakładać "już zrobione X" z pamięci. Zasada: grep first, never assume. RSS >1.2 GB = checkpoint + alert.

**#5 — Ground truth > statistical discovery.** Adriana wiedza (tier kurierów, dzielnice, peak hours, customer patterns) wygrywa z statystycznym clusteringiem CC. CC weryfikuje na danych, nie zgaduje.

**#6 — Post-restart shadow warm-up ~5-6 min.** Pierwszy PROCESS event ~5-6 min po restart (NIE 10s). Verify: poczekaj 5-6 min zanim checkujesz processed counter.

**#7 — Systemd TimeoutStopSec default za krótki.** Panel-watcher SIGKILL po 15s timeout = niedokończony write. Fix: TimeoutStopSec=120s panel-watcher + 60s shadow.

**#8 — Minimum-viable implementation > full architecture.** MVP z flag default False + shadow + side-by-side validation + incremental flip.

**#9 — Cognitive fatigue guards.** Strategic decisions (tier policy, scoring formula, BAG cap thresholds) — nie na końcu 10h sesji. Implementacja może być kontynuowana, ale nie nowe architectural choices.

## V.2 Lekcje meta z Q&A (#10-#12, plik #2)

**#10 (LESSON-QA-8) — Claude cognitive drift.** Po 4h sesji obligatory STOP + re-grep CLAUDE.md. Po 6h propose session close. Q&A: max 3 cases per batch, re-verify między batches. (Wycofana 01.05 jako Lekcja #23 — sesje 12-15h dopuszczalne na good-form days.)

**#11 (LESSON-QA-9) — Operational awareness > scoring quality.** Naprawa systemu informacyjnego (Ziomek widzi rzeczywistość) ma WYŻSZY priorytet niż tuning scoring. R-03 (2h, NIE zmienia scoring) > R-05 (6-10h scoring).

**#12 (LESSON-QA-10) — Rule gradient nie threshold.** Binary rule "nowy=skip" zawodzi gdy nowy ma obiektywną przewagę +63. Gradient z 3-5 buckets > binary threshold. Binary tylko HARD rejects (safety, collision, post-shift).

**#11a (LESSON-QA-11) — Concrete mapping wymaga Adrian verify.** CC zmapował cid=522 → "Grzegorz Rogowski" błędnie (Szymon Sadowski). Reguła: cid→nazwa / restaurant→dedicated / shift→courier bindings = manual verify ZAWSZE.

**#11b (LESSON-QA-12) — Screenshoty paneli + mapy = game-changer Q&A.** Multi-stop decisions zawsze z wizualizacją trajectory (Google Maps). Pattern analysis na log only = ślepe dla spatial reasoning.

## V.3 Lekcje sprintowe V3.25-V3.27 (#13-#34, pliki #3-#8)

**#13** — Replay reconstruction has fundamental limits — audit ≠ production validation.

**#14** — Adrian's domain knowledge > statistical inference (e.g., "Albert od 12 to bug" identified w 30 sekund).

**#15** — Rule changes mid-session require 2-3 iterations + Bartek validation, NIE one-shot implementation.

**#16** — Defense-in-depth principle: Layer 1 (parser) + Layer 2 (anomaly) + Layer 3 (cross-validation) + Layer 4 (tests).

**#17** — Atomic git ops, granular tags as rollback points, never restart systemd without `py_compile` + import check.

**#18** — Per-step ACK gates — never proceed without explicit Adrian confirmation.

**#19-#22** — Big-Bang sprint lessons (25.04): TSP timing wymaga `get_traffic_multiplier()`, OR-Tools distance matrix musi mieć traffic, sequential evaluation 200ms × 10 candidates = 2000ms regression, parallel ThreadPoolExecutor potrzebny.

**#23** — Max 6h sesja. **WYCOFANA 01.05** explicit by Adrian (Opcja C 16h sprint dyscyplinowany lepszy niż 11 deliveries).

**#24** — Performance tests muszą simulować full per-proposal lifecycle (10 candidates evaluated), NIE isolated unit calls.

**#25-#28** — V3.27 lessons: latency Phase 1 software shortcut OK gdy Z3 fix planowany, OSRM persistence wymaga `--restart=unless-stopped`, login refresh TTL 20min, pre-warm login force=True na startup → 87% latency reduction.

**#29** — Cache TTL discovered = 20 min (NIE 22 jak hipoteza).

**#30** — V3.27.4 frozen window violation: bag_orders mają `czas_kuriera` frozen ale TSP planuje pickup poza window. Fix: dwell parity injection + slack=0 strict.

**#31** — Defense-in-depth multiplikatywne: KnownIdsWindow rolling 7-day uplift mechanism (orphan_size > 5 → critical).

**#32-#34** — V3.27.6 lessons: Path C robust detection + diagnostic assertion, restart-in-peak failed probe (precyzja vs availability tradeoff). **#34 (peak blackout) WYCOFANA 30.04** explicit by Adrian (Z1 supremacy).

## V.4 Lekcje incidentów + Z1/Z2/Z3 formalization (#42-#50, plik #9)

**#42** — Diagnose-not-rollback methodology. Gdy time-box <10 min + rollback dostępny, WPIERW diagnose. Walidowana 2× w incidents 30.04.

**#43** — Class-of-bug elimination > local fix. V3.28 Sprint 3 (3 phases) eliminated CSRF collision permanently zamiast quick patch.

**#44** — Diagnose-not-rollback discipline (rozszerzenie #42).

**#45** — Multi-service shared resource bez coordination layer = guaranteed bug. V3.28 Phase 1 (default OFF) = workaround. V3.29 (proper IPC daemon) = na lata.

**#46** — Spec MUST address multi-service coordination kiedy deployujemy "shared resource manager".

**#47** — Service-scoped configuration changes (env vars w systemd override) muszą być applied do WSZYSTKICH services importujących modyfikowany moduł. Pre-deploy checklist 'audit all consumers'. **Dowód:** Half-fix rano (override.conf TYLKO dispatch-shadow) kosztował 3.5h freeze incident #2.

**#48 (KRYTYCZNA)** — Recurring bug w short window (38 min + 64 min same root cause) = signal że fix był incomplete, NIE bad luck. Każdy 'recurrent' bug wymaga audit czy fix scope był complete.

**#49** — Operator UX peak operations ma hard limit cognitive load. Single-order approval flow nie skaluje powyżej ~10-15 propose/h niezależnie od button quality. ML training musi opierać się głównie na **PANEL_OVERRIDE (batch passive capture)**, TG_REASON jako secondary signal.

**#50** — Score function gaps wykrywane przez operator domain expert szybciej niż statistical analysis. Adrian zauważył #469834 z screenshota w 2 min, dataset analysis wymagałby godzin patternów.

## V.5 Lekcje ML pipeline (#51-#59, plik #9 sprint 01.05)

**#51** — Diagnose-not-rollback walidowane 3rd time (Sprint 3 success).

**#52** — R-04 v1 (volume-based) ABANDONED po 30 min — peak-quality philosophy v2 (peak_speed_med, on_time_rate, p90 latency) lepsza. **Rule:** schema design wymaga calibration loop z Adrian feedback do ground truth match.

**#53** — Phase 2 auto-apply z reversibility = OK (audit trail + 5 sec rollback). 5 std→std+ promotions auto-applied bezpiecznie.

**#54** — "Skopiuj nas + propozyje lepsze + ucz się" — w 3 zdaniach cała ML architektura: pure behavioral cloning + hard rules guardrails + continuous learning loop.

**#55** — CC pivot data source = Z3 win, multi-tenant ready. learning_log (~250-700 decisions) → available_pool.parquet (43K decisions, 5.5 mc) = 60× więcej training labels.

**#56** — Defense-in-depth ML inference 6 fallback paths.

**#57** — Empirical validation deployment-quality wymaga real production traffic. Smoke testy mock data weryfikują infrastructure (model load, inference path), ale NIE feature parity dla real decisions.

**#58** — Z2 supremacy "może 7 dni jakościowy". Quality > deadline ZAWSZE.

**#59 (Faza 5.1 features-jako-balast)** — 7 reconstruction features dropped (collinear z pool_size). Re-train identical metrics z cleaner architecture = strict improvement. **Rule:** każda feature engineering iteration musi mieć "drop redundant" pass przed training.

## V.6 Lekcje V3.28 Parser Resilience (#60-#66, plik #11)

**#60** — stdlib HTMLParser self.offset to column-in-line, NIE byte offset. Anti-pattern dla block boundary detection.

**#61** — Z3 dependency minimization > DOM parsing convenience. Pure regex stdlib + observability + property-based tests = future-proof. Anti-pattern: lxml/beautifulsoup dla simple ID extraction.

**#62** — Property-based parametric tests = strukturalna regression prevention. 8+ prefixes × N lengths = systemic verification. Anti-pattern: single hardcoded sample test.

**#63** — "ack alone" w 3+ outcome decisions = wymaga pushback (rozszerzenie #25 cognitive desync). Drugi Claude proaktywnie pyta "kiedy widziałeś X" przed eskalacją.

**#64** — **WYCOFANA 02.05 16:30** przez Adrian directive "naprawiamy proper dziś" (defer-after-architecture-sprint nie zawsze właściwa — context matters, Adrian fatigue + business pressure).

**#65** — Monitor thresholds wymagają peak/off-peak adaptive design. Sztywne thresholds = false positive na natural plateau LUB missed detection. ENV-tunable thresholds + adaptive defaults per time-window.

**#66** — Multi-signal anomaly detection > single-signal. Single metric (count_stuck) generates false positives na correlated patterns. Multi-signal (count + assigned_variance + delivered_count + new_count) eliminuje false positives bez kompromisu detection. Każdy nowy monitor check = ≥2 niezależne sygnały dla alert fire.

## V.7 Lekcje Z3-FOUNDATION-DAY + post-pivot (#67-#70, plik #13)

**#67** — Pre-flight diagnostic MUST include "primary output produced RIGHT NOW" check. Implementation: Fix 5 + Fix 5b + Fix 5c (truthful multi-signal + auto Telegram alert).

**#68** — Silent dead code detection: integration tests obowiązkowe dla schedulers. Implementation: Fix 8 + 5 nowych integration tests (czasowka_scheduler).

**#69** — Git workflow discipline w multi-faza sprintach. Self-detected by CC (recovery 3 min vs 5-10 budgeted).

**#70 (FAZA Z, candidate)** — Multi-signal verify nawet gdy CC ma HIGH confidence. Z.B verdict STRUCTURAL okazał się timing artifact po CSV finding. **Lesson:** peer-review z external data source nawet gdy internal signals zgodne.

## V.8 Operator/Adrian collaboration patterns (meta)

**Strategic decisions Adriana — pattern:**
1. Ground truth domain knowledge bije statistical inference
2. Peak quality > volume (R-04 v2.0 case)
3. "Skopiuj nas + propozyje lepsze + ucz się" — ML w 3 zdaniach
4. Z2 supremacy "może 7 dni jakościowy"
5. "Nie robimy łat, ma być na lata" (02.05 12:35)
6. Pushback przeciw konserwatywnym presetom CC kiedy trzeba (02.05 16:30 wycofanie #64)

**CC failure modes:**
- "ACK alone" assumption → wymaga pushback
- Cognitive drift po 4-6h
- Concrete mappings (cid→nazwa) wymaga Adrian verify
- Domyślna ostrożność czasem = nadmierna konserwatywność (Adrian samopoczuwa lepiej niż CC re: energii i biznesowych priorytetów)


---

# CZĘŚĆ VI — ROADMAP

## VI.1 Tydzień 1 (04-10.05.2026) — KRYTYCZNY

### TRACK A — Faza 7-AUTO-PROXIMITY (priorytet główny, 60% effort)
**Cel końca tygodnia:** 30% autonomy LIVE, stable 48h+ post-deploy.

| Dzień | Zadanie |
|---|---|
| Pn 04.05 (dziś) | F7.1 Scope+Design (CC autonomous) → F7.2 Implementation → ACK Gate F7.A → F7.3 Deploy 30% autonomy → F7.4 Live observation dinner peak |
| Wt-Czw 05-07.05 | Daily 30 min monitoring agreement_rate + override_count, edge cases capture |
| Pt 08.05 | 24h+ stability check, decyzja Tydzień 2 scale 30% → 70% |

**High-confidence threshold (TBD design):**
- pool>=2
- score margin >X (do empirycznie ustalenia)
- Gold/Std+ tier
- brak edge cases (czasówka, mass fail, low pool)

### TRACK B — Czasówki investigation + fix (priorytet wysoki, 20% effort)
**Cel:** czasówki działają empirycznie (5-15 propozycji/dzień, obecnie 1).

- Pn 04.05 — CC investigation czemu Fix 8 deployed nie generuje empirycznych propozycji (czasowka_scheduler tick-by-tick + replay konkretnych czasówek z 03.05)
- Wt-Śr 05-06.05 — Fix bug + deploy + smoke test

### TRACK C — Telegram UX redesign (priorytet średni, 15% effort)
**Cel:** 3 typy messages mobile-friendly (AUTO/ACK/ALERT), deployed.

- Wt-Śr 05-06.05 — audit + redesign mockup + implementation + deploy

### TRACK D — Notification system V1 (priorytet niski, 5% effort)
**Cel:** cron 1h przed shift start/end → Bartek Telegram notif.

- Czw-Pt 07-08.05 — implementation (~2-3h CC) + 1 day verify

## VI.2 Tydzień 2 (11-17.05.2026)

**Tech:**
- Faza 7-AUTO-PROXIMITY scale 30% → 70%
- Notification system V2 (kurier confirms online)
- LGBM_SHADOW timing investigation (decyzja Faza 5.2 GO/CANCEL Tydzień 3)
- Bolt Food integration sprint (technical pierwszy step)

**Bartek Recovery Campaign:**
- Mama Thai Bistro meeting (już planowane)
- Pani Pierożek, Goodboy, Pizza Dealer retention calls
- Picobello, Szklanki Talerze comeback discussions

**Adrian:**
- MVP design phase START (stack research, scope spec)
- Bartek conversation: comp model formal draft
- Server access verify

## VI.3 Tydzień 3 (18-24.05.2026)

**Tech:**
- Faza 7-AUTO-PROXIMITY 100% autonomy LIVE
- LGBM Faza 5.2 retrain decision (GO lub CANCEL)
- Bolt Food TEST traffic

**Bartek:**
- Akwizycja 2-4 nowych restauracji (sales mode)
- CSV growing stories interview (Ogniomistrz +167%, Retrospekcja +25%)

**Adrian:**
- MVP backend prototyp (auth + order placement + Ziomek dispatch integration)

## VI.4 Tydzień 4 (25-31.05.2026)

**Tech:**
- Ziomek autonomous 90%+ decisions
- Bolt Food LIVE traffic ramp

**Bartek formal promotion:**
- Manager Gastro role aktywne
- Comp model 0.50 PLN/order delta active
- Recovery campaign delivered (cel: +75-100 orderów/dzień retention)

**Adrian:**
- MVP UI + first restaurant test
- Decyzja kumpel co-founder (latest Tydzień 4)

## VI.5 Q3 2026 (czerwiec-sierpień)

**Volume target:** 400-450 orderów/dzień (recovery from 300 baseline).

**Drivers:**
- Bartek recovery campaign (+75-100)
- Bolt Food integration (+30-50)
- Nowe restauracje (+20-30)
- MVP własna aplikacja LIVE (5-10 restauracji)

**Marketing:**
- nadajesz.pl Białystok-only
- Dinner-focused (per CSV finding)
- Brand building krok 1

**Strategic:**
- Pierwsza rozmowa o franczyzie w drugim mieście (research mode)
- Decyzja kandydaci miasta: Lublin / Olsztyn / Toruń / Częstochowa

## VI.6 Q4 2026 (wrzesień-listopad)

**Volume target:** 600+ orderów/dzień peak (recovery YoY -50% gap closed).

**Drivers:**
- October peak (studenci wracają, najlepszy miesiąc 2025)
- MVP scale do 15-20 restauracji
- Marketing campaign nadajesz.pl

**Strategic:**
- Franczyza #1 w drugim mieście (start operations late Q4)
- Q1 2027 plan: 2-3 franczyzy active

## VI.7 2027+ Strategic horizon

- Multi-city franczyza scale (3-5 franczyz w Polsce)
- Volume per city baseline 500+/dzień
- SaaS multi-tenant pitch ready
- Rutcom API integration (= "API" goal, pełna autonomia) — decision Q3 2026

## VI.8 Ryzyka + mitigation

| Ryzyko | Mitigation |
|---|---|
| **R1:** Faza 7-AUTO-PROXIMITY quality drop | Liniowy progression z 24-48h obs każdy step, rollback ENV flag w 5s |
| **R2:** Czasówki investigation odkryje deeper bug niż Fix 8 | Tydzień 1 buffer dla deeper fix, jeśli >2 dni effort = STOP, decyzja |
| **R3:** Volume recovery nie nastąpi (600+ Q4 unattainable) | 4 niezależne lewary growth (Bartek campaign + Bolt + nowe + MVP), nawet 2/4 wystarczą do 400-450 |
| **R4:** Macro pogłębienie (recession Polska) | Niskie OpEx, performance-based comp, multi-channel revenue |
| **R5:** Bartek odchodzi | Tydzień 1 conversation o new role + comp = retention play |

## VI.9 Key metrics daily tracking

1. **Volume:** orderów dziennie total (cel: 300 → 400 Q3 → 600 Q4)
2. **Operator dependency:** % decisions wymagających human approval (cel: 100% → 30% Tydzień 2 → <10% Tydzień 4)
3. **Bartek hours na dispatch:** estimated (cel: ~8h obecnie → <2h Tydzień 4)
4. **Restaurant churn rate:** # NEW vs # GONE per miesiąc (cel: NEW > GONE = netto +)

## VI.10 Definicja sukcesu

**Tydzień 4 (~30.05):**
- Ziomek autonomous 90%+ decisions
- Bartek = Manager Gastro z aktywnym comp model
- Bolt Food generating traffic
- Recovery campaign +75-100 orderów/dzień

**Q3 (do końca sierpnia):**
- 400-450 orderów/dzień stable
- MVP własna aplikacja LIVE 5-10 restauracji
- Decyzja franczyza city #2

**Q4 (do końca listopada):**
- 600+ orderów/dzień peak (recovery YoY)
- Franczyza #1 active w drugim mieście
- MVP 15-20 restauracji

**2027+:**
- Multi-city scale (3-5 franczyz)
- Volume per city baseline 500+/dzień


---

# CZĘŚĆ VII — SPRINT HISTORY ARCHIVE (1-2 paragrafy per sprint)

Wszystkie sprinty od fundamentów (V3.19h, 21.04) do post-pivot (Z3-FOUNDATION-DAY, 03.05). Each sprint = 1-2 paragraph summary z kluczowymi faktami i lessons. Pełne logs w pliki #1-#13 (zachowane jako reference historyczny).

## VII.1 V3.19h era (do 21.04.2026) — Plik #1

Fundamenty Ziomka. 3 bugi V3.19h zidentyfikowane: (1) BUG-1 drop-zone vs same-restaurant bundling — Adrian's hipoteza obalona, faktycznie obaj robią SR ale Bartek z drop-clustering, fix mnożnik × bonus_l1; (2) BUG-2 wave anticipation — interleave gold 33% vs std 20.5%, fix wave_continuation_bonus; (3) BUG-3 directional NOT CONFIRMED, defer; (4) BUG-4 tier×pora bag cap matrix — match 10/12 cells z Adriana ground truth. **Filozofia FILOZ-1..5 sformalizowana** (Bartek+Adrian ground truth). 28 osiedli + 4 outside-city + 74 par adjacency manually approved przez Adrian. Tier assignment na 40k dataset: 4 Gold (Bartek 123, Mateusz O 413, Krystian 61, Gabriel 179), 1 Std+ (Adrian R 400), 22 Std, 3 Slow. Lekcje #1-#9 fundamenty.

## VII.2 V3.24 deploy (22.04 wieczór) — Plik #2

**V3.24-A SCHEDULE-INTEGRATION** — pierwszy realny schedule-aware gating, zastępuje V3.23 Albert Dec blacklist patch. Czyta gid 533254920, sprawdza shift_start/shift_end + tolerancje. Cold start zone <30min od start. **V3.24-B CZASOWKA-EMIT-SCHEDULER** — standalone scheduler dla czasówek (≥60min prep), progresywna selektywność 60/50/40min. Early morning gate 9:10. Telegram on-air @NadajeszBot. 0 crashes 24h. **Q&A 22.04** wieczór z Adrianem ekstraktował 21 reguł (R-01..R-21) + 5-step mental model dispatchera (FILTER-FILTER-FILTER-SCORING-DECISION). Tier changes: Krystian permanent OFF, Kuba OL bump Std→Std+, Szymon Sadowski cid=522 (NIE Grzegorz Rogowski). Lekcje #10-#12.

## VII.3 V3.25 + V3.26 (23-24.04) — Plik #3

**V3.25 night sprint (23.04 21:00 → 24.04 02:30, ~5.5h):** 4 CRITICAL ukończone — R-01 SCHEDULE-HARDENING (cid_unknown HARD REJECT, dropoff post-shift HARD), R-02 COURIER-SYNC + DISTRICTS, R-03 /stop /wraca core (TTL do 3:00 Warsaw), R-04 NEW-COURIER-CAP gradient. Hotfix PIN 9279 phantom. **V3.26 day sprint (24.04):** 7 HIGH features. **3 CRITICAL bugi odkryte (24.04 wieczór):** BUG-1 parser, BUG-2 parser, BUG-3 OSRM no traffic (shadow). **Adrian decyzje 24.04:** R-08 PICKUP-EXTENSION-NEGOTIATION ANULOWANE, R-12 restaurant-holding ANULOWANE, R-04 NEW-COURIER hardcoded 30-days graduation rejected — wymaga schema. Lekcje #13-#18. Override rate baseline pre-sprint 81%, target <60% post-V3.25.

## VII.4 Big-Bang sprint 25.04 (rano-popołudnie) — Plik #4

**Adrian's strategic principle formalized:** "Przy decyzjach architektonicznych ZAWSZE wybieram rozwiązanie najlepsze jakościowo i pod skalowanie na duży system w przyszłości. Nigdy pragmatic shortcuts." **OSRM TRAFFIC MULTIPLIERS** flip 08:12 UTC po 767 samples validation 6/9 buckets ±15%. **R-09 NameError fix** (osrm_client.haversine — DEAD od 23.04, ~960 errors/dobę). **C1 Solo Fallback fix** (DEAD od V3.25). **Venv migration LIVE** (`/root/.openclaw/venvs/dispatch/`). **OR-Tools TSP + same-restaurant grouper FLIP** → **3 regressions w peak:** Bug X (TSP timing underestimated ~60% — OR-Tools distance matrix bez `get_traffic_multiplier()`), Bug Y (zigzag routes), latency 2000ms/proposal vs 100-150ms baseline (sequential 200ms × 10 candidates, NIE parallel). **ROLLBACK OR-Tools/Grouping 16:30** — flagi rolled-back, kod commit'd. Lekcje #19-#24 (incl. #23 max 6h sesja, później wycofana 01.05; #24 performance tests muszą simulować full per-proposal lifecycle).

## VII.5 V3.27 wieczór (25.04) — Plik #5

**Diagnose-driven sprint:** fix 3 OPEN issues z Big-Bang rollback + re-flip OR_TOOLS + GROUPING. **Adrian explicit pre-sprint:** pełna diagnoza wszystkich bugów (NIE MVP reduction), latency Opcja A parallel ThreadPoolExecutor (NIE shortcut z time_limit), 1 flaga bundled. **4 fixy LIVE:** Bug A anchor, Bug B event_bus, Bug C po drodze strict, Bug D anchor. **Phase 1 latency software shortcut** (skip OR-Tools dla bag<2). **Hetzner upgrade pending** (CPX22→CPX32 niedziela rano). Lekcje #25-#28.

## VII.6 V3.27.1 + V3.27.2 (26.04) — Plik #6

**Hetzner CPX22→CPX32 upgrade EXECUTED** (4 vCPU/8GB AMD EPYC Genoa, +€6/mies). **OSRM Docker persistence fix** (`docker update --restart=unless-stopped`). **Sesja 1 V3.27.1 BUG-1 czas_kuriera emit (5h)**. **Sesja 2 atomic flip ROLLBACK** — Bug 1 helper schema. **Sesja 3 fix Bug 1**. **V3.27.2 DWELL bump + atomic re-flip + ROOT CAUSE login refresh** (cache TTL = 20 min discovered, NIE 22 jak hipoteza). Lekcje #28-#29.

## VII.7 V3.27.3-V3.27.5 jednodniowy (27.04) — Plik #7

**3 sprinty w 1 dniu:** V3.27.3 + V3.27.4 + V3.27.5. **Faza 0 sesja 4 V3.27.1 pre-warm login** — `panel_client.login(force=True)` na startup, **87% latency reduction** (6748ms cold → 841ms warm). **3 critical bugi naprawione (Adrian eyeball + screenshots):**
- **#468945 Andrei** — kurier z bagiem dostawał propozycję powodującą 14 min wait pod nową restauracją. TASK B fix LIVE.
- **#469014 Pani Pierożek 17:09** — TSP planował pickup 17:09 mimo czas_kuriera 16:55 (frozen). V3.27.4 fix LIVE.
- **#469099 Picked-up bug** — plan zawierał pickupy dla orderów już picked up. V3.27.5 Path A+B fix LIVE.

5 changes LIVE, 5 raportów (~3500 linii markdown), 13+ commits, 11+ tagów git, ~7h CC autonomic. Lekcje #28-#31.

## VII.8 V3.27.6 (28.04) — Plik #8

**Sprint Path C + diagnostic assertion + probe.** **#469150 Tomasz Ch 12:29** — V3.27.4 frozen window VIOLATION dla bag orderu (Rukola Sienkiewicza pickup planowany 12:55 = 26.7 min poza window [12:24, 12:34]). Bliźniaczy case z 27.04 (#469099 Szymon P, +65 min poza window). **Empirical scope:** 2 violations / 22 applicable propose / 131 total = 9.1% applicable, 1.5% all. Strategy w obu: ortools (NIE greedy_fallback). **Failed restart-in-peak probe** — precyzja vs availability tradeoff. **Lekcja #34 peak blackout** sformalizowana (później wycofana 30.04). Lekcje #32-#34.

## VII.9 V3.27.7 + TECH_DEBT (29.04) — Plik #9 (research)

**Środa wieczorem ~21:00-23:00.** Overnight analysis (H4 STRONGLY CONFIRMED, 455 violations 12h). **V3.27.7 research — 3 opcje analyzed** (A slack=0 frozen, B dwell parity injection, C re-solve loop). Default rec: **Opcja B** (latency neutral, 1.5-2h effort, fixes broader scoring accuracy). Skip C (treats symptom, +200ms p95 cofa TECH_DEBT #20 zysk). **TECH_DEBT #20 panel_bg_refresh deploy** (mechanical GREEN-conditional, daemon thread interval=900s, 2 call sites + watchdog, env override `ENABLE_PANEL_BG_REFRESH=False`). Empirical latency walidacja defer 30.04 lunch peak. Tag stable `v3277-tech-debt-20-deploy-stable-2026-04-29`.

## VII.10 V3.27.7 INCIDENTS DAY (30.04) — Plik #9' (incidents)

**Dzień intensywny:** 4 LIVE deploys, 2 incidents resolved (oba diagnose-not-rollback), V3.28 Sprint 3 LIVE z 3 phases, **diagnoza krytycznego bundle bug #469834**, formalizacja **3 zasad kardynalnych Z1+Z2+Z3**.

**Najważniejsze osiągnięcia:**
1. V3.28 Sprint 3 (3 phases): class-of-bug CSRF collision permanently eliminated
2. Sprint 1 logging fixes: TOP_N=16 + pool counts → pełna observability decision_record
3. **V3.19i 2-part Telegram UX: TRASA + 8 reason buttons LIVE w peak**
4. Diagnoza #469834: pierwszy konkretny Ziomek quality bug z pełnymi Sprint 1 danymi
5. Lekcje #42-#50 (8 nowych) z dowodami empirycznymi

**Kluczowe odkrycia:**
- Propose flow uptime 13.2% w lunch peak (anomalia, baseline 80-95%)
- TG_REASON adoption 1 entry / 731 propose (Bet C confirmed częściowo)
- Bundle scoring gap dla cross-restaurant pickup-bundle (FILOZ-3 violation)
- Half-fix incidentu #1 kosztował 3.5h freeze incident #2 (Lekcja #47/#48)

**Adrian explicit override 17:10:** Z1 supremacy → Lekcja #34 (peak blackout) trwale wycofana.

## VII.11 ML Pipeline Sprint (01.05.2026) — Plik #9'' (FINAL)

**Historic milestone: 16 deliveries w 10h sprintu z zero incidents.** Architektoniczne breakthroughs:
1. **R-04 v2.0 peak-quality philosophy** (peak_speed_med, on_time_rate, p90 latency) — 5 std→std+ promotions auto-applied (Paweł SC, Michał K, Adrian Cit, Kacper Sa, Dariusz M)
2. **BC + Hard Rules + Continuous Learning architecture** sformalizowana (Adrian directive "skopiuj nas + propozyje lepsze + ucz się")
3. **ML pipeline Faza 3-6 LIVE:** Faza 3 Pairwise Dataset (399,361 pairs / 5.5 mc), Faza 4 Feature Engineering (78 cols × 6 groups, kd-tree 8.6K nodes, 92% OSRM coverage), Faza 5 LGBM Ranker (NDCG@5=0.852, pa 88.45%, 5.7s training), Faza 6 LGBM Shadow LIVE (31ms/decision, 6 fallback paths), **Faza 5.1 retrain v1.1** (7 features dropped collinear z pool_size — identical metrics, Lekcja #59).
4. **T1 Logger fix** (dispatch_pipeline INFO routing) + **T3 Schedule hot-refresh** (TTL 10min, eliminuje 8h freeze daily)
5. **Faza 7 design spec** ready (3.5-4h effort)

**Lekcje #51-#59. Lekcja #23 (max 6h sesja) RETRACTED explicit by Adrian.** Sprint velocity peak — CC 3-10× szybciej niż estymaty (Faza 5 5.7s training, Faza 5.1 retrain 6 min, T1+T3 30 min vs 1.5h plan). Tag `v319i-2-part-stable-2026-05-01-23-30`.

## VII.12 V3.28 PARSER-RESILIENCE (02.05.2026) — Plik #11

**Sobota 01.05 ~20:06 ID counter rolnął 469999→470000.** Hardcoded regex `r"id:\s*(46\d{4})"` w panel_client.py:223 (timebomb od V3.19f / Initial commit, 4-5 lat) nagle przestał wyłapywać orderów. Ziomek silent 16h+. **Adrian directive 12:35 "Nie robimy łat, ma być na lata"** → odrzucił quick fix (5 min regex change), pivot na proper Z3 architectural sprint.

**4-Layer defense-in-depth deployed:** Layer 1 universal regex `\d{5,7}`, Layer 2 ParserHealthMonitor motion-aware, Layer 3 KnownIdsWindow 7-day, Layer 4 property-based tests + HTTP health endpoint :8888. **57/57 tests pass.** Phase A → B v1 → SOFT rollback (false positive Layer 2) → ETAP 1 motion-aware proper fix → Phase B v2. **7 propozycji wysłanych dinner peak, 100% pipeline conversion, ZERO crash.**

**4 post-sprint tickets w 66 min (sub-budget z 3.5h target):**
- TICKET 1: Motion threshold tuning >0 → >=4 (env tunable)
- TICKET 2: LGBM_SHADOW log visibility + standalone validation gate pipeline
- TICKET 3: Faza 7 deploy checklist (10 pre-conditions + 3 UAT scenarios)
- TICKET 4: Plik wiedzy #10 polish

**Micro-ticket: Git config fix** (going-forward Adrian Czapla <ac@nadajesz.pl>, 293 historic commits zachowane). Lekcje #60-#66 (Lekcja #64 wycofana 02.05 16:30 przez Adrian "naprawiamy proper dziś"). Tag `v328-post-sprint-tickets-2026-05-02`.

## VII.13 Z3-FOUNDATION-DAY + Strategic Pivot (03.05.2026) — Plik #13

**02.05 23:03 last propose Ziomka pre-incident (#470205). 03.05 09:30 Adrian discover incident (12h+ outage).** Sprint Z3-FOUNDATION-DAY 11h (11:00-22:00) — **9 fixów LIVE** (resilience + observability + czasówki recovery):
- Fix 5 + Fix 5b + Fix 5c (truthful multi-signal pre-flight + auto Telegram alert)
- Fix 8 czasowka_scheduler integration tests
- Inne Fix 1-7

**FAZA Z extended (19:30-21:30):** Z.B verdict STRUCTURAL — pre-CSV.

**22:30 Bartek strategic input:** 5 strategic moves (sales, outsource conversion, Bolt Food, YoY, MVP) + Manager Gastro role + 0.50 PLN/order delta comp model.

**23:00-23:15 CSV YoY analysis (4 zmiany strategiczne):**
- -52% volume YoY confirmed
- Lunch -64% / Dinner -35% — MVP MUST be dinner-focused
- 18 GONE + 10 SEVERE DROP restauracji = main driver (-300 orderów attributable)
- Bundle 86%→66% = timing artifact, NIE structural single-order

**~23:30 Strategic Pivot Document v2:**
- ❌ Faza 7 LGBM PRIMARY CANCEL (kosmetyczna, 100% fallback)
- ✅ Faza 7-AUTO-PROXIMITY GO (rule-based, 30%/70%/100% progression)
- 🟡 Faza 5.2 retrain LGBM v1.2 HOLD (timing artifact investigation Tydzień 2-3)
- ❌ Faza 8 ALT Explorer / Faza 10 A/B Test CANCEL
- ❌ Restimo / Wolt Drive POTENTIALLY DEPRECATED (decision Q3 2026)

Lekcje #67-#70 (incl. #70 candidate Multi-signal verify nawet gdy CC HIGH confidence).

**04.05.2026 9:00 nowa sesja, Faza 7-AUTO-PROXIMITY GO** — pierwszy dzień post-pivot.


---

# CZĘŚĆ VIII — ANULOWANE / WYCOFANE (decisions log)

Pełen audit trail decyzji architektonicznych odrzuconych lub wycofanych. Każda pozycja: co, kiedy, dlaczego, kto.

## VIII.1 Reguły anulowane

| Pozycja | Kiedy | Dlaczego | Kto |
|---|---|---|---|
| **R-08 PICKUP-EXTENSION-NEGOTIATION** | 24.04 | Adrian explicit decyzja, RESTAURANT_EXTENSION_TOLERANCE table niepotrzebny | Adrian |
| **R-12 restaurant-holding-detection** | 24.04 | Adrian explicit decyzja, hold detection inną drogą | Adrian |
| **R-04 v1 (volume-based)** | 01.05 | Schema demote'owała Mateusz O (intentional part-time) — peak-quality v2.0 lepsza | Adrian |

## VIII.2 ML/architektura anulowane

| Pozycja | Kiedy | Dlaczego | Kto |
|---|---|---|---|
| **Faza 7 LGBM PRIMARY** | 03.05 | Kosmetyczna zmiana (LGBM zwraca fallback do V3.27 baseline w 100% przypadków przy pool_size=0). Deploy = ZERO functional change. | Adrian (post-CSV) |
| **Faza 8 ALT Explorer** | 03.05 | Zbędne dla rule-based autonomy | Adrian |
| **Faza 10 A/B Test 30% Peak** | 03.05 | Zastąpione liniowym progression Faza 7-AUTO-PROXIMITY | Adrian |
| **Restimo / Wolt Drive integration** | 03.05 (potencjalnie) | Decision Q3 2026 — jeśli MVP własna aplikacja działa, nie potrzebujemy aggregatorów | Adrian |
| **Faza 5.2 retrain LGBM v1.2** | 03.05 (HOLD) | CSV pokazał 66% bundle ratio (NIE 0% jak Z.B). Hipoteza timing artifact. Investigation Tydzień 2-3 | Adrian |

## VIII.3 Lekcje wycofane

| Lekcja | Kiedy wycofana | Dlaczego | Kto |
|---|---|---|---|
| **Lekcja #23** (max 6h sesja) | 01.05 | Opcja C 16h sprint dyscyplinowany lepszy niż 11 deliveries. Sesje 12-15h dopuszczalne na good-form days. Hard quality rules (Z2/Z3) zostają. | Adrian |
| **Lekcja #34** (peak blackout) | 30.04 17:10 | Z1 supremacy → autonomia primary goal niezależnie od dnia/pory | Adrian |
| **Lekcja #64** (defer-after-architecture-sprint) | 02.05 16:30 | Adrian directive "naprawiamy proper dziś" — context matters (fatigue + biznesowy priorytet) | Adrian |

## VIII.4 Quick fixes / patches odrzucone

| Pozycja | Kiedy | Dlaczego | Kto |
|---|---|---|---|
| **Quick fix regex change** (5 min, V3.28) | 02.05 12:35 | "Ma być na lata, system jakościowy" — Z3 architectural fix preferred (4-layer defense-in-depth) | Adrian |
| **Hardcoded 30-days graduation R-04** | 24.04 | Wymaga schema (peak-quality), NIE hardcoded threshold | Adrian |
| **Pragmatic shortcuts** (--break-system-packages, hardcoded values dla speed) | 25.04 (formalizacja Z3) | Strategic principle: zawsze rozwiązanie najlepsze pod skalowanie | Adrian |

## VIII.5 Action Items "anulowane jako redundantne"

| Pozycja | Kiedy | Dlaczego |
|---|---|---|
| **V3.28-TELEGRAM-ALERT-PATH-VERIFY** | 02.05 14:42 | Already verified end-to-end (18 alerty na "Grupa ziomka" w Phase A) |

---

# CZĘŚĆ IX — METADATA + POINTERS

## IX.1 Współpraca z CC — strict per-step workflow

1. Draft → ACK → `cp .bak` → `str_replace` → `py_compile` → import check → test → commit → restart → verify → **stop for ACK**
2. Granular git tags as rollback points at every step
3. Never restart systemd without `py_compile` + import check
4. **No `jq`**; `sed` for reading only; atomic writes via temp/fsync/rename
5. **No heredocs with quotation marks** (safety prompt trigger)
6. Per-step ACK gates — never proceed without explicit confirmation
7. **Telegram NIGDY restart bez explicit ACK w czacie**
8. CC autonomic mode + eskalacja: write poza scope, contradiction, fundamental FAIL po 2 próbach, >30min bez progresu

## IX.2 Sprint sessions

- Checkpoints **co 2h** (obligatoryjne self-check)
- Sesje 12-15h dopuszczalne na good-form days (Lekcja #23 wycofana 01.05)
- Hard quality rules (Z2/Z3) regardless of session length
- Accumulated errors causing frustration → automatic stop signal, roll back, continue in new chat

## IX.3 Komunikacja

- **Po polsku**, direct and concise
- **2-5 najmocniejszych opcji z oceną** (NIE 20 luźnych pomysłów)
- **"Pytaj nie zgaduj"** przy unknowns
- Explicit about weak points or missing data
- Adrian decyzje strategiczne, CC executes z ACK Gates

## IX.4 Feature flags + learning

- All major features gated by flags w `flags.json`, hot-reloaded every tick
- Enables safe rollback bez code changes
- **Learning signals → `learning_log.jsonl`:** PANEL_OVERRIDE (PRIMARY), ASSIGN_DIRECT, REPLY_OVERRIDE, OPERATOR_COMMENT, TG_REASON
- **`rule_weights.json`** = adaptive penalties R1/R5/R8 dla auto-calibration by `learning_analyzer` po 50+ TAK/NIE signals

## IX.5 Critical paths cheat-sheet

```
SERVER: Hetzner CPX32, 178.104.104.138, Ubuntu 24.04, UTC

CODE:
/root/.openclaw/workspace/scripts/dispatch_v2/   — Ziomek
/root/.openclaw/workspace/scripts/ml_data_prep/  — ML pipeline
/root/.openclaw/venvs/dispatch/                   — Python 3.12.3 venv
/root/.openclaw/venvs/ml_data_prep/               — ML pipeline venv

STATE:
/root/.openclaw/workspace/dispatch_state/learning_log.jsonl
/root/.openclaw/workspace/dispatch_state/events.db
/root/.openclaw/workspace/dispatch_state/courier_tiers.json
/root/.openclaw/workspace/dispatch_state/kurier_piny.json
/root/.openclaw/workspace/dispatch_state/schedule_today.json
/root/.openclaw/workspace/dispatch_state/restaurant_violations.jsonl

LOGS:
/root/.openclaw/workspace/scripts/logs/watcher.log
/root/.openclaw/workspace/scripts/logs/dispatch.log
/root/.openclaw/workspace/scripts/logs/czasowka.log

ML MODELS:
/root/.openclaw/workspace/scripts/ml_data_prep/models/v1.1/

HEALTH:
http://localhost:8888/health/parser
```

## IX.6 Kluczowe ID i dane

| Co | Wartość |
|---|---|
| **Telegram grupa "Grupa ziomka"** | chat_id `-5149910559` |
| **Dispatch bot @NadajeszBot** | token `8571778075:AAE3lidXU9ijX2FNPpJRxd5DyfIu6FTwYp0`, chat ID `8765130486` |
| **Control bot @GastroBot** | token `8770101598:AAF-x2srerA7ES-fzlehd7SY4kn5gUUcvsQ` |
| **Schedule Sheet ID** | `1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8`, gid `533254920` |
| **APK URL** | `https://gps.nadajesz.pl/apk/courier.apk` |
| **Admin panel** | `https://gps.nadajesz.pl/panel` (admin/nadajesz2026) |
| **Panel orders** | `gastro.nadajesz.pl/admin2017/new/orders/edit-zamowienie` |
| **GitHub** | `github.com/czaplaadrian88-code/ziomek-dispatch-` |
| **Email** | `ac@nadajesz.pl` |
| **GCP project Maps API** | `gen-lang-client-0704473813` |

## IX.7 Quick reference — co gdzie szukać

| Pytanie | Sekcja |
|---|---|
| "Co teraz robimy?" | I.1, I.4, VI.1 |
| "Kto jest top kurier?" | II.5 |
| "Kontakt do panel API?" | III.2 |
| "Jakie są hard rules?" | II.2 |
| "Czemu nie robimy LGBM PRIMARY?" | I.3, VIII.2 |
| "Lekcja #X to co?" | V (numerowane) |
| "Co było w sprincie X.04?" | VII (chronologicznie) |
| "Co wycofane?" | VIII |
| "Paths do plików?" | III.1, IX.5 |
| "Telegram bot setup?" | III.4 |
| "Czasówki workflow?" | III.7 |
| "FILOZ-1..5?" | II.1 |
| "21 reguł?" | II.3 |
| "BUG-1..4?" | II.4 |
| "Districts adjacency?" | II.6 |

## IX.8 Pliki źródłowe (zachowane jako historical reference)

| Plik | Data | Zakres |
|---|---|---|
| `Plik_wiedzy` | 2026-04-21 | Fundamenty: BUG-1..4, FILOZ-1..5, 28 osiedli, lekcje #1-#9 |
| `Plik_wiedzy_2_Q_A_V3_24_V3_25_2026-04-23.md` | 2026-04-23 | V3.24 deploy, Q&A 22.04, 21 reguł, lekcje #10-#12 |
| `3_plik_wiedzy_3_sprint_history_23-24_04.md` | 2026-04-24 | V3.25 + V3.26 sprint, R-08/R-12 anulowane, lekcje #13-#18 |
| `Plik_wiedzy_4_Big-Bang_25_04` | 2026-04-25 | Big-Bang sprint, 7 fixów + ROLLBACK OR-Tools, lekcje #19-#24 |
| `Plik_wiedzy_5` | 2026-04-25 wieczór | V3.27 + 4 fixy + Hetzner upgrade pending, lekcje #25-#28 |
| `Plik_wiedzy_6` | 2026-04-26 | V3.27.1 + V3.27.2 + login refresh, lekcje #28-#29 |
| `Plik_wiedzy_7` | 2026-04-27 | V3.27.3-5 jednodniowy + Hetzner CPX32 EXECUTED, lekcje #28-#31 |
| `Plik_wiedzy_8` | 2026-04-28 | V3.27.6 + Path C + lekcje #32-#34 |
| `Plik_wiedzy_9_TECH_DEBT` | 2026-04-29 | TECH_DEBT #20 + V3.27.7 research |
| `PLIK_WIEDZY_9_INCIDENTS` | 2026-04-30 | V3.27.7 INCIDENTS + Sprint 1-2-3 + V3.19i + Z1+Z2+Z3 formalization |
| `Plik_wiedzy_9_FINAL` | 2026-05-01 | ML Pipeline sprint (16 deliveries) + R-04 v2.0 + Faza 5.1 + lekcje #51-#59 |
| `Plik_wiedzy_11` | 2026-05-02 | V3.28 PARSER-RESILIENCE + 4 post-sprint tickets + lekcje #60-#66 |
| `Plik_wiedzy_13_Strategic_Pivot` | 2026-05-04 (sprint 03.05) | Z3-FOUNDATION-DAY + CSV YoY + Strategic Pivot + lekcje #67-#70 |
| `Plan_pracy_najbliższy_czas_Tydzień_1-4` | 2026-05-04 | Roadmap Tydzień 1-4 post-pivot + Q3-Q4 + 2027+ |

## IX.9 Status sumaryczny (stan 04.05.2026 9:00)

- **Last sprint:** Z3-FOUNDATION-DAY 03.05 (11h, 9 fixów) + FAZA Z extended (1h) + Strategic Pivot
- **Last commit:** 9242209 (Fix 5c merge)
- **Last tag:** v329-faza-z-extended-2026-05-03
- **Tests:** 95/95 V3.28 PASS
- **Status services:** wszystkie 5 LIVE, stable 24h+
- **Cardinal exceptions:** 3 panel-watcher restarts (oba ACK'd)
- **Następna sesja:** 04.05 9:00 Warsaw, Faza 7-AUTO-PROXIMITY scope sprint
- **Open questions:** czasówki investigation (Track B), high-confidence threshold formula (Track A), Telegram UX redesign (Track C)

---

**END OF MASTER KNOWLEDGE FILE**

**Wersja:** v1.0 — konsolidacja Pliki Wiedzy #1-#13 + Plan Pracy
**Data:** 04.05.2026
**Owner:** Adrian Czapla <ac@nadajesz.pl>
**Następna aktualizacja:** po sprintach Tydzień 1, dodać sekcję "Sprint Tydzień 1 summary" do VII

