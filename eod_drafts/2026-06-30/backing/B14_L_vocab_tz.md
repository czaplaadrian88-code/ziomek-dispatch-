# B14 — KLASA L (słownictwo niejednoznaczne / jednostki / TZ) — backing

**Agent:** B14-L-vocab-units-tz · **lane B** · **READ-ONLY** · **2026-06-30 ~14:1x UTC** · sesja tmux 2
**Zakres:** przeciążone nazwy (`tier`, `czasowka`/`elastyk`, `podjazdy`), niejawne jednostki (minuty-od-teraz vs HH:MM vs timestamp), TZ (Warsaw vs UTC, naive-vs-aware, today-only-bez-+1-przez-północ shift_start, checkpoint-TZ nawracający).
**Wszystkie `plik:linia` ze ŚWIEŻEGO grepu DZIŚ** (linie dryfują — re-grep przed użyciem jako pewnik).
**Dedup vs Faza A:** A2 już złapała L na `R8 PICKUP_SPAN_HARD_*` / `R-LATE-PICKUP HARD_GATE` / `R-RETURN VETO` (nazwa-HARD vs zachowanie-SOFT) — TO NIE powtarzam (należy do I/L precedencji, lane konfliktów). A4 złapała `checkpoint_tz` stale-jsonl. A6 grupa 7 = `eta_pickup` display≠decision (root R5). **Tu ROZSZERZAM: 4 nowe pod-roots klasy L nieobjęte A: (LT) overload `tier`, (LN) split konwencji naive-datetime, (LM) midnight-crossing shift_start, (LE) enum `elastic`/`elastyk` + jednostki pól.**

---

## TL;DR — 6 pod-rootów klasy L (most-severe first)

| # | Pod-root | Najgorsza instancja | Sev | Źródło/objaw | Patched? |
|---|---|---|---|---|---|
| **LN** | **Split konwencji naive-datetime: boundary=Warsaw / math=UTC** | feasibility_v2 + route_simulator zakładają naive→UTC w HARD-bramce; parser zakłada naive→Warsaw; self-doc plan_recheck:288 „=błąd +2h" | **P2** | źródło | część (checkpoint_tz) |
| **LN-x** | **fleet_state.py: DWA parsery, odwrotne naive (intra-file)** | `_iso`:100 naive→Warsaw vs `_parse_ts`:219 naive→UTC; pola „Warsaw naive" | **P2** | źródło | nie |
| **LM** | **shift_start bez +1-przez-północ (asymetria vs shift_end)** | `_shift_start_dt`:1264 + `_minutes_to_pre_shift`:1246 = zawsze DZIŚ; `_shift_end_dt` MA 24:00→+1 | **P2** | źródło | nie |
| **LT** | **`tier` = 4 znaczenia (klasa/eskalacja/solver-dim/GPS)** | `_esc_tier∈{2,3}` serializowany OBOK courier-class `tier`; „tier-3 cap=40"=eskalacja, `tier=='slow'`=klasa | **P2** | źródło | nie |
| **LE** | **`order_type` enum mieszany PL/EN: „elastic" vs „elastyk"** | prod=„elastic"; positive-matcher `==\"elastic\"`; fixtury+`czasowka_dispatchable_fleet` używają „elastyk" | **P3** | źródło | nie |
| **LU** | **Jednostki w prefiksie: `eta_pickup_min`(min) vs `_utc`(abs) vs `_hhmm`(display); `czas_odbioru`(int) vs `_timestamp`(dt)** | `eta_pickup_min` (minuty-od-teraz) ≠ `eta_pickup_utc` (absolut) — ten sam prefiks | **P3** | źródło | nie |

Plus: **WARSAW const = 8 NAZW** (A1/L kosmetyka, P3); **2 progi 60-min** (czasowka vs early-bird, redundancja, P3); **checkpoint_tz** PATCHED-instancja LN (dowód nawrotu).

---

## LN — SPLIT KONWENCJI NAIVE-DATETIME (Warsaw vs UTC) ★ headline

**Reguła faktyczna (panel API):** panel wysyła timestamp jako **naive Warsaw** string `"YYYY-MM-DD HH:MM:SS"` (np. `czas_odbioru_timestamp`, `picked_up_at`); `created_at` = UTC z `Z`.

**Dwie warstwy, DWIE przeciwne konwencje dla naive:**

### Camp B — naive = **WARSAW** (warstwa parse/boundary — POPRAWNA dla panelu)
| Plik:linia | Helper | naive→ |
|---|---|---|
| `common.py:467-468` | `parse_panel_timestamp` (`if dt.tzinfo is None: dt=dt.replace(tzinfo=WARSAW)`) | Warsaw |
| `panel_client.py:566` | `_parse_warsaw_naive` (`strptime(...).replace(tzinfo=WARSAW_TZ)`) | Warsaw |
| `panel_client.py:610-635` | `_czas_kuriera_to_datetime` (anchor Warsaw, closest-day) | Warsaw |
| `sla_tracker.py:167` | `dt.replace(tzinfo=ZoneInfo("Europe/Warsaw"))` | Warsaw |
| `state_machine.py:779` | `strptime(picked,...).replace(tzinfo=ZoneInfo("Europe/Warsaw"))` (picked_up_at) | Warsaw |
| `courier_api/courier_orders.py:118-129` | `_iso_to_hhmm` („naive='…' z panelu — już lokalny → bez konwersji") | Warsaw |

### Camp A — naive = **UTC** (warstwa math/HARD-bramki — defensywna, „poprawna tylko bo upstream znormalizował")
| Plik:linia | Kontekst | naive→ |
|---|---|---|
| `feasibility_v2.py:127-128` | helper aware-norm | UTC |
| `feasibility_v2.py:442-443` | `now` | UTC |
| `feasibility_v2.py:749` | **shift_start** (HARD pre-shift gate) `replace(tzinfo=timezone.utc) if tzinfo is None` | UTC |
| `feasibility_v2.py:770-771` | **shift_end** (HARD shift-end gate) | UTC |
| `route_simulator_v2.py` | ~20 sites: 70-71, 271-275, 565-566, 592-593, 616-617, 652-653, 680/685/689/692, 722-723, 1145-1146, 1182-1183, 1213-1214, **1249-1250, 1283-1284** (czas_kuriera_warsaw!), 1317-1318, 1453-1454 | UTC |
| `plan_recheck.py:166-167, 257-258, 297-298` | `_parse_dt` (ISO→aware UTC) | UTC |
| `state_machine.py:928-929, 1064` | norm | UTC |

**Self-documented hazard (DOWÓD że to znana mina):** `plan_recheck.py:284-288` docstring `_parse_dt`:
> „NIE używać dla naiwnych Warsaw timestampów (np. orders_state.picked_up_at "YYYY-MM-DD HH:MM:SS" bez offsetu — **interpretacja jako UTC = błąd +2h**)."

→ Istnieją DWA parsery tego samego pola `picked_up_at` z przeciwną kotwicą TZ: `state_machine.py:779` (naive→Warsaw, POPRAWNY) vs `plan_recheck._parse_dt` (naive→UTC, +2h). Poprawność = każdy caller MUSI wybrać właściwy. Klasa L + B-twin.

**Dlaczego „mostly works" (i dlaczego latentne, nie martwe):** warstwa parse (panel_client/common) konwertuje panel-naive-Warsaw → aware-UTC NA GRANICY. Do math-layer wszystko dochodzi aware-UTC → guard `if tzinfo is None: →UTC` = defensywny no-op. **Mina:** wartość Warsaw-naive która OMINIE granicę i dotrze do math-layer = czytana jako UTC = +2h W HARD-BRAMCE (feasibility shift gate / route_simulator R6 anchor).

**DOWÓD NAWROTU (PATCHED-instancja):** `tools/checkpoint_tz_shadow.py:6` — „checkpointy GPS są Warsaw-naive; **4 miejsca w courier_resolver parsowały je jako UTC** → predykcja pozycji" → fix `ENABLE_CHECKPOINT_TS_WARSAW_PARSE` (LIVE, VALIDATED). To jest dokładnie LN w courier_resolver, naprawione. A4 zauważyła że kolektor disabled + jsonl 27.06 STALE (mylące przy ślepym `ls`).

**dedup_hint:** `LN-naive-tz-convention-split` (NOWY root, NIE w A6 R1-R5; pokrewny K1 „brak jednego źródła" ale specyficzny dla semantyki TZ). Faza F target: jeden typ/kontrakt „wszystkie czasy aware-UTC od granicy" + zakaz naive w math-layer (assert).

---

## LN-x — fleet_state.py: DWA PARSERY, ODWROTNE NAIVE (intra-file, cross-repo konsola)

`nadajesz_clone/panel/backend/app/integrations/ziomek/fleet_state.py`:
- `_iso(s)` **:97-102** → `dt if dt.tzinfo else dt.replace(tzinfo=WARSAW)` = naive→**Warsaw**
- `_parse_ts(val)` **:214-221** → `dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)` = naive→**UTC**

Oba w TYM SAMYM pliku. Pola dataclass `delivered_at` **:192** i `picked_up_at` **:195** opatrzone komentarzem „(Warsaw, naive)" → jeśli parsowane przez `_parse_ts` (naive→UTC) = +2h. Konsola to powierzchnia render DEC-ADJ (A5: `_build_route`/`_eta_chain` carried-first + committed-pickup = decyzyjna kolejność/czas), więc ±2h na HH:MM widzianym przez koordynatora.
**Status:** CONFIRMED strukturalnie (2 helpery, przeciwne konwencje); LIVE-impact = PLAUSIBLE (zależy który helper dotyka pól „Warsaw naive" — nie traceowałem każdego call-site, read-only). dedup_hint: `LN-naive-tz-convention-split` (cross-repo manifest).

---

## LM — shift_start BEZ +1-PRZEZ-PÓŁNOC (asymetria bliźniaka vs shift_end)

`courier_resolver.py`:
- `_shift_start_dt(entry)` **:1252-1266**: `now_w.replace(hour=int(h), minute=int(m), ...)` → **ZAWSZE dzisiejsza doba**, ZERO obsługi przełomu północy.
- `_minutes_to_pre_shift(...)` **:1240-1247**: ten sam wzorzec `now_w.replace(hour,minute)` → zawsze dziś.
- ⚠ KONTRAST: `_shift_end_dt(entry)` **:1269-1287** MA: `if end_str=="24:00": base.replace(0:0)+timedelta(days=1)` (**:1278-1280**) + komentarz „zmiana skończyła się wczoraj (now=01:00, end=23:00) → interpretujemy jako today (przeszłość)".

**Skutek:** zmiana z `start="22:00"` lub `"23:00"` odczytana przy `now=00:30` (tuż po północy) → `_shift_start_dt` zwraca DZIŚ 22:00/23:00 = ~22h W PRZYSZŁOŚCI zamiast WCZORAJ. Kurier nocny wygląda na `pre_shift` ~22h → błędny clamp/`PRE_SHIFT_TOO_EARLY` HARD-reject (`feasibility_v2:751`). Relewancja realna: grafik dopuszcza zmiany pt/sb **do 24:00** (GRF-02), więc późne zmiany istnieją. `_shift_end` to obsługuje, `_shift_start` NIE — bliźniaki rozjechane (klasa B + L).
**dedup_hint:** pokrewny A6 R4 (`one-earliest-pickup-floor`, grupa 6 shift_start) — ALE to OSOBNA fasetka: nie „brak floor", lecz „błędne date-anchoring HH:MM→datetime" (L/TZ). Tag: `LM-shift-start-midnight-anchor`.

---

## LT — `tier` = 4 ROZŁĄCZNE ZNACZENIA (overload tokenu) ★

Memory/CLAUDE.md explicit ostrzega „⚠ „tier" = DWIE rzeczy". Świeży grep pokazuje **CZTERY** osie tego samego tokenu w kodzie decyzyjnym:

| # | Znaczenie | Świeże plik:linia | Wartości |
|---|---|---|---|
| 1 | **KLASA kuriera** (jakość/prędkość) | `feasibility_v2.py:355` `_tcap = 4 if tier=="gold" else 3`; `common.py:2162` `dwell_for_tier`; `common.py:2148` `DWELL_BY_TIER`; `common.py:2010` `tier_label='new'` | gold/std/std+/slow/new |
| 2 | **POZIOM ESKALACJI** (3-stopniowa reguła Adriana) | `dispatch_pipeline.py:725-746` `_esc_tier ∈ {2,3}` (Tier2=pierwszy-wolny ≤próg, Tier3=carry-aware cap-stretch); serializowany `best_effort_objm_esc_tier` **:743** | 2 / 3 |
| 3 | **WYMIAR SOLVERA OR-Tools** (soft-bound) | `route_simulator_v2.py:1260-1263` „tier-2 soft bound na committed... Łączy się z tier-1 (osobny wymiar w solverze)" | tier-1 / tier-2 |
| 4 | **TIER GPS** (dokładność/blend dwell) | `common.py:600` „tier-1 GPS bias −1.37→−0.39"; `common.py:611` „tier-1 GPS blended" | tier-1 (GPS) |

**Kolizja krytyczna:** R6 cap „TIER-AWARE 35/40" (protokół, `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` common.py:~2651, komentarz **:2657** „na RZADKIE dni niedoboru (tier-3, jak 16.05)") — tu „tier-3"=**poziom eskalacji/niedoboru** (oś 2), NIE klasa kuriera „slow" (oś 1). Ale `feasibility_v2:355` `tier=="gold"` = oś 1. **Dwa różne „tier-3" w sąsiednim kodzie.** `_esc_tier=3` (`dispatch_pipeline:739`) serializowany do `shadow_decisions` jako `best_effort_objm_esc_tier` OBOK courier-class `tier` — konsument joinujący po „tier" może pomylić eskalację z klasą. Brak glosariusza/single-source „tier" (grep potwierdza: ZERO kanonicznej definicji).
**dedup_hint:** `LT-tier-token-overload` (NOWY; pokrewny N „rozsyp progów 35/40" ale tu overload SŁOWNICTWA, nie progu). Faza F: rozdzielić nazwy (`courier_class` vs `escalation_level` vs `solver_dim` vs `gps_tier`).

---

## LE — `order_type` ENUM MIESZANY PL/EN: „elastic" vs „elastyk"

- **Wartość produkcyjna = „elastic" (EN):** `panel_client.py:692` `order_type = "czasowka" if prep>=CZASOWKA_THRESHOLD_MIN else "elastic"`. Także `czasowka_uwagi.py:6` „order_type='elastic'".
- **Pattern bezpieczny LIVE = negacja:** `common.py:3500` `return order_dict.get("order_type") != "czasowka"`; `state_machine.py:121`, `panel_watcher.py:805/1064/2150`, `sla_tracker.py:216` — wszystkie `== "czasowka"`. Negacja maskuje rozjazd „elastic"/„elastyk".
- **ALE positive-matcher na „elastic":** `tools/czasowka_uwagi_oracle.py:153` `r["order_type"] == "elastic"` — gdyby źródło zapisało „elastyk" (PL), oracle cicho miscount (elastyk→nie-elastic).
- **Fixtury+kod używają „elastyk" (PL):** `tests/test_czasowka_dispatchable_fleet_fix.py:44`, `tests/test_restaurant_violations.py:31` `"order_type": "elastyk"` → dowód że model mentalny rozjechany (część kodu „elastic", część „elastyk"). Display jeszcze inny: `daily_briefing.py:181` „elastyk"/„czasówka" (PL z diakrytykami).
- **3 reprezentacje:** enum `elastic`/`czasowka` (mix PL/EN, bez diakr.) ‖ display `elastyk`/`czasówka` (PL diakr.) ‖ termin domenowy „elastyk"/„elastyczne".
**Status:** CONFIRMED rozjazd; LIVE-impact dziś nikły (negacja dominuje) ale `czasowka_uwagi_oracle` + każdy przyszły positive-matcher = mina. dedup_hint: `LE-order-type-enum-spelling`.

---

## LU — JEDNOSTKI W PREFIKSIE / NIEJAWNA JEDNOSTKA POLA

1. **`eta_pickup_*` — 3 jednostki, ten sam prefiks:**
   - `eta_pickup_utc` (`dispatch_pipeline.py:4057/4061/4067/4077`, `:5862/5877`) = **absolutny UTC datetime**
   - `eta_pickup_min` (`common.py:3613/3642`, `_carry_chain_penalty`) = **minuty-od-teraz (duration ≥0)**; `common.py:3559` „Penalty = -COEFF * eta_pickup_min"
   - `eta_pickup_hhmm` (`shadow_dispatcher.py` derived) = **display HH:MM**
   → czytelnik myli `eta_pickup_min` (kara×minuty) z wartością zegarową. Most do A6 R5 (display≠decision), ale tu fasetka JEDNOSTEK.

2. **`czas_odbioru` vs `czas_odbioru_timestamp` — różnią się sufiksem `_timestamp`, ale to INNA jednostka I semantyka:**
   - `czas_odbioru` (`panel_client.py:7/688`) = **int minut** (ile restauracja potrzebuje na przygotowanie = prep duration)
   - `czas_odbioru_timestamp` (`panel_client.py:4/673`) = **Warsaw datetime** (faktyczny pickup wall-clock)
   → sufiks `_timestamp` to jedyny sygnał że to absolut a nie duration; mylące sąsiedztwo.

3. **Panel `time` param = integer minuty-od-teraz** (NIE HH:MM, NIE timestamp) — udokumentowana mina (CLAUDE.md Panel API: „time param w przypisz-zamowienie: integer minutes from now"). `--keep-time` musi re-fetch `czas_odbioru` i re-send int (`0` czyści UI). Cross-repo `parcel_assign.py:33/45-46` przekazuje `time_arg` jako str.

4. **`pickup_at`-rodzina: ≥7 nazw, niejawne TZ na bez-sufiksowych:** `pickup_at` / `pickup_at_warsaw` / `plan_pickup_at` / `_plan_pickup_at` / `_cd_plan_pickup_at` / `planned_pickup_at` / `new_pickup_at` (grep sufiksów dispatch_pipeline/shadow_dispatcher). Bez-sufiksowe (`pickup_at`, `pickup_ready_at`, `delivered_at`, `new_pickup_at`) NIE niosą TZ w nazwie — obok jawnie sufiksowanych `_utc`/`_warsaw` → niespójna konwencja nazewnicza (czytelnik musi traceować każde).
**dedup_hint:** `LU-units-in-name` (część pokrywa A6 R5 eta_pickup; reszta NOWA).

---

## L-kosmetyka (P3, audit-friction)

- **WARSAW const = 8 NAZW dla `ZoneInfo("Europe/Warsaw")`:** `WARSAW` (80×), `WAW` (15× inline w funkcjach — re-instancja per-call: `courier_resolver.py:1243/1261/1276`, `feasibility_v2.py:61`), `_WARSAW_TZ` (6×, `event_bus.py:32`), `_WARSAW` (4×, `pln_objective.py:36`), `_WAW` (2×, `manual_overrides.py:16`), `WARSAW_TZ` (2×, `panel_client.py:39`), `_w` (1×), `_waw` (1×). A1/L — to samo pojęcie, 8 nazw; utrudnia grep/audyt. dedup_hint: `L-warsaw-const-naming`.
- **DWA progi 60-min, różne pojęcia:** `auto_koord.py:32` `CZASOWKA_THRESHOLD_MIN=60` (granica czasówka) vs `EARLY_BIRD_THRESHOLD_MIN=60` (KOORD wczesny). Ta sama liczba, różne reguły — early-bird PRZEKWALIFIKOWANE jako redundantne z czasówką (lekcja #196, memory). dedup_hint: `L-dual-60min-threshold`.
- **Pole NAZWANE „warsaw", konwertowane do UTC:** `czas_kuriera_warsaw` → `parse_panel_timestamp` → aware-**UTC**; `route_simulator_v2.py:1277-1284` czyta `czas_kuriera_warsaw` i defensywnie `if tzinfo is None: →UTC` na polu o nazwie „warsaw". Nazwa sugeruje Warsaw, wartość płynie jako UTC. dedup_hint: `LN-naive-tz-convention-split` (manifest nazewniczy).
- **`route_podjazdy`/`podjazdy` = słowo w 2 implementacjach** (A1 smell #11 potwierdzony): engine `route_podjazdy.order_podjazdy` vs konsola `fleet_state._build_route` — to samo pojęcie „podjazdy/trasa", parytet niepilnowany importem. (Należy do A6 R2 route-order; tu odnotowane jako L-słownictwo.)

---

## ✅ POZYTYW (jedyny inwariant słownictwa/jednostki w kodzie)

`state_machine.py:61-95` `_sanity_*`: **„ISO `strftime('%H:%M')` MUSI == raw `czas_kuriera_hhmm`"** (sygnał korupcji parsera `_czas_kuriera_to_datetime`). To JEDYNY runtime-strażnik spójności pary jednostek (ISO-decision ↔ HH:MM-display) w całej klasie L. **BRAK analogicznego inwariantu dla:** konwencji naive-TZ (Camp A vs B), overloadu `tier`, enum `elastic`/`elastyk`, `eta_pickup_min` vs `_utc`. → Faza F: ten wzorzec inwariantu = szablon do replikacji.

---

## TABELA POKRYCIA (jawne — nie cisza)

### Zbadane (świeży grep + lektura)
| Obszar | Pliki/symbole | Werdykt |
|---|---|---|
| `tier` overload | feasibility_v2:355, dispatch_pipeline:725-746, route_simulator_v2:1260-1263, common:600/611/2148/2162/2657 | CONFIRMED 4 osie |
| naive-TZ split | common:467, panel_client:566/610, sla_tracker:167, state_machine:779/928, feasibility_v2:127/442/749/771, route_simulator_v2 (~20), plan_recheck:166/257/288/297, courier_orders:118, fleet_state:100/219 | CONFIRMED split + self-doc |
| shift_start midnight | courier_resolver:1240-1287 (start vs end) | CONFIRMED asymetria |
| czasowka/elastyk granica | auto_koord:32/41, czasowka_scheduler:128, panel_client:692, common:3500 | granica 60 = single-source (OBALONE „3 def" — zgodne z A2/protokół) |
| enum elastic/elastyk | panel_client:692, czasowka_uwagi_oracle:153, fixtury | CONFIRMED rozjazd PL/EN |
| jednostki pól | eta_pickup_{utc,min,hhmm}, czas_odbioru{,_timestamp}, pickup_at-rodzina, panel time param | CONFIRMED niejawne |
| WARSAW const naming | 8 nazw, count grep | CONFIRMED kosmetyka |
| peak window TZ | event_bus:101-108 (now.hour, tylko naive→Warsaw); feasibility_v2:475 (astimezone WARSAW) | event_bus latent (aware-UTC now→UTC hour), callers no-arg = OK; inconsistent vs feasibility |
| checkpoint_tz (patched) | checkpoint_tz_shadow:6, ENABLE_CHECKPOINT_TS_WARSAW_PARSE | PATCHED instancja LN |
| invariant pozytyw | state_machine:61-95 (ISO≡HH:MM) | jedyny strażnik |

### NIE-zbadane (luka jawna + powód)
1. **courier-app Kotlin** (`/root/courier-app` RouteLogic.kt) — render czasów/TZ po stronie apki NIE czytany (poza budżetem; A6 luka #1 — apka API-driven, ETA serwerowa przez courier_api; lokalny re-format Kotlin niezweryfikowany). Faza B/J.
2. **Pełen trace call-site `_iso` vs `_parse_ts`** w fleet_state.py — które pole „Warsaw naive" trafia do którego helpera = LIVE-impact LN-x. Read-only, nie traceowałem (PLAUSIBLE→Faza C oracle: porównaj HH:MM konsoli vs engine na polu picked_up_at).
3. **most paczki** (`parcel_lane_merge`/`parcel_assign`) TZ/jednostki — natywny tor orders_state; `time_arg` jako str odnotowany, ale pełen parse-path paczki nie prześwietlony. Faza B.
4. **czasowka_proactive/`*` + cod_weekly/daily_accounting** TZ — peryferia (PERI), poza rdzeniem decyzji; nie audytowane.
5. **Wartość LIVE-błędu naive-TZ** (czy jakaś realna wartość Warsaw-naive omija granicę DZIŚ) — NIE zmierzone (read-only; to oracle Fazy C: grep `shadow_decisions` na anomalie +/-120min między eta_pickup_utc a oczekiwanym). Deklaruję LN jako **latentne/symptom** (math-guardy dead w normalnym path, czekają na ominięcie granicy), z 1 CONFIRMED nawrotem (checkpoint).
6. **GRANICA:** Mailek/Papu — poza zakresem.

---

## HANDOFF Faza D/E/F

- **Faza E (dedup, anty-double-count):** LN (naive-tz-split), LN-x (fleet_state intra-file), checkpoint_tz (patched) = **JEDEN root** `naive-datetime convention` (NIE 3 chaosy). LM (shift_start midnight) ⊂ pokrewny A6 R4 ale OSOBNA fasetka date-anchoring (nie floor). LT (tier overload) = NOWY root słownictwa, NIE myl z N (progi 35/40). LU eta_pickup ∩ A6 R5 (group 7) — jednostkowa fasetka tego samego pola, raportować RAZEM z R5.
- **Faza D (konflikt/precedencja):** LN tworzy potencjalny I-konflikt: gdyby Warsaw-naive `shift_start` dotarł do `feasibility_v2:749` (naive→UTC), HARD pre-shift gate liczy okno +2h przesunięte = sprzeczność z kanonem pre-shift (A6 R4). Para do grafu D: „naive-tz w HARD-bramce ↔ konwencja boundary".
- **Faza F (target kontrakty):** (1) typ/kontrakt „aware-UTC od granicy" + assert-no-naive w math-layer (LN); (2) rozdzielenie nazw `tier`→{courier_class, escalation_level, solver_dim, gps_tier} (LT); (3) ujednolicić `order_type` enum (LE) — jeden język; (4) `_min`/`_utc`/`_hhmm`/`_timestamp` jako WYMUSZONA konwencja sufiksu jednostki (LU); (5) replikować inwariant `state_machine:61-95` (ISO≡HH:MM) na pary TZ; (6) `_shift_start_dt` symetryczny do `_shift_end_dt` (LM).
- **Faza C (oracle dla LN-x/LN):** porównaj HH:MM konsoli (`fleet_state`) i apki (`courier_orders`) vs engine na polu Warsaw-naive (picked_up_at/delivered_at) — rozjazd ±120min = CONFIRMED live; `ziomek_time_route_monitor` może już to nieść.
