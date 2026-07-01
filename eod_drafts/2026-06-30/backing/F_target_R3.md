# FAZA F — STAN DOCELOWY rodziny **R3 „Prawda"** (klasy D · E · N)

> **⚠️ DRAFT — produkt syntezy audytu READ-ONLY (sesja tmux 2).** Zero kodu, zero flipów, zero restartów, zero `--notify`, zero git. Ten dokument definiuje **KANONICZNY STAN DOCELOWY + PLAN KONSOLIDACJI** dla rootów rodziny R3. Każda zmiana kodu = OSOBNY mini-sprint protokołem ETAP 0→7 + ACK Adriana. **Numery linii zweryfikowane ŚWIEŻYM grep DZIŚ — HEAD silnik `8024705` (2026-06-30 10:23) — DRYFUJĄ (≥3 żywe sesje/repo), re-grepuj przed dotknięciem jako pewnik.**

**Data:** 2026-06-30 · **Tryb:** READ-ONLY · **HEAD:** `8024705` (working tree `.py` czysty)
**Wejście:** `E_dedup_2_truth_conflict.md` (R1·R3·R4·R6·R8·R11·R14·R25 = klaster prawda) + `E_dedup_3_semantics_lifecycle` (granice H/K) + werdykty adwersaryjne FAZY E (objm-twins CONFIRMED, carried-guard CONFIRMED, flag-state-3-layer CONFIRMED/harm-LATENT) + lane-C oracle (`C02_bug4_reseq`, `C03_feas_carry`, `C14_min_delivered`, `C05_objm`, `C06_b_route`, `C18_void_claimed`) + lane-B kod (`B07_E_codeside` serializer, `B06_D_flag_drift`, `B17_N_thresholds`, `D03_flag_coupling`) + świeże greppy DZIŚ.
**Kontrakty referencyjne (DESIGN §4):** ①JEDNO-źródło/regułę(kopie=1) ②kontrakt-warstw(HARD-przed-SOFT+inwarianty) ③parytet-bliźniaków(divergence=0) ④prawda-flag(dead=0,rejestr) ⑤prawda-przyrządów(void=0 PRZED flipem) ⑥brak-dryfu-semantyki(display≠decision) ⑦kompletność-cyklu-życia(0-bez-GC) ⑧koherencja(0-konfliktów).

---

## 0. ZAKRES — które rooty należą do R3 (+ granice anty-double-count)

Rodzina **R3 = „Prawda"** — naruszenie zaufania do INSTRUMENTU / FLAGI / PROGU: liczba, którą czyta człowiek lub którą bramkujemy flip, **NIE odpowiada rzeczywistości**. Trzy pod-rodziny:
- **E (przyrząd kłamie / jest niemy):** instrument liczy z PREDYKCJI bez joina z fizyką, bliźniak-cień rankuje inaczej niż live, metryka GINIE w serializerze, werdykt czyta ZŁE/NIEŚWIEŻE źródło, inwariant źle zdefiniowany.
- **D (flaga kłamie o efektywnym stanie):** stan decyzyjny ≠ jeden plik (3 warstwy per-proces), przyrząd biega z pustym env → czyta flagi silnika jako default-OFF, fingerprint pokrywa część flag = fałszywy parytet.
- **N (próg kłamie o jednolitości):** ta sama liczba progowa w N miejscach z mieszanym override; strojenie wyjątku cicho rozsynchronizowuje bazę.

**8 przetrwałych rootów (po dedup + adwersaryjna weryfikacja FAZY E):**

| # | Root | Sev | Klasy | Werdykt | source | open | Pod-rodz. | Objaw „prawdy" (1 zdanie) |
|---|---|---|---|---|---|---|---|---|
| **R3-E1** | `feas-carry-instruments-predict-not-outcome` | P1 | E,G,M,C,B,F | CONFIRMED | TAK | TAK | E | 3 przyrządy liczą benefit/regret z PREDYKCJI `objm_r6_breach`, ZERO joina z `decision_outcomes` → trigger fantomowy 85%, akcja promuje HARD-NO. |
| **R3-E2** | `objm-shadow-canary-twins-alltick` | P1 | E,B,G,I,N,M | CONFIRMED | TAK | TAK | E | `peak_verdict` headlineuje ALL-TICK (×7-11 zawyżka) — fix poszedł do bliźniaka-monitora, peak_verdict nietknięty; uzbrojony na flipie POST_SHIFT. |
| **R3-E3** | `bug4-reseq-invariant-misspec` | P1 | E,F,O,H | CONFIRMED | TAK | TAK | E | Inwariant `delta≥0` źle zdefiniowany (objektyw=SLA+total_duration, nie OSRM-drive) → 11,5% legalnych „suspect", own-gate PADA i pisze „wciąż skażony". |
| **R3-E4** | `serializer-allowlist-metrics-vanish` | P1 | E,N,B | CONFIRMED | TAK | TAK | E (META) | `_AUTO_PROP_PREFIXES` = allowlist BEZ kontroli kompletności → 38 kluczy ginie (14 HARD); `eta_source`=0/858 (prowenancja ETA niewidoczna). |
| **R3-E5** | `verdict-reader-wrong-stale-source` | P1 | E,H | CONFIRMED | TAK | TAK | E | Werdykty czytają ZŁE/NIEPEŁNE źródło: rotation-blind (ślepy na `.1`), zamrożony `sla_log` (mtime 20.06), dual-path `dispatch_state` vs `scripts/logs` → fałszywe „mało danych". |
| **R3-D1** | `carried-first-guard-empty-env-void` | P1 | D,E | CONFIRMED | TAK | TAK | D | Strażnik biega z PUSTYM env systemd → reużyte funkcje silnika czytają 14 route/canon flag jako default-OFF → `no_position` 88% fikcji; claim „liczy IDENTYCZNIE jak silnik" = fałsz. |
| **R3-D2** | `flag-state-3-layer-no-single-source` | P1 | D,E,**J** | CONFIRMED | TAK | TAK (harm LATENT) | D (fundament) | Stan decyzyjny w 3 warstwach (flags.json hot / drop-in env-frozen / stała modułu); `flag_fingerprint` 63/≥90 → „fingerprinty identyczne = parytet" = fałszywe zapewnienie. |
| **R3-N1** | `numeric-threshold-scatter-mixed-override` | P2 | N | CONFIRMED | TAK | TAK | N (META) | Ten sam próg w N miejscach z mieszanym override (bare/env/flags.json-hot) BEZ reguły; strojenie hot-wyjątku rozsynchronizowuje bare-bazę. |

**Wiodący kontrakt §4 dla CAŁEJ rodziny = §4.5 „prawda-przyrządów (void=0 PRZED flipem)"** — żaden flip/zmiana silnika nie może być uzasadniony liczbą z przyrządu VOID/UNTESTED/twin-divergentnego/czytającego-złe-źródło. Kontrakty wspierające: **§4.4** (prawda-flag — R3-D1/D2), **§4.6** (brak-dryfu-semantyki display≠decision — serializer, objm-cień, bug4-display-delta), **§4.1** (jedno źródło — N-progi, jeden loader ledgera), **§4.3** (parytet bliźniaków — objm-twins, feas-carry blind↔readmit, serializer A/B), **§4.7** (cykl-życia — stale `.txt`, rotacja).

**Granice (NIE liczę podwójnie — cross-ref do innych rodzin/agentów):**
- **`flag-state-3-layer` (R3-D2) = WSPÓLNY z R1-D.** F_target_R1 prowadzi target „JEDEN rejestr flag" (aspekt §4.1 jedno-źródło + klasa J cross-repo). **R3 NIE re-derywuje konsolidacji — przejmuje TRUTH-aspekty:** §4.4 (flaga kłamie o efektywnym stanie, dead-but-ON) + §4.5 (fingerprint = kłamiący przyrząd, fałszywy-parytet). **Fix jest JEDEN** (rejestr, R1 FAZA 0) — R3 dokłada inwarianty prawdy, nie 2. plan.
- **`carried-first-guard` (R3-D1): FIX własności R1-D FAZA 0.3** („instrument reużywający funkcje silnika dziedziczy env silnika"). F_target_R1:26 jawnie deleguje: „raportowany pełniej w rodzinie R3-Prawda". **R3 raportuje go pełniej (E/D manifestacja: no_position 88% fikcji), konsolidacja = R1 0.3** — nie dubluję kroku.
- **R6-cap „35 płaski vs 40 tier" (N1 z B17) = root R7** `r6-cap-35-flat-vs-40-tier-plus-quantile` (I,N,G — bo to inwersja HARD↔SOFT + kalibracja na złej osi = koherencja, agent R7). **`czasowka-60 silent-desync` (N2 z B17)** — aspekt cichej-inwersji-po-hot-knob = R7 (D05 early-bird↔czasówka undefined); aspekt rozsypu-wartości odnotowany TU jako sub-N1 cross-ref. **R3-N1 obejmuje META-rozsyp** (R27±5, preshift-30, bag-cap, margin-15, spread-8, DWELL, V324/V325 dead-twin, override-path-chaos) — NIE re-derywuję R6-cap/czasówki.
- **`instrument-append-jsonl-silent-swallow`** (R9 dedup, fam R5/M) — wspólny `_append_jsonl` połyka → agent M. TU tylko most: każdy E-przyrząd dziedziczy ten swallow (feas-carry `:413`, bug4 fail-soft, min_delivered `:6047` — wszystkie „mogą milczeć null zamiast krzyczeć").
- **`stale-txt-verdict-no-ttl`** (R10 dedup, fam R6/H) + **`dead-producer-orphan-consumer-shadow-logs`** (R12, fam R6) — agent lifecycle. TU cross-ref: bug4 F5 stale `.txt`, verdict-reader stale `result.txt`/`atrun.log` — manifestacje H w MOICH E-rootach (ten sam mechanizm braku TTL).
- **`frozen-lexqual-shadow`** (R1-C, fam R1, source=NIE) — resztka unifikacji selekcji. Sprzężona z R3-E2 (wspólny flip `ENABLE_POST_SHIFT_OVERRUN_PENALTY` uzbraja OBA) — ruszać RAZEM, ale rdzeń-target w R1-C.
- **Out-of-engine position-classifier-drift** (allocation-agent) + **oś-kalibracji poślizg-odbioru** (calibration-agent, root `calibration-on-wrong-axis`) — TU tylko jako PRZYCZYNA „złej osi" w E-rootach (feas-carry trigger fantomowy = G; bug4 zła oś kosztu; objm G-1 quantile). Mechanizm-prawdy TU, oś-kalibracji TAM.
- **Sentinele (0,0)/BIALYSTOK_CENTER/V328** (sentinel-agent, K5) — TU most: sentinele-JAKO-DANE wpadające do przyrządów (feas-carry objm~10000 sum, carried-guard no_position, serializer V328-diag-ginie, `eta_source` fikcja). Produkcja sentineli = TAM.
- **STOP na dyspozytorni** — Mailek/Papu poza zakresem.

---

## 1. CROSS-CUTTING — KONTRAKT PRAWDY PRZYRZĄDÓW (produkt §4.5 + §4.4 + §4.6)

Stan docelowy R3 zaczyna się od JEDNEGO żywego artefaktu = **REJESTR PRAWDY PRZYRZĄDÓW** (analogiczny do MACIERZY-warstw w R2): każdy instrument/flaga/próg ma jawny **status-prawdy** i **co bramkuje**. Dziś ten rejestr nie istnieje jako kontrakt — `A4_instrument_registry` jest inwentarzem, ale bez egzekwowanej kolumny „validated/void/untested + outcome-join".

**REJESTR PRAWDY (szkielet — kolumny kontraktu):**

| Instrument | Czyta (źródło-prawdy) | Status DZIŚ | Co BRAMKUJE (flip/decyzja) | Naruszony kontrakt |
|---|---|---|---|---|
| `feas_carry_readmit_replay` + blind_review + postflip | `feas_carry_blind_shadow.jsonl` (PREDYKCJA) | 🔴 VOID | `ENABLE_FEAS_CARRY_READMIT` (#483000) | §4.5 (predykcja≠outcome), §4.3 (blind↔readmit asym B) |
| `objm_lexr6_peak_verdict._g2c_note` | `shadow_decisions` reorder all-tick | 🔴 VOID (×7-11) | Faza-4 objm ON-vs-rollback (at-200, 03.07) | §4.3 (twin≠monitor), §4.6 (headline≠gate) |
| `bug4_reseq_verdict` (health-gate) | `bug4_reseq_shadow.jsonl` | 🔴 VOID (inwariant mis-spec) | GO/WAIT sprint re-sekwencji (02.07) | §4.6 (delta na drive≠objektyw), §4.7 (stale .txt) |
| serializer `_AUTO_PROP_PREFIXES` | — (38 kluczy ginie) | 🔴 NIEMY (14 HARD) | kalibracja O2/SLA-anchor (02.07), audyt R6 | §4.6 (compute-but-vanish), §4.1 |
| `min_delivered_at_verdict` / `b_route_shadow_review` | ZŁY plik (rotation-blind / mtime-20.06) | 🔴 VOID (fałszywy-negatyw) | A/B min-total; b_route GO-kandydat | §4.5 (złe źródło), §4.7 (rotacja/TTL) |
| `carried_first_guard` | reużyte funkcje silnika @PUSTY env | 🔴 VOID (no_position 88% fikcji) | (read-only detektor nawrotu carried-first) | §4.4 (env-default-OFF), §4.5 |
| `flag_fingerprint()` | 63/≥90 flag decyzyjnych | 🔴 VOID (fałszywy-parytet) | „parytet bliźniaków = OK" przed flipem | §4.4 (rejestr niepełny) |
| `min_delivered` PRODUCENT (silnik) | `predicted_delivered_at` (PROXY) | 🟡 VALIDATED-logic / proxy-data | (j.w.) | §4.5 (proxy ≠ GT — oznaczone) |
| bug4 KSIĘGOWANIE (5623122) | `plan.sequence` + skip carried | 🟢 VALIDATED (OSRM ground-truth) | (j.w.) | — |

**Cel rejestru: kolumna „Status" = 🟢 dla KAŻDEGO przyrządu, który bramkuje JAKIKOLWIEK flip — ZANIM flip się odbędzie.** To strukturalizacja lekcji C9/C11 („oracle-gate > więcej recenzentów"): instrument nie ma prawa głosu w decyzji flip, póki sam nie przeszedł oracle z joinem do fizyki.

**INWARIANTY PRAWDY (docelowa suite — czerwone-na-start, zielone-po-konsolidacji):**

- **INV-TRUTH-1 (outcome-join przed flipem, E):** każdy przyrząd, którego liczba uzasadnia flip („benefit ✅", „materialność ✅", „GO"), MUSI joinować fizyczne źródło (`gps_delivery_truth.jsonl` GT, lub `decision_outcomes.jsonl` button-truth z jawnym proxy-caveatem) — ALBO być jawnie oznaczony „prediction-space, NIE flip-justyfikacja". *Test:* grep „benefit/materialność/GO" w werdykcie ⇒ plik referuje `delivered_at`/`r6_actual`/`gps_delivery_truth`. Dziś: feas-carry 0 referencji, min_delivered 0, bug4 0.
- **INV-TRUTH-2 (parytet bliźniaków przy KAŻDYM stanie flag, E/§4.3):** cień/canary ≡ live BAJT-identycznie na wspólnym wejściu przy OBU/WSZYSTKICH istotnych stanach flag (nie tylko przy obecnym OFF). *Test:* golden `objm_shadow_lex_qual ≡ objm_lexr6.lex_qual` przy `POST_SHIFT∈{OFF,ON}`; `peak_verdict.reorder_pct ≡ canary_monitor.reorder_pct` (per-decyzja, nie all-tick); feas-carry replay-bramka ≡ bramka LIVE (newbag, nie redirect_objm).
- **INV-TRUTH-3 (kompletność serializera, E/§4.6):** każdy klucz `metrics` ∈ {prefiks} ∪ {explicit} ∪ {świadomie-wykluczone-z-powodem} — kontrola KOMPLETNOŚCI (deny-list/test), nie allowlist. *Test:* `set(wszystkie metrics-keys) − set(serializowane) ⊆ set(jawnie-wykluczone)`; nowy klucz HARD bez decyzji = CZERWONY.
- **INV-TRUTH-4 (czytelnik czyta KOMPLETNE+POPRAWNE+ŚWIEŻE źródło, E/H/§4.7):** każdy werdykt na `shadow_decisions` jest rotation-aware (`.1`/`.gz`) + repoint na MASTER `scripts/logs/` (NIE `dispatch_state/`) + każdy `.txt`-werdykt ma timestamp+TTL+„stale" marker. *Test:* `min_delivered_at_verdict` na świeżym + `.1` = 802+357 non-null (nie 0); `b_route_shadow_review.real_joined > 0`.
- **INV-TRUTH-5 (env przyrządu = env silnika, D/§4.4):** instrument reużywający funkcje silnika (carried_first_guard, każdy przyszły) dziedziczy stan-flag silnika (drop-in/jawny config), nie pusty default. *Test:* `flag_fingerprint(guard-proc) ≡ flag_fingerprint(plan-recheck-proc)` na flagach route/canon.
- **INV-TRUTH-6 (flaga/próg = JEDNO źródło, D+N/§4.4+§4.1):** (a) flaga decyzyjna ⇒ w rejestrze ∧ w fingerprincie ∧ strippowana conftest ∧ efektywny-stan = funkcja JEDNEGO źródła; (b) próg decyzyjny ⇒ 1 nazwana stała + 1 ścieżka override, baza i wyjątek w TYM SAMYM mechanizmie (runtime-inwariant np. `early_bird_threshold == czasowka_threshold`, `40 == 35 + margines` z bramką). *Test:* fingerprint pokrywa wszystkie decyzyjne; `r6_cap_for_tier()` jedyny producent capa.

Mapowanie inwariant→pod-rodzina: **E** → 1,2,3,4 · **D** → 5,6(flaga) · **N** → 6(próg).

---

## 2. STAN DOCELOWY PER ROOT (twardy kontrakt + inwariant runtime)

### ▰ POD-RODZINA E (przyrząd kłamie / niemy)

### R3-E1 — `feas-carry-instruments-predict-not-outcome` (P1, CONFIRMED, źródło, OTWARTY)

**CO DZIŚ (entropia, świeżo zweryfikowane — lane-C oracle C03):**
- 3 przyrządy (`feas_carry_readmit_replay`, `feas_carry_blind_review`, `feas_carry_readmit_postflip`) + bliźniacza para w silniku (`dispatch_pipeline.py:1215 _feas_carry_blind_shadow` / `:1289 _feas_carry_readmit_pick`, wpięcie `:6256-6301`) liczą `regret/benefit/true_delta` z **PREDYKCJI** `objm_r6_breach_max_min` (`objm_lexr6.py`), **ZERO joina z `decision_outcomes.jsonl`** (który ISTNIEJE, FRESH, ma `r6_actual_min`/`delivered_at`).
- JOIN button-truth (zrobiony w oracle): trigger fantomowy — predykcja `chosen_forgiven_breach>0` = **99,7%** vs realny breach **14,8%** → ~85% „wybaczonych breachy" które instrument ma naprawiać NIGDY się nie zmaterializowało. „Benefit" SUM sentinelowo skażony (median 9,7 vs mean 302 vs max 10177 — sentinel ~10000-min niefizyczny dla R6=35, most do K5). Korzyść = KONTRFAKT nieobserwowalny (81,5% celów redirectu nigdy nie pojechało).
- **Asymetria bliźniaków (§4.3):** LIVE kapuje na `_newbag` (czas NOWEGO zlecenia, `:1322`); shadow jsonl **NIE MA pola newbag** → replay projektuje na `redirect_objm≤40` (WORST breach worka) = INNA bramka niż kod LIVE. „Dowód pozytywnego wpływu" mierzy bramkę, której silnik nie stosuje.
- Tripwire `bad_regret = regret≤0` (`postflip:81`) **strukturalnie martwy** (`regret=chosen−rej>0` z definicji). Akcja LIVE promuje verdict NO→MAYBE na `top[0]` (`:6278`) → na flipie dokłada REALNY breach (r6_new = nowe zlecenie ŁAMIE R6). Flaga `ENABLE_FEAS_CARRY_READMIT=False` (rollback po flipie 27.06), ale **replay DALEJ drukuje „benefit ✅ 54,3%"** → ryzyko re-flipu VOID-akcji.

**STAN DOCELOWY (kontrakt §4.5 + §4.3 + §4.6):**
1. **§4.5** Werdykt feas-carry („dowód pozytywnego wpływu") MUSI joinować `gps_delivery_truth.jsonl` (GT) / `decision_outcomes.jsonl` (button-truth, jawny proxy-caveat) PRZED jakąkolwiek flip-justyfikacją. Trigger liczony na REALNYM breach, nie predykowanym. `void/untested` przyrządu = **0 przed flipem zależnym** (INV-TRUTH-1).
2. **§4.3** Replay-bramka ≡ bramka LIVE: log shadow MUSI emitować `newbag` (pole którego dziś brak) → projekcja replaya kapuje na TEJ SAMEJ wielkości co `_feas_carry_readmit_pick` LIVE. `twin-divergence` (blind↔readmit) = **0**.
3. **§4.6** Etykieta „regret = redukcja worst-breach floty" jest NIEPRAWDZIWA gdy źródło breachu = carried (chosen dalej wiezie tamto zlecenie) — przedefiniować na rzeczywistą deltę floty `max(chosen_bez_nowego, rej_z_nowym)` lub usunąć etykietę. Tripwire `bad_regret` przedefiniować na sensowny (nie strukturalnie-martwy).
4. **Sentinel fail-loud (most K5):** wartości `objm`~10000 (49 rek >100) NIE wchodzą do SUM/median jako dane — sanityzacja u źródła ingestu (sentinel ≠ dana).

**INWARIANT RUNTIME (fail-loud):**
> Żaden werdykt feas-carry nie deklaruje „benefit/GO" bez joina do fizycznego outcome (INV-TRUTH-1). Drugi: `top[0]@emit.feasibility_verdict != 'NO'` (re-admisja NO→MAYBE za guardem `:6278` nie dożywa do emisji — cross-ref R2 ROOT-9 INV-LAYER-2). Flip `ENABLE_FEAS_CARRY_READMIT` sprzężony z re-enable zaworów KOORD (backstop dziś wyłączony).

**BRAMKA „ZERO NOWYCH KOPII":** dodać 1 pole `newbag` do logu + 1 join-harness (współdzielony z R3-E5/E3), NIE 4. przyrząd. Sekwencja: flaga OFF = INERT — naprawiać PRZED ewentualnym re-flipem #483000 (oracle PRZED, nie po). A4-rejestr: „NIE wskrzeszać" — target = albo napraw outcome-join, albo formalnie zarchiwizuj instrument.

---

### R3-E2 — `objm-shadow-canary-twins-alltick` (P1, CONFIRMED, źródło, OTWARTY)

**CO DZIŚ (entropia, świeżo zweryfikowane — FAZA_E verdict + C05):**
- `objm_lexr6_peak_verdict.py:37 _g2c_note` headlineuje **ALL-TICK** `g2c = 100*reorder_oids∩shadow_oids / n_orders` — BAJT-identyczne z metryką którą siostrzany `canary_monitor.py:339` SAM etykietuje „diagnostyka, ZAWYŻONE ×~3,5". Monitor-gate G2c używa `reorder_pct` PER-DECYZJA; peak_verdict NIE.
- Fix `397a665` (29.06) ruszył TYLKO `canary_monitor.py` (+test) — `peak_verdict.py` NIETKNIĘTY (mtime 26.06). **Twin-scatter: fix trafił monitor, nie bliźniaka.**
- Durable `.txt` SAM SOBIE PRZECZY: 29.06 headline `25,2%` (all-tick) obok gate `per-decyzja 3,7% (6/163)`; 26.06 `54,7%`, 28.06 `62,1%` → realne ~3,7-5,6% = **×7-11 zawyżka empirycznie potwierdzona**.
- **at-200 PENDING `Fri Jul 3 18:10`** → `objm_lexr6_peak_verdict >> checkpoint_2026-07-03.log` **bez `--dry-run`** → zapisze zawyżony headline + Telegram. Instrument decyzji Fazy-4 (ON-na-stałe `ENABLE_OBJM_LEXR6_SELECT=True` vs rollback).
- **Uzbrojony latentny most (sub):** `_objm_lexr6_shadow._lex_qual` (`dispatch_pipeline.py:~1122`) = ZAMROŻONA 3-krotka, NIE post-shift-aware; kanon `objm_lexr6.lex_qual` warunkowo 3/4-krotka. Zgodne TYLKO bo `ENABLE_POST_SHIFT_OVERRUN_PENALTY=False` + `ENABLE_OBJM_LEXR6_SELECT_SHADOW=False` (cień nawet nie wołany). Flip POST_SHIFT ON → cień rankuje INACZEJ = kłamiący przyrząd. **= R1-C `frozen-lexqual-shadow`** (dedup A6-gr1) — ruszać RAZEM.

**STAN DOCELOWY (kontrakt §4.3 + §4.6 + §4.5):**
1. **§4.3 / §4.6** `peak_verdict` headline = `reorder_pct` PER-DECYZJA (jak naprawiony monitor) — NIE all-tick. `twin-divergence` (peak_verdict ↔ canary_monitor) = **0**; headline ≡ gate w tym samym pliku.
2. **§4.5** at-200 (03.07) NIE wolno użyć do decyzji Fazy-4, póki headline kłamie — albo fix PRZED 03.07, albo at-200 z `--dry-run` + jawny „stale-axis" marker. `void` przyrządu-decyzji = **0 przed checkpointem**.
3. **§4.3 (sprzężone z R1-C)** Po PASS at-200: golden `objm_shadow_lex_qual ≡ canon lex_qual` BAJTOWO przy `POST_SHIFT∈{OFF,ON}`; przepiąć cień na kanon LUB usunąć. `copy-count` klucza-jakości (frozen inline) → 0 (ostatnia, R1-C 3.2).
4. Baseline G2b (single-day OFF 89,13% z 25.06) = znana stale-oś (C7-uznana POPRAWNA bramka STOP) — odświeżyć przy flipie, NIE traktować jako nowy defekt.

**INWARIANT RUNTIME:**
> `peak_verdict.headline_metric ≡ canary_monitor.gate_metric` (per-decyzja) — golden, oba czytają TĘ SAMĄ definicję (INV-TRUTH-2). `objm_shadow_lex_qual ≡ canon` przy każdym stanie POST_SHIFT (INV-TRUTH-2, wspólny z R1-C).

**BRAMKA „ZERO NOWYCH KOPII":** przepiąć headline na istniejącą per-decyzja metrykę (−1 zawyżona kopia), NIE dodać 3. metryki. Sekwencja: NIE forsować przed at-200 03.07 (świadomie zamrożony pod walidację, protokół C7 — „G2b STOP = poprawna bramka, NIE bug").

---

### R3-E3 — `bug4-reseq-invariant-misspec` (P1, CONFIRMED, źródło, OTWARTY)

**CO DZIŚ (entropia, świeżo zweryfikowane — lane-C oracle C02, OSRM ground-truth):**
- **KSIĘGOWANIE = VALIDATED** (3 fixy `5623122`: `fresh_deliv_order=plan.sequence`, skip fikcyjnych pickupów odebranych, same-set — recompute **0/1074 mismatch**, OSRM-wierny). To JEST naprawione.
- **Inwariant `delta≥0` (`plan_recheck.py:~1723`) ŹLE ZDEFINIOWANY:** deklaruje „wierny re-solve NIE może być GORSZY". FAŁSZ — klucz solve `:~1670` minimalizuje `(sla_violations, total_duration)`, NIE OSRM-drive. Solver świadomie wybiera WIĘKSZY drive dla mniejszej liczby naruszeń SLA / carried-first. Ground-truth brute-force (cid=515): min-DRIVE perm = FROZEN (11,3 min), fresh objective-optymalny +4,9 drive → `delta<0` legalny.
- **Skutek:** 123/1074 (**11,5%**) „suspect/skażony" jest PRZEWAŻNIE legalnych (48/123 mają `deliv_seq_differs=False` = carried-first interleaving, nie resekwencja wcale).
- **own health-gate PADA na żywo:** `bug4_reseq_verdict.py:~87` `suspect_pct≤10%` → 11,5% > 10% → `GO=False` I instrument pisze „⚠ pomiar WCIĄŻ SKAŻONY". → MEMORY „#1 NAPRAWIONE 29.06, instrument zdrowy" **sprzeczne z własnym werdyktem przyrządu na dzisiejszych danych.**
- `fresh_drive` (`:~1708`) liczone nad PROXY sort-ts (`events.sort` po predicted-ts), NIE nad `plan.sequence` (zła oś, F4). Migotanie etykiet 25%/18% per worek między tickami (verdict liczy REKORDY nie distinct-worki, F3). Stale `.txt` (29.06 07:41) kłamie „logger nic nie zapisał" mimo 1074 świeżych rek. (F5, klasa H).

**STAN DOCELOWY (kontrakt §4.6 + §4.7 + §4.3):**
1. **§4.6** Inwariant zdefiniowany na TYM SAMYM objektywie co solve (`total_duration`/`sla_violations`), NIE na proxy-drive. `delta<0` na osi-drive = LEGALNY (carried-first/SLA-driven) — nie „skażenie". Materialność/median liczone na `plan.sequence`, nie sort-ts.
2. **§4.6** Verdict liczy DISTINCT-worki (cid, frozenset(bag)) nie rekordy — eliminuje migotanie 25% (F3). Own-gate `suspect≤10%` przedefiniowany pod nowy (poprawny) inwariant — przestaje fałszywie PADAĆ.
3. **§4.7** Stale `.txt` z timestamp+TTL+„stale gdy mtime>kadencja" (wspólny z R3-E5/stale-txt) — czytający dostaje „świeży albo jawnie-stary", nie „pusty".
4. **§4.5** Po re-spec: przyrząd bramkuje GO/WAIT sprintu re-sekwencji (checkpoint 02.07) PRAWDZIWĄ liczbą — dziś WAIT z FAŁSZYWEGO powodu (zły inwariant), nie z braku materialności (22,5%/5,6 spełnione).

**INWARIANT RUNTIME:**
> `bug4 suspect ⟺ delta < 0 NA OSI total_duration` (nie OSRM-drive). Health-gate PADA tylko gdy realna materialność-na-objektywie < próg, nie gdy carried-first daje legalny drive-delta<0 (INV-TRUTH-3 + outcome-spójność).

**BRAMKA „ZERO NOWYCH KOPII":** przedefiniować istniejący inwariant + przełączyć licznik na distinct-worki (−migotanie), NIE dodać 2. shadow. Sekwencja: gated checkpoint 02.07 (bramkuje sprint feasibility↔route_simulator↔plan_recheck).

---

### R3-E4 — `serializer-allowlist-metrics-vanish` (P1, CONFIRMED, źródło, OTWARTY, META)

**CO DZIŚ (entropia, świeżo zweryfikowane — lane-B B07, ledger ground-truth):**
- `shadow_dispatcher.py:190 _AUTO_PROP_PREFIXES` = allowlist explicit-lub-prefiks (`:272` `any(k.startswith(p))`) **BEZ kontroli kompletności**. Mechanizm naprawy = dorzucanie prefiksu pole-po-polu (ostatnie 2 „naprawy" #6/F2 dodały `would_hard_cap`/`post_shift_overrun_` jako dosłowne stałe — załatały 2 RODZINY, nie mechanizm).
- **38 kluczy GINIE** (walidacja ground-truth: 858 świeżych decyzji peaku, każdy ginący = **0 wystąpień**; kontrolne #6/F2 ŻYWE: `would_hard_cap` 340×). Z 38: **14 HARD** (SLA-detail `sla_violations`/`_blocking_count`/`_pre_existing` `feasibility_v2.py:1182-1185`; R6-internal `r6_gold4_gate_recovered:1098`/`r6_paczka_exempt_oids:1117`/`r6_soft_zone_active:1130`; `pickup_dist_km:649`; D2 `d2_stale_schedule_soft/_soft_penalty:684-685`; C2 `c2_passes`+3 `:1285-1288`) + 5 PROV (`eta_source:5289` = prowenancja ETA: realny route vs FIKCJA; `sla_minutes_used`/`cs_tier_label`/`shift_start_min`/`shift_remaining_min`) + 7 DIAG (V328 `mass_fail_ratio`/`fleet_size`... = scarcity nieidentyfikowalna) + 6 SOFT.
- **Asymetria E2+B:** bliźniak `fail12_*` (ten sam gate schedule) ŻYJE przez prefiks, `d2_*` GINIE → częściowo widoczny gate. **Asymetria A/B serializera:** `prep_bias`/`pickup_debias` liczone TYLKO dla `best` (LOCATION B), nie `alternatives` (LOCATION A) → kontrfaktyk alternatyw ślepy.
- **Bramkuje sprint O2 SLA-anchor (02.07)** — kalibracja ready-anchor↔pickup_at z master-ledgera niemożliwa (SLA-detail ginie). **TEN SAM root** co 11 „compute-but-vanish" 28.06 (domknięte 2 rodziny, reszta otwarta).

**STAN DOCELOWY (kontrakt §4.6 + §4.1 + §4.3):**
1. **§4.6 / §4.1** Serializer = **kontrola KOMPLETNOŚCI** (deny-list LUB test): każdy klucz `metrics` jest serializowany ALBO jawnie wykluczony z POWODEM — nie explicit-allowlist. Jeden punkt prawdy ledgera. `metrics-vanish` (HARD) = **0**; nowy klucz HARD bez decyzji widoczności = CZERWONY test.
2. **§4.3** LOCATION A ≡ LOCATION B na polach kontrfaktycznych (prep_bias/pickup_debias/target_pickup dla alternatyw, nie tylko best) — replay alternatyw nie-ślepy.
3. Priorytet domknięcia: **14 HARD najpierw** (bramkują kalibrację) — szczególnie SLA-detail (O2 02.07) + R6-internal (audyt najważniejszej HARD-reguły) + `eta_source` (most do M-sentinel: czy zwycięska ETA = BIALYSTOK_CENTER fikcja). DIAG V328 = most do K5 (scarcity→pile-on).

**INWARIANT RUNTIME:**
> `set(klucze metrics liczone w silniku) − set(serializowane) ⊆ set(jawnie-wykluczone-z-powodem)` — test kompletności w CI (INV-TRUTH-3). Żaden klucz HARD nie ginie cicho; każdy nowy = decyzja {serializuj | wyklucz-z-powodem}.

**BRAMKA „ZERO NOWYCH KOPII":** zamienić allowlist→deny-list+test (1 mechanizm), NIE dodać 36. prefiks. To zamyka root RAZ (nie „carried-first naprawiane 10×" / „35. prefiks po raz N-ty").

---

### R3-E5 — `verdict-reader-wrong-stale-source` (P1, CONFIRMED, źródło, OTWARTY)

**CO DZIŚ (entropia, świeżo zweryfikowane — lane-C oracle C14 + C06):**
- **(a) Rotation-blindness:** `min_delivered_at_verdict.py:16` czyta TYLKO żywy `scripts/logs/shadow_decisions.jsonl` (whole-file, brak `.1`/`.gz`). Logrotate `/etc/logrotate.d/dispatch-v2` = **dzienny** → dane 25-26.06 (357 non-null) wylądowały w `.1`, niewidoczne. Odpalony 27.06 07:00 (głęboka noc po rotacji, przed lunch) → żywy plik = garść nocnych = „non-null: 0, mało danych ⚠" = **FAŁSZYWY NEGATYW**. Żywy ledger DZIŚ = 802 non-null / 266 changed (33,2%). Siostrzany `objm_lexr6_canary_monitor` TEN SAM ledger czyta `.1`-aware — wzorzec naprawialny ISTNIEJE obok, nie zastosowany.
- **(b) Złe źródło (path-trap):** `b_route_shadow_review.py` czyta ZAMROŻONY `dispatch_state/sla_log.jsonl` (mtime 20.06) zamiast żywego `scripts/logs/` → `real_joined=0` (powinno ~289), GO-kandydat nieosiągalny. **Master-ledger leży w `scripts/logs/` NIE `dispatch_state/`** (dual-path pułapka).
- **(c) Stale `.txt` bez TTL (klasa H):** `result.txt`/`atrun.log` zamrożone 27.06 07:00 bez markera → czytający dostaje „0 changed, odrocz" gdy rzeczywistość = 33% material + regresja floty (re-run przerzuca werdykt na „MATERIAL leans NEITHER"). A/B odroczona na FAŁSZYWEJ przesłance (decyzja-by-nie-flipować obronna, ale uzasadnienie instrumentu = void).

**STAN DOCELOWY (kontrakt §4.5 + §4.7 + §4.1):**
1. **§4.1 / §4.5** JEDEN helper-loader ledgera: rotation-aware (`.1`/`.gz` po dacie) + repoint na MASTER `scripts/logs/shadow_decisions.jsonl` (NIE `dispatch_state/`). Każdy werdykt-tool importuje go (nie własny `open(SHADOW)`). `wrong/incomplete-source-reader` = **0**.
2. **§4.7** Każdy `.txt`-werdykt z timestamp+TTL+„stale gdy mtime>kadencja" (wspólny mechanizm z R3-E3/F5 + cross-ref root R10 stale-txt) — albo emit do durable jsonl z ts. `stale-txt-no-TTL` = **0**.
3. **§4.5** Re-run rotation-aware PRZED jakimkolwiek werdyktem A/B (min-total) — instrument napędza decyzję PRAWDĄ (33% material leans NEITHER / R-FLEET-LEVEL), nie fałszywym „inconclusive". Caveat proxy: `delivered_at`=predykcja → join `gps_delivery_truth.jsonl` zanim „sooner" uznane fizycznie (mediana 2,8 min w podłodze szumu predykcji).

**INWARIANT RUNTIME:**
> Każdy reader `shadow_decisions` = rotation-aware + master-path; każdy `.txt`-werdykt ma `mtime + TTL` marker (INV-TRUTH-4). Test: re-run `min_delivered` na świeżym+`.1` ⇒ non-null = 802+357 (nie 0); `b_route real_joined > 0`.

**BRAMKA „ZERO NOWYCH KOPII":** 1 wspólny loader importowany przez N werdyktów (−N własnych `open`), NIE N-ty rotation-blind reader. Każdy nowy werdykt powiela loader, nie pułapkę.

---

### ▰ POD-RODZINA D (flaga/env kłamie o efektywnym stanie)

### R3-D1 — `carried-first-guard-empty-env-void` (P1, CONFIRMED, źródło, OTWARTY) — fix własności R1-D 0.3

**CO DZIŚ (entropia, świeżo zweryfikowane — FAZA_E verdict, 4 metody):**
- Strażnik `tools/carried_first_guard.py` biega z PUSTYM env systemd (`systemctl show -p Environment` = puste, brak `.d/`/EnvironmentFile) → reużyte `plan_recheck._start_anchor`/`_apply_canon_order_invariants` czytają 14 route/canon flag jako default-OFF (`plan_recheck.py:347 ENABLE_GPS_FREE_ANCHOR = os.environ.get(...,"0")=="1"` at-import; konsumpcja `:570`). Silnik ŻYWY ma drop-iny `=1`.
- Empiryczny `carried_first_guard.jsonl` (1317 rek.): `no_position` **88,5%**; spośród 152 rozwiązanych pozycji **100% `gps_pwa`, ZERO przez last_event/committed/last_known_pos** — NIEMOŻLIWE przy flagach ON dla floty „bez GPS" → empiryczny dowód flags-OFF. cid=179: 166 rek. WSZYSTKIE no_position przez ~8,5h ciągłego multi-baggingu, gdy silnik go trasował (plan 29 oids). Claim docstring „liczy IDENTYCZNIE jak silnik" = fałsz.
- DISSENT (uczciwie): READ-ONLY detektor (zero wpływu na DECYZJE live, P1↔P2 sporne); alarm-risk ZACHOWANY (no_position też risk=True); tracona DROBNA kontrola kanonu carried-first; „fikcja" to flag-stłumiona pod-rozdzielczość, nie zmyślone dane.

**STAN DOCELOWY (kontrakt §4.4 + §4.5):**
> Przyrząd reużywający funkcje silnika MUSI dziedziczyć env silnika (drop-in / jawny config / wspólne źródło stanu-flag), nie pusty default. **= R1-D FAZA 0.3** (jedno źródło stanu-flag dla N procesów). Po fix: guard liczy IDENTYCZNIE jak silnik (kontrakt z docstringa spełniony), `no_position` spada do realnego (silnikowe kotwiczenie last_pos/committed widoczne).

**INWARIANT RUNTIME:** `flag_fingerprint(carried_guard_proc) ≡ flag_fingerprint(plan_recheck_proc)` na flagach route/canon (INV-TRUTH-5). Strażnik nie raportuje `no_position` tam, gdzie silnik MA kotwicę.

**BRAMKA / SEKWENCJA:** **konsolidacja NIE jest osobnym krokiem R3 — to R1-D 0.3.** R3 raportuje truth-manifestację (§4.5 void) + dostarcza INV-TRUTH-5 jako test akceptacji fixu R1. Fix net-poprawia (ściśle zbliża guard↔silnik), nie net-szkodzi → po R1-D 0.3 status guardu = 🟢 validated.

---

### R3-D2 — `flag-state-3-layer-no-single-source` (P1, CONFIRMED, źródło, OTWARTY, harm LATENT) — WSPÓLNY z R1-D

**CO DZIŚ (entropia, świeżo zweryfikowane — FAZA_E verdict /proc/environ + B06/D03):**
- Stan decyzyjny ≠ jeden plik: (1) `flags.json` hot (~233 klucze), (2) drop-iny systemd `Environment=` env-frozen, (3) stała modułu (`common.py`/`plan_recheck.py`/`panel_client.py`). Precedencja `decision_flag()` `common.py:~348` = flags.json → stała → False.
- `flag_fingerprint()` (`common.py:364`, `names = ETAP4_DECISION_FLAGS + _FINGERPRINT_EXTRA_FLAGS:322`) widzi **63 flagi**; `common.py` ma 168 ENABLE_* bool-global → **107 poza fingerprintem**. `/proc/<pid>/environ` shadow vs panel-watcher: REALNA divergencja env (`USE_V2_PARSER=1` tylko panel-watcher → shadow=V1; plan_recheck writer-flagi CARRIED_FIRST_RELAX/GPS_FREE_ANCHOR/... =1 tylko panel-watcher). → „fingerprinty identyczne = parytet" = **fałszywe zapewnienie** (klasa E, kłamiący przyrząd).
- **Harm = LATENT/scoped (nie active):** 63 monitorowane BAJT-identyczne (decision_flag→flags.json shared); divergentne flagi scoped-by-design (shadow NIE importuje plan_recheck → writer-flagi nie konsumowane) + spójne u konsumentów; 2 martwe-ON (`PANEL_IS_FREE_AUTHORITATIVE`/`TRANSPARENCY_SCORING`, 0 konsumentów) = inert. Harm = mina mis-flipu (OR_TOOLS/GROUPING sprzężone, double-insert) + luka przyrządu (false-parity) + dług rejestru.

**STAN DOCELOWY — TRUTH-aspekty (R3 własność; konsolidacja = R1-D):**
> **Fix JEDEN = R1-D „JEDEN rejestr flag" (FAZA 0).** R3 NIE re-derywuje rejestru/migracji env→ETAP4 (to R1, klasa J/§4.1). R3 dostarcza **kontrakty prawdy jako testy-akceptacji**:
1. **§4.5 (fingerprint = prawda):** `flag_fingerprint` pokrywa WSZYSTKIE flagi decyzyjne (nie 63/≥90) — cross-proces parytet REALNY. `void` (fałszywy-parytet) = **0**. Drop-in dodany do 1 serwisu a nie do bliźniaka MUSI być złapany porównaniem fingerprintów.
2. **§4.4 (dead=0):** 2 martwe-ON flagi usunięte/oznaczone; `dead-flag = 0`, 100% w rejestrze.
3. **§4.4 (efektywny-stan = funkcja jednego źródła):** flaga decyzyjna ⇒ jej stan per-proces = funkcja JEDNEGO źródła (koniec module-const-env-frozen dla decyzyjnych); usunąć inwersję maskującą `COMMIT_DIVERGENCE_VERDICT_GATE` (const-default≠json, cross-ref R7 R20).

**INWARIANT RUNTIME:** flaga decyzyjna ⇒ (a) w rejestrze, (b) w fingerprincie, (c) strippowana conftest, (d) efektywny-stan per-proces = funkcja jednego źródła (INV-TRUTH-6a). Cross-proces fingerprint-parytet = REALNY.

**BRAMKA / SEKWENCJA:** konsolidacja = R1-D FAZA 0 (R3 nie dubluje). R3 wnosi INV-TRUTH-6a + akceptację „fingerprint pokrywa wszystkie decyzyjne" jako DEFINICJĘ-ukończenia rejestru. Harm LATENT → niski priorytet wykonania, ale fundament wiarygodności dla CAŁEJ R3 (twin-parytet/instrument-env zakłada znany stan flag).

---

### ▰ POD-RODZINA N (próg kłamie o jednolitości)

### R3-N1 — `numeric-threshold-scatter-mixed-override` (P2, CONFIRMED, źródło, OTWARTY, META)

**CO DZIŚ (entropia, świeżo zweryfikowane — lane-B B17):**
- Ten sam próg skopiowany w N miejscach z mieszanymi ścieżkami override BEZ reguły który-którym; strojenie hot-wyjątku rozsynchronizowuje bare-bazę. META-root zwija rodziny N3-N10 (B17):
  - **R27 ±5** w 5 stałych (`OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN=5.0 common.py:2554` / `LATE_PICKUP_HARD_MAX_MIN=5.0 :2824` env / `LATE_PICKUP_SOFT_FREE_MIN=5.0 :2840` env / `V3274_FROZEN_PICKUP_WINDOW_MIN=5.0 :3122` bare) — komentarz `:2841` SAM przyznaje „spójne z HARD_MAX" = ręczny invariant bez strażnika.
  - **pre-shift floor „30"** = DWA progi (`V325_PRE_SHIFT_HARD_REJECT_MIN=30 :1972` bare vs `PRE_SHIFT_NEAR_MIN=30 :1990` env — różne mechanizmy, ta sama liczba).
  - **margin „15"** w 5 podsystemach (`REASSIGN_FWD_MARGIN`/`CZASOWKA_PROACTIVE_MIN_MARGIN`/`PENDING_RESWEEP_MARGIN` flags.json + `auto_proximity:71`/`auto_assign_gate:84` hardcode); `min_score` rozsyp 50/40/30/60/−100.
  - **deliv-spread „8.0 km"** w 2 (`R1_MAX_DELIV_SPREAD_KM feasibility:90` bare vs `BUNDLE_MAX_DELIV_SPREAD_KM common.py:2280` env — cross-ref R2 ROOT-7 A1).
  - **DWELL fallback twin** (common 1.0/3.5 ↔ route_simulator 1.0/3.5). **dropoff-after-shift „5"**: V324 ŻYWA `:1820` vs V325 MARTWY duplikat `:1997` (most do K dead-code).
  - **lying-doc (sub E/L):** `scoring.py compute_wait_courier_penalty` docstring `:126-129` (5/6/20/−5) ≠ stałe (`V3273_WAIT_*`=3/−8/15) — **JUŻ zatruł audyt A2** (powtórzył „20"). Klasa E (przyrząd-doc kłamie).
  - **META override-path-chaos:** 3 niespójne mechanizmy (bare-literał / env-frozen / flags.json-hot `FLAGS_JSON_NUMERIC_OVERRIDES common.py:270`, 25 kluczy wybiórczych) — w obrębie JEDNEJ reguły baza zamrożona, wyjątek hot → cichy rozjazd.

**STAN DOCELOWY (kontrakt §4.1 + §4.6):**
1. **§4.1** Jedna NAZWANA stała per pojęcie + jedna ścieżka override (reguła: każdy próg decyzyjny → SCALE-01 `load_flags().get(KEY, C.KEY)` hot ALBO jawnie bare-z-powodem). Baza i wyjątek w TYM SAMYM mechanizmie. `copy-count` per próg → 1; `override-path` per próg → 1. Helpery: `r27_pickup_tol()` (1 źródło dla 5 stałych ±5), `score_margin_confident()` (1 dla 5×15).
2. **§4.6 (lying-doc):** docstring generowany ze stałej LUB test `docstring == const` — przestaje zatruwać audyt.
3. Dead-twin: V325_DROPOFF (martwy) usunięty (cross-ref K dead-code). Redundancja za flagą (`V325_PRE_SHIFT_SOFT_PENALTY=−20` zerowane przez `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY` ON) zdjęta.
4. **Cross-ref (NIE re-derywuję):** R6-cap 35/40 = R7 `r6-cap-35-flat-vs-40-tier` (helper `r6_cap_for_tier()` tam); czasówka-60 = R7 (inwersja po hot-knob, runtime-inwariant `early_bird==czasowka` tam). TU tylko zaznaczam że RUSZAJĄ tym samym wzorcem „1 stała + 1 override + runtime-inwariant".

**INWARIANT RUNTIME:** każdy próg decyzyjny = 1 producent; runtime-inwariant relacji-bliźniaczej (`40==35+margines`, `early_bird==czasowka`, `late_soft==late_hard`) jako tripwire fail-loud (wzór `carried_first_guard`) — INV-TRUTH-6b. `docstring==const` test.

**BRAMKA „ZERO NOWYCH KOPII":** scal N-literałów → 1 helper/stała (−kopie), NIE dodaj 6. ścieżki override. Measure-first per protokół (P2, niski harm runtime, wysoki harm poznawczy/maintenance).

---

## 3. PLAN KONSOLIDACJI (zależnościowo; każdy krok REDUKUJE ≥1 metrykę entropii; bramka „ZERO NOWYCH KOPII")

**Zasada anty-entropii:** konsoliduj-nie-dodawaj; każdy krok ściśle redukuje void-instrument / twin-divergence / metrics-vanish / wrong-source / stale-txt / dead-flag / threshold-copy-count. **Lekcja przewodnia R3 = oracle-gate (C9/C11):** instrument nie głosuje w flipie, póki sam nie przeszedł oracle z joinem do fizyki. Wszystko dotykające kodu = OSOBNY ACK + ETAP 0→7, off-peak, replay ON↔OFF, parytet bliźniaków, pełna regresja `pytest tests/` vs baseline.

> **Kolejność wymuszona naturą R3:** najpierw FUNDAMENT (rejestr-flag R1-D + harness fizyki + rejestr-prawdy + kompletność-serializera), bo czyni KAŻDY downstream instrument WERYFIKOWALNYM (inaczej twin-parytet/outcome-join biegają na nieznanym stanie flag i bez źródła fizyki). Potem instrumenty od najmniej-ryzykownych-i-blokujących-kalibrację (serializer, reader) do gated-checkpointami (objm 03.07, bug4 02.07, feas-carry #483000). N-progi na końcu (niski harm runtime).

### FAZA 0 — FUNDAMENT (czyni prawdę instrumentów weryfikowalną — przed dotykaniem przyrządów)
- **S0.1** Rejestr flag = R1-D FAZA 0 (WSPÓLNY — nie dubluję): fingerprint pokrywa wszystkie decyzyjne, conftest/doc-baseline keyowane z rejestru, 2 martwe-ON usunięte, env→ETAP4 dla decyzyjnych. *Redukuje: dead-flag→0, fingerprint-void→0 (R3-D2).* Bramka: zero flag poza rejestrem.
- **S0.2** Instrumenty reużywające funkcje silnika dziedziczą env silnika (R1-D 0.3, carried_first_guard najpierw). *Redukuje: void-instrument (R3-D1)→validated.* Akceptacja = INV-TRUTH-5.
- **S0.3** Zbuduj **harness fizyki** (jeden loader joinujący `gps_delivery_truth.jsonl` GT + `decision_outcomes.jsonl` button-truth po order_id, z jawnym proxy-caveatem) — współdzielony przez feas-carry/min_delivered/bug4. *Przygotowuje INV-TRUTH-1 dla Faz 3.* Bramka: 1 harness, nie N joinów ad-hoc.
- **S0.4** Zbuduj **rotation-aware master-loader ledgera** (`.1`/`.gz` + repoint `scripts/logs/`) + konwencja `.txt`-TTL. *Przygotowuje INV-TRUTH-4 dla R3-E5/E3.* Bramka: 1 loader.
- **S0.5** Spisz **REJESTR PRAWDY PRZYRZĄDÓW** (§1) jako żywy artefakt + szkielet suite INV-TRUTH-1..6 (czerwone-na-start). *Czyni `void-instrument-count` MIERZALNYM.* Read/doc-only, brak ACK.

### FAZA 1 — SERIALIZER kompletność (R3-E4) — odblokowuje widoczność HARD pod kalibrację reszty
- **S1.1** Serializer allowlist → deny-list/kontrola-kompletności + test (każdy `metrics`-key serializowany ∨ jawnie-wykluczony). *Redukuje: metrics-vanish 38→0 (14 HARD).* Bramka: NIE 36. prefiks.
- **S1.2** Domknij 14 HARD najpierw (SLA-detail dla O2 02.07 + R6-internal + `eta_source`); LOCATION A≡B na polach kontrfaktycznych. *Redukuje: HARD-vanish→0; A/B-asym→0.* Bramka O2 02.07.

### FAZA 2 — CZYTELNIK + stale-txt (R3-E5) — werdykty przestają kłamić
- **S2.1** Repoint wszystkich werdyktów na rotation-aware master-loader (S0.4); `min_delivered`/`b_route` re-run rotation-aware. *Redukuje: wrong-source-reader→0.*
- **S2.2** Każdy `.txt`-werdykt z mtime+TTL+stale-marker (wspólne z R3-E3/F5; cross-ref root R10). *Redukuje: stale-txt-no-TTL→0.*

### FAZA 3 — VOID/TWIN przyrządy (gated checkpointami)
- **S3.1 (objm, R3-E2 — gated at-200 03.07)** `peak_verdict` headline → per-decyzja (jak monitor); golden twin-parity. *Redukuje: twin-divergence (objm)→0; headline-zawyżka×7-11→0.* Bramka: NIE przed 03.07 (C7).
- **S3.2 (frozen-lexqual, R1-C — RAZEM z S3.1 przy POST_SHIFT)** golden `shadow_lex_qual ≡ canon` przy OBU POST_SHIFT; przepiąć/usuń cień. *Redukuje: copy-count klucza-jakości→0.*
- **S3.3 (bug4, R3-E3 — gated 02.07)** re-spec inwariantu na objektyw (total_duration/SLA), distinct-worki, gate pod nowy inwariant. *Redukuje: false-suspect 11,5%→realny; migotanie→0.*
- **S3.4 (feas-carry, R3-E1 — flaga OFF, przed #483000)** outcome-join (S0.3 harness) + log `newbag` (replay≡LIVE) + re-spec etykiety regret + tripwire. *Redukuje: void→validated-lub-zarchiwizowany; twin-asym→0.* Bramka: oracle PRZED ewentualnym re-flipem; flip sprzężony z re-enable zaworów KOORD.

### FAZA 4 — N-progi (R3-N1) — niski harm runtime, na końcu
- **S4.1** Helpery 1-źródło: `r27_pickup_tol()`, `score_margin_confident()`, scal spread-8 (z R2 S1), DWELL-fallback; reguła override-path (SCALE-01 ∨ bare-z-powodem). *Redukuje: threshold-copy-count→1; override-path→1.*
- **S4.2** lying-doc → docstring-ze-stałej + test `docstring==const`; usuń V325_DROPOFF dead-twin + redundancję-za-flagą. *Redukuje: lying-doc→0; dead-twin→0.*
- **S4.3** runtime-inwarianty relacji-bliźniaczych (`late_soft==late_hard`, `40==35+margines` [z R7], `early_bird==czasowka` [z R7]) — tripwire fail-loud. *Redukuje: silent-desync→strażnik.*

### Sekwencja zależności (skrót)
```
FAZA 0 (rejestr-flag R1-D + harness-fizyki + loader-ledgera + rejestr-prawdy)
   [czyni twin-parytet/outcome-join WIARYGODNYMI; leczy env-void D1; fundament D2]
        ├──> FAZA 1 (serializer kompletność) ──> [odblokowuje HARD pod kalibrację O2]
        ├──> FAZA 2 (reader + stale-txt) ──────> [werdykty przestają kłamić]
        ├──> FAZA 3 (objm 03.07 ∥ bug4 02.07 ∥ feas-carry pre-#483000)  [gated]
        └──> FAZA 4 (N-progi, niski harm)
```
FAZA 0 PRZED wszystkim (bez znanego-stanu-flag + źródła-fizyki każdy instrument biega na nieznanym). FAZA 1+2 tanie i odblokowujące. FAZA 3 bramkowana zewnętrznymi checkpointami. FAZA 4 ostatnia.

---

## 4. DASHBOARD ENTROPII — rodzina R3 (DZIŚ → CEL)

| Metryka | Root | DZIŚ (zmierzone) | CEL | Krok |
|---|---|---|---|---|
| void-instrument (bramkuje flip) | E1/E2/E3/E5/D1/D2 | **8** VOID (feas-carry×3, objm peak, bug4-gate, min_delivered-verdict, b_route, carried-guard, fingerprint) | **0 przed flipem zależnym** | S0.3-0.5,S3,S2,S0.2 |
| outcome-join (przyrząd↔fizyka) | E1/E5 | 0 referencji `gps_delivery_truth`/`decision_outcomes` | **1 harness, wszystkie flip-instrumenty** | S0.3 |
| twin-divergence (cień↔live) | E1/E2 | objm ×7-11 zawyżka; feas-carry blind↔readmit (brak newbag) | **0** (golden przy każdym flagu) | S3.1,S3.4 |
| metrics-vanish (HARD) | E4 | **14 HARD** + 5 PROV + 7 DIAG ginie (0/858 ledger) | **0 HARD** (kompletność-test) | S1.1,S1.2 |
| wrong/incomplete-source reader | E5 | 2 (rotation-blind + path-trap `dispatch_state`) | **0** (1 master-loader) | S2.1 |
| stale-txt bez TTL | E3/E5 | ≥3 (bug4.txt, min_delivered result.txt, b_route) | **0** (mtime+TTL marker) | S2.2 |
| inwariant źle-zdefiniowany | E3 | 1 (`delta≥0` na drive≠objektyw) | **0** (na total_duration/SLA) | S3.3 |
| flag-fingerprint pokrycie | D2 | 63/≥90 (107 ENABLE_* poza) → fałszywy-parytet | **wszystkie decyzyjne** | S0.1 |
| instrument-env = silnik-env | D1 | carried-guard pusty env (no_position 88% fikcji) | **dziedziczy (fingerprint≡)** | S0.2 |
| dead-but-ON flagi | D2 | 2 (PANEL_IS_FREE, TRANSPARENCY_SCORING) | **0** | S0.1 |
| threshold copy-count (per próg) | N1 | R27±5 ×5, spread-8 ×2, margin-15 ×5, preshift-30 ×2 | **1** stała/pojęcie | S4.1 |
| override-path (per próg) | N1 | bare/env/flags.json wymieszane bez reguły | **1** ścieżka/próg | S4.1 |
| lying-doc (doc≠const) | N1 | 1 (scoring wait, zatruł A2) | **0** (doc-ze-stałej/test) | S4.2 |
| dead-twin próg (martwy duplikat) | N1 | 1 (V325_DROPOFF) | **0** | S4.2 |

**Reguła zdrowia (samo-zachowawcza, rozszerzenie Przykazania #0):** żaden przyszły sprint nie pogarsza żadnej liczby. Anty-wzorce R3 = RED: „nowy werdykt bramkujący flip BEZ outcome-join = RED · nowy klucz `metrics` HARD bez decyzji-widoczności = RED · nowy reader `shadow_decisions` bez rotation/master = RED · nowy `.txt`-werdykt bez TTL = RED · nowy próg-kopia bez nazwanej-stałej = RED · nowy przyrząd reużywający silnik bez env-dziedziczenia = RED".

---

## 5. CROSS-REF / GRANICE / OTWARTE PYTANIA DO ADRIANA

**Sprzężenia z innymi rodzinami (rusza RAZEM lub gateuje):**
- **R3-D2 (flag-state) + R3-D1 (carried-guard) = R1-D fundament** — konsolidacja w R1 FAZA 0; R3 wnosi INV-TRUTH-5/6 jako akceptację. FAZA 0 R3 = FAZA 0 R1 (jeden fundament dla obu rodzin).
- **R3-E2 (objm peak) ↔ R1-C (frozen-lexqual)** — wspólny flip `ENABLE_POST_SHIFT_OVERRUN_PENALTY`; ruszać RAZEM (S3.1+S3.2), oba gated at-200 03.07.
- **R3-E4 (serializer) → bramkuje R7 O2 SLA-anchor (02.07)** — SLA-detail musi być widoczna PRZED kalibracją ready-anchor↔pickup_at; `eta_source` most do M-sentinel; V328-diag most do K5 (scarcity).
- **R3-E1 (feas-carry) ↔ R2 ROOT-9 (hard-feasibility-split)** — re-admisja NO→MAYBE `:6278` za guardem = ten sam INV-LAYER-2 (re-assert na emit); ruszać spójnie.
- **R3-N1 (progi) ↔ R7 (R6-cap 35/40, czasówka-60) + R2 ROOT-7 (spread-8 A1)** — wspólny wzorzec „1 stała + 1 override + runtime-inwariant"; helpery `r6_cap_for_tier`/`czasowka_threshold` własności R7, `r27_pickup_tol`/`score_margin` własności R3.
- **Wszystkie E-rooty ↔ M (`_append_jsonl` swallow) + K5 (sentinele-jako-dane)** — fail-loud swallow (agent M) + sanityzacja sentineli u ingestu (agent K5/sentinel) wzmacniają INV-TRUTH-1 (sentinel ≠ dana w SUM/median).

**OTWARTE PYTANIA (priorytet/inwersje — PYTAJ, nie zgaduj):**
1. **R3-E1 feas-carry docelowy:** naprawić outcome-join (instrument żyje, gotowy pod ewentualny re-flip #483000) **vs** formalnie zarchiwizować (A4 „NIE wskrzeszać")? Rekomendacja-DRAFT: archiwizuj akcję LIVE (flaga OFF zostaje), ale NAPRAW „benefit ✅"-kłamstwo cienia (inaczej następna sesja re-flipuje na fałszywym nagłówku) — minimalny ruch = oznacz replay „prediction-space, NIE flip-justyfikacja" + outcome-join jako warunek-wskrzeszenia.
2. **R3-E2/at-200 03.07:** naprawić `peak_verdict` headline PRZED 03.07 (mały, trywialny — czytać per-decyzja jak monitor) czy puścić at-200 z `--dry-run`+stale-marker? Rekomendacja-DRAFT: fix PRZED (NIE net-szkodliwy, kasuje kłamstwo Fazy-4 decyzji).
3. **R3-E4 serializer kolejność:** 14 HARD wszystkie naraz vs SLA-detail-najpierw (bramkuje O2 02.07)? Rekomendacja-DRAFT: SLA-detail + R6-internal + eta_source jako P1-podzbiór przed 02.07, reszta HARD/PROV/DIAG w drugim kroku.
4. **R3-N1 zakres:** czy „1 ścieżka override = flags.json-hot wszędzie" jest OK, czy niektóre progi (R6-base 35, R27±5) MUSZĄ zostać bare/env restart-gated z powodów bezpieczeństwa (jak pytanie R1-D o route/canon)? Rekomendacja-DRAFT: HARD-bazy restart-gated z JAWNYM powodem-w-kodzie + runtime-inwariant relacji do hot-wyjątku; reszta hot.
5. **harm LATENT D2:** czy migrować 107-flag-poza-fingerprintem PROAKTYWNIE (fundament wiarygodności R3) czy tylko fingerprint rozszerzyć (tańsze, ale env-frozen zostaje)? Rekomendacja-DRAFT: rozszerz fingerprint TERAZ (tani, kasuje fałszywy-parytet), migracja env→ETAP4 per-flaga z R1-D.

---

## 6. POKRYCIE / CO NIE ROZSTRZYGNIĘTE (jawnie, nie cisza)

- **Wartości runtime parytetu/joina NIE udowodnione w tym dokumencie** (że feas-carry po outcome-join da „WAIT/archive"; że objm headline≡gate po fixie bajtowo; że serializer-kompletność-test złapie wszystkie HARD; magnituda regresji b_route po rotation-aware re-run) — to **Faza C oracle / PoC**, nie ten dokument syntezy (read-only). Lane-C JUŻ dał kierunki (C02/C03/C14 z joinami), ale flip-walidacja = osobny mini-sprint.
- **R3-D1/D2 konsolidacja delegowana do R1-D** — jeśli R1 zmieni formę rejestru, R3-INV-TRUTH-5/6 muszą się dostroić (akceptacja, nie niezależny plan). Tu trzymane jako wspólny fundament; „source-ness" D2 pełna w R1-D.
- **Granica E↔H↔M:** stale-txt (R10) i append-jsonl-swallow (R9) i dead-producer (R12) są fam R6/R5 (agenci lifecycle/M) — TU tylko cross-ref jako manifestacje w MOICH E-rootach (bug4 F5, każdy fail-soft). NIE re-derywuję ich rdzeni.
- **R6-cap 35/40 + czasówka-60** = R7 (`r6-cap-35-flat-vs-40-tier`) — TU tylko aspekt rozsypu-wartości (N) jako cross-ref; inwersja HARD↔SOFT + oś-kalibracji rozstrzygane w R7. NIE liczę podwójnie.
- **Proxy vs ground-truth (krytyczne dla INV-TRUTH-1):** `decision_outcomes` = button-truth (klik ~192s przed GPS, 0/377 auto_geofence GT); jedyny GT = `gps_delivery_truth.jsonl`. Harness S0.3 MUSI joinować GT (nie tylko button) zanim „benefit/sooner" uznane fizycznie — inaczej outcome-join goni artefakty predykcji (mediana 2,8 min w podłodze szumu). To granica metody, nie luka planu.
- **Numery linii dryfują** (≥3 sesje/dzień/repo) — zweryfikowane DZIŚ HEAD `8024705` (serializer `:190/272`, feas-carry `:1215/1289/6278`, objm `_g2c_note:37`, fingerprint `:364/322`, carried-guard `plan_recheck:347/570`, numeric `common.py:430/763/1972/2554/270`, min_delivered_verdict `:16`), ale PoC/zmiana MUSI re-grepować (Przykazanie #0 ETAP 0).
- **PoC = osobny ACK** — ten dokument NIE wybiera/nie pisze PoC. Kandydaci R3 wg dźwigni×ryzyka: **(a) serializer-kompletność (R3-E4 S1.1)** — najwyższa dźwignia (odblokowuje kalibrację 14 HARD + bramkuje O2 02.07), niskie ryzyko (decyzyjnie-neutralny, psuje tylko obserwowalność dziś), zamyka root RAZ; **(b) rotation-aware master-loader (R3-E5 S0.4/S2.1)** — tani, kasuje fałszywe-negatywy werdyktów. Rekomendacja-DRAFT PoC: **R3-E4 serializer-kompletność** (deny-list+test) — fundament prawdy-przyrządów, nie zmienia zachowania LIVE, dowód „ON≠OFF" = klucz HARD pojawia się w ledgerze.
