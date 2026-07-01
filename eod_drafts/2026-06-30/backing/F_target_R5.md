# FAZA F — STAN DOCELOWY rodziny **R5 „Stres/awaria"** (klasy M · G · O)

> **⚠️ DRAFT — produkt syntezy audytu READ-ONLY (sesja tmux 2).** Zero kodu, zero flipów, zero restartów, zero `--notify`, zero git. Ten dokument definiuje **KANONICZNY STAN DOCELOWY + PLAN KONSOLIDACJI** dla rootów rodziny R5. Każda zmiana kodu = OSOBNY mini-sprint protokołem ETAP 0→7 + ACK Adriana. **Numery linii zweryfikowane ŚWIEŻYM grep DZIŚ — HEAD silnik `8024705` (2026-06-30 10:23, working tree `.py` czysty) — DRYFUJĄ (≥3 żywe sesje/repo), re-grepuj przed dotknięciem jako pewnik.**

**Data:** 2026-06-30 · **Tryb:** READ-ONLY · **HEAD:** `8024705`
**Wejście:** `E_dedup_3_semantics_lifecycle.md` (podklaster R5 = F1·F2·F3·F4·F5) + `E_dedup_2_truth_conflict.md` (R9 swallow, R20 silent-OFF, granice E) + lane-B (`B09_G_calibration`, `B15_M_sentinel_sel`, `B16_M_sentinel_state`, `B18_O_concurrency`) + lane-C oracle (`C13_pickup_slip` VALIDATED znak-dodatni, `C07_drive_speed` N/A-flaga-OFF, `C15_carried_guard` VOID) + świeże greppy DZIŚ (flags.json efektywny, `_append_jsonl` ×6 tools, `os.replace`-bez-fcntl, V328/COORD_GUARD live).
**Kontrakty referencyjne (DESIGN §4):** ①JEDNO-źródło/regułę(kopie=1) ②kontrakt-warstw(HARD-przed-SOFT+inwarianty) ③parytet-bliźniaków(divergence=0) ④prawda-flag(dead=0,rejestr) ⑤prawda-przyrządów(void=0 PRZED flipem) ⑥brak-dryfu-semantyki(display≠decision) ⑦kompletność-cyklu-życia(0-bez-GC) ⑧koherencja(0-konfliktów).

---

## 0. ZAKRES — rodzina R5 = JAK SYSTEM SIĘ PSUJE pod stresem

Rodzina **R5 = „Stres/awaria"** — nie „co liczymy", lecz **JAKI JEST TRYB-BŁĘDU**, gdy dane są zatrute, oś kalibracji nietrafiona albo dwa procesy piszą naraz. Trzy klasy = trzy nieprawidłowe tryby-awarii:

- **M (cicha awaria / sentinel-jako-dana):** awaria jest NIEWIDOCZNA (bare-except połyka, sentinel `(0,0)` wpada w matematykę jako liczba, fail-open `return True` bez log) **lub OPTYMISTYCZNA** (kara znika → kandydat tańszy; jedzenie „zdąży" bo sentinel udaje 0). Brak operatora-widzialnego sygnału.
- **G (kalibracja na złej osi):** strojenie celuje w oś gdzie błędu NIE MA (noga jazdy ~0) albo selekcyjnie-zatrutą (delivery-pesymizm matched-courier), i **LUZUJE HARD-R6**; oś realnego błędu (poślizg odbioru +18..+27, prep +11..+13) jest OFF/shadow/pod-wymiarowana. Net: silnik systematycznie OPTYMISTYCZNY na świeżości.
- **O (współbieżność / wyścig):** stan dzielony pisany przez ≥2 procesy bez wspólnego locka → lost-update; read-with-side-effect; współdzielona sesja pod ThreadPoolExecutor. „Bezpieczne" tylko przez postawę (Telegram muted / self-healing), nie przez dyscyplinę.

**TEZA RODZINY (jedno zdanie):** każda z 3 klas to ta sama wada trybu-awarii — **awaria jest CICHA, OPTYMISTYCZNA i WYŚCIGOWA, zamiast GŁOŚNA, BEZPIECZNIE-PESYMISTYCZNA i ZSERIALIZOWANA.** Stan docelowy R5 = odwrócić te trzy przymiotniki.

### 0.1 Które rooty NALEŻĄ do R5 (final taxonomy vs dedup3)

| # | Root (final taxonomy) | Sev | Klasy | Werdykt | source | Pod-rodz. | = dedup3 |
|---|---|---|---|---|---|---|---|
| **R5-G1** | `calibration-on-wrong-axis` | P1 | G,E | CONFIRMED | **TAK** | G | F3 |
| **R5-M1** | `instrument-append-jsonl-silent-swallow` | P2 | M | PLAUSIBLE | NIE (manifestacja) | M | (R9 z dedup2) |

**Tylko 2 rooty mają `fam=R5` w finalnej taksonomii** — ale **KLASA M/G/O ma szerszy ślad**, rozdystrybuowany jako mosty/cross-cutting do innych rodzin (zgodnie z dedup, by nie liczyć podwójnie). Pełny ślad rodziny (z dedup3 R5-podklastra + manifestacje M/G/O w innych rootach):

| dedup3 | Root | Klasa | Własność konsolidacji | Status w TYM dok. |
|---|---|---|---|---|
| **F3** | `calibration-on-wrong-axis` (R5-G1) | **G** | **R5 (TU, pełny target)** | §2 G1 |
| (R9) | `instrument-append-jsonl-silent-swallow` (R5-M1) | **M** | **R5 (TU, pełny target)** | §2 M1 |
| **F1** | `coord-sentinel-no-ingest-chokepoint` | **M** (+B,+C) | sentinel/K5-agent + allocation (geometry-blind / no-global-deconflict) | §2 M2 — TU aspekt-M (fail-loud), konsolidacja XREF |
| **F2** | `schedule-fail-open-vs-fail-close-asymmetry` | **M**/K4 | R7 `schedule-data-3way-failopen-failclose` + floor-agent | §2 M3 — TU aspekt-M (cichy fail-open), koherencja XREF R7 |
| **F4** | `shared-state-no-lock-rmw` | **O** | **R5 (TU, pełny target)** | §2 O1 |
| **F5** | `cookiejar-threadpool-shared-session` | **O**/C | **R5 (TU, pełny target)** | §2 O2 |
| — | `load_plan` read-with-side-effect (O2) | **O** | R6a `courier-plans-lifecycle` (lifecycle-agent) | §2 O3 — TU aspekt-O, konsolidacja XREF R6a |

**Wiodący kontrakt §4 dla CAŁEJ rodziny = §4.5 „prawda-przyrządów" rozszerzony o TRYB-AWARII** — instrument/dana/kalibracja, która **cicho gubi sygnał, udaje liczbę albo stroi nieistniejący błąd, jest formą KŁAMSTWA przyrządu** (klasa C11 na poziomie systemowym). Kontrakty wspierające: **§4.8** (koherencja — JEDNA polityka fail-open/close, nie 3 sprzeczne; G-1 luzuje HARD = inwersja do rozstrzygnięcia), **§4.7** (cykl-życia — brak read-with-side-effect, GC), **§4.1** (jedno źródło — JEDNA dyscyplina locka zamiast N mityacji; JEDEN walidator coords u ingest; JEDNA oś kalibracji), **§4.6** (display≠decision — sentinel ≠ dana decyzyjna).

### 0.2 Granice (NIE liczę podwójnie — cross-ref do innych rodzin/agentów)

- **F1 `coord-sentinel` (M) — KONSOLIDACJA = sentinel/K5-agent + allocation (R1/R2).** To NAJWIĘKSZA manifestacja M (live: 2046× V328 + 14456× COORD_GUARD, 8 ofiar 30.06), ale jej rdzeń-fix („JEDEN walidator `coords_in_bialystok_bbox` u KAŻDEGO ingest") = sentinel-agent; K5 to MOST do geometry-blind-selection (pula kurczy się → best-effort pile-on) i floor (BIALYSTOK_CENTER fiction). **TU raportuję aspekt-M (catch-all połyka fail-loud, brak operator-alertu, truthy-guard `if coords:`≠`_valid`) i dostarczam INV-FAIL jako test akceptacji fixu sentinel-agenta** — nie re-derywuję 6 manifestacji jako 6 chaosów.
- **F2 `schedule-fail-open` (M) — KONSOLIDACJA = R7 `schedule-data-3way-failopen-failclose` (I-koherencja: 3 sprzeczne traktowania jednego defektu) + floor-agent (is_on_shift = warstwa floor).** TU tylko aspekt-M (cichy `return True` bez log.warning, wzorzec FAIL12-loud niereplikowany). Polityka precedencji fail = R7.
- **R5-G1 aspekt-E (G-2 niewidoczność) = R3-E4 `serializer-allowlist-metrics-vanish`.** `r6_gold4_gate_recovered` liczony LIVE ale **0/ledger** — fix = serializer-kompletność (R3 FAZA 1). TU tylko zaznaczam zależność: G-target nie jest mierzalny póki R3-E4 nie odsłoni licznika.
- **R5-G1 aspekt-anchor = R7 `r6-cap-35-flat-vs-40-tier-plus-quantile` + E_dedup_1 ROOT5 (SLA/R6-anchor).** G-1 quantile-recovery = inwersja HARD↔SOFT + ZŁA oś — TU oś-kalibracji (G), w R7 inwersja-kanon (I). O2-review 02.07 rozstrzyga ŁĄCZNIE (bagcap + anchor + prep) — co-design, ruszać razem.
- **O3 `load_plan` read-side-effect = R6a `courier-plans-lifecycle` (lifecycle-agent, K2 plan_recheck-cofacz).** TU aspekt-O (TOCTOU read↔advance); fix-u-źródła (odwróć default `invalidate_on_mismatch`) = R6a.
- **R20 `commit-divergence-masking-and-silent-off` (M-aspekt: „bezpieczny fallback OFF" misnomer) = R7.** TU cross-ref jako manifestacja M (flaga fail-open/floor/exempt cicho odwraca decyzję na utracie klucza) — rdzeń w R7.
- **M-7 magic-score `-1e9` = mostly-mitigated (Z-18).** Sub-nota, nie root.
- **STOP na dyspozytorni** — Mailek/Papu poza zakresem. Cross-repo render sentineli (`fleet_state`/`courier_orders` BIALYSTOK_CENTER) = J-agent.

---

## 1. CROSS-CUTTING — KONTRAKT TRYBU-AWARII (produkt §4.5 + §4.8 + §4.7 + §4.1)

Stan docelowy R5 zaczyna się od JEDNEJ zasady, którą dziś łamie każda z 3 klas: **awaria musi być WIDOCZNA i BEZPIECZNA.** Dziś nie istnieje jako egzekwowany kontrakt — `A4_instrument_registry` wymienia przyrządy, ale nie ma kolumny „jak się psuje" (loud/silent), a 130 bloków `except` w `dispatch_pipeline.py` (119× `except Exception` + 1 bare) nie ma reguły fail-loud-vs-fail-soft.

**SZKIELET KONTRAKTU TRYBU-AWARII (kolumny — analogiczne do REJESTRU-PRAWDY w R3):**

| Mechanizm awarii | Dziś (tryb) | Cel (tryb) | Naruszony kontrakt |
|---|---|---|---|
| `_append_jsonl` ×6 (utrata zapisu przyrządu) | 🔴 CICHY (`_log.warning`, brak licznika) | GŁOŚNY (counter+alert na utratę) | §4.5, §4.1 (1 helper) |
| `_v328_eval_safe` catch-all (kurier znika) | 🔴 CICHY (per-courier drop, brak KOORD) | data-poison ≠ real-bug, operator-alert | §4.5, §4.8 |
| sentinel `(0,0)` produkcja (`or (0,0)`) | 🔴 CICHA (fallback bez warning) | fail-loud/SKIP u ingest, 1 walidator | §4.6, §4.1 |
| `is_on_shift` fail-open `return True` | 🔴 CICHY (24/7, 0 log) | GŁOŚNY (jak FAIL12) + 1 fail-policy | §4.8, §4.5 |
| flaga fail-open/floor/exempt na utracie klucza | 🔴 CICHO→OFF (cofa decyzję) | const-default-ON jawnie + w flags.json | §4.4, §4.8 |
| kalibracja G-1 (luzuje R6, zła oś) | 🔴 OPTYMISTYCZNA + niemierzalna | oś-realna (pesymistyczna-bezpieczna) + serializowana | §4.5, §4.8 |
| RMW bez fcntl (4 pliki) | 🔴 WYŚCIGOWY (lost-update) | 1 dyscyplina LOCK_EX | §4.1, §4.7 |
| `load_plan` read-side-effect | 🔴 WYŚCIGOWY (TOCTOU) | pure-read default | §4.7 |
| CookieJar shared opener / ThreadPool | 🔴 WYŚCIGOWY (419-kaskada) | per-wątek opener | §4.7, §4.1 |

**INWARIANTY TRYBU-AWARII (docelowa suite — czerwone-na-start, zielone-po-konsolidacji):**

- **INV-FAIL-1 (M: brak cichego połknięcia w hot-path / przyrządzie):** żaden `except` na ścieżce decyzyjnej ani w zapisie przyrządu nie kończy się samym `log.warning`/`pass` bez (a) re-raise dla PRAWDZIWEGO buga, ALBO (b) operator-widzialnego sygnału (counter/alert) dla utraty-danych. *Test:* grep `except.*:\s*\n.*\(warning\|pass\|return None\)` w hot-path + `_append_jsonl` ⇒ każdy ma counter/alert albo jawną adnotację „neutralny-fail-soft-z-powodem". Dziś: ≥8 swallow bez licznika.
- **INV-FAIL-2 (M: sentinel ≠ dana — chokepoint u ingest):** każda współrzędna przekraczająca granicę INGEST (`gps_server`, `state_machine`, panel-parse) przechodzi przez JEDEN walidator (`coords_in_bialystok_bbox`); KAŻDY caller geometrii (`haversine`/osrm) używa `_valid(coords)` nie truthy-guard `if coords:`. *Test:* grep `if .*coords\s*:` przed `haversine(` = 0; `(0,0)` nigdy nie dociera do matematyki bez fail-loud. Dziś: `:4822`/`:2147` truthy, `gps_server:328` przepuszcza `(0,0)`.
- **INV-FAIL-3 (M: jedna polityka fail dla zepsutych danych):** ten sam zły wpis (grafik, coord, flaga) ma JEDNĄ politykę (open LUB close), spójną cross-warstwa, GŁOŚNĄ (log.warning). *Test:* `is_on_shift` loguje przy fail-open jak FAIL12; flaga fail-open/floor/exempt = const-default-ON ∧ obecna w flags.json (utrata klucza ≠ cichy flip). Dziś: 3 sprzeczne traktowania grafiku (open/close/loud).
- **INV-FAIL-4 (G: kalibracja celuje w oś realnego błędu — outcome-join):** każda żywa kalibracja zmieniająca decyzję (zwłaszcza luzująca HARD) MUSI być zwalidowana joinem ground-truth (`gps_delivery_truth.jsonl` dostawa / `pickup_slip_monitor.jsonl` odbiór / `restaurant_prep_bias.json` prep) na osi, którą koryguje — ALBO oznaczona „shadow, NIE-decyzyjna". *Test:* G-1 (luzuje R6) referuje oś poślizgu/prep (gdzie błąd siedzi), nie delivery-pesymizm matched-courier. Dziś: jedyna żywa kalibracja luzuje, oś-realna OFF.
- **INV-FAIL-5 (G: kierunek bezpieczny — pesymizm na HARD):** korekta na osi błędu świeżości MUSI iść w stronę GATE-STRICTER (bag_time rośnie → R6 bije wcześniej), NIGDY bardziej liberalna (wzór z `feasibility_v2.py:~1056-1060` komentarz prep). *Test:* żadna kalibracja nie obniża `bag_time` poniżej surowego na osi, gdzie zmierzony bias jest DODATNI (optymistyczny). Dziś: G-1/G-3 obniżają.
- **INV-FAIL-6 (O: jedna dyscyplina zapisu współdzielonego):** każdy plik pisany przez ≥2 procesy używa fcntl LOCK_EX RMW (wzór `plan_manager`/`state_machine`) + UNIKALNY tmp; zero read-with-side-effect (odczyt nie persystuje); współdzielona sesja HTTP = per-wątek. *Test:* `os.replace` bez `LOCK_EX` na pliku multi-writer = 0; `load_plan` default `invalidate_on_mismatch=False`; recheck używa `panel_detail_prefetch` per-wątek. Dziś: 4 RMW no-lock + load_plan True-default + CookieJar shared.

Mapowanie inwariant→klasa: **M** → 1,2,3 · **G** → 4,5 · **O** → 6.

---

## 2. STAN DOCELOWY PER ROOT (twardy kontrakt + inwariant runtime)

### ▰ POD-RODZINA G (kalibracja na złej osi)

### R5-G1 — `calibration-on-wrong-axis` (P1, CONFIRMED, źródło, OTWARTY) — dedup3 F3

**CO DZIŚ (entropia, świeżo zweryfikowane — B09 + flags.json DZIŚ + oracle C13/C07):**
- **Oś PRAWDY (gdzie błąd FIZYCZNIE siedzi):** poślizg ODBIORU (assign→pickup) `pickup_slip_monitor.jsonl` ciasno/solo med **+27.4** (n=43), srednio **+17.7**, luzno **+6.2** — **DODATNI = silnik OPTYMISTYCZNY** (oracle C13 VALIDATED: znak dodatni POTWIERDZONY); prep-bias `restaurant_prep_bias.json` med **+11..+13**, p90 **+32-35** (n=25912, FRESH); noga JAZDY **~0 błędu** (29.06 — wcześniejsze „2× OSRM" = zła kolumna ref).
- **Żywe strojenie LUZUJE (zła/zatruta oś) — POTWIERDZONE flags.json DZIŚ:** `ENABLE_ETA_QUANTILE_R6_BAGCAP=True` (G-1 LIVE) → `feasibility_v2.py:~1088-1100` dla gold∧`len(bag)+1≤4` przelicza `bag_time` mapą kwantylową delivery-ETA z PRÓBY SELEKCYJNEJ (`eta_quantile_calib.py:~29-30` sam ostrzega „matched_courier only, unmatched mieszają szum selekcji") → `_gate_bt=p80` → worek surowo >35 przechodzi (`r6_gold4_gate_recovered:~1098`). Magnituda: `r6_breach_shadow` 1621/5000 = **32,4%** R6-rejectów `would_pass_calibrated` (dźwignia na całej 1/3; live-scope gold≤4 ogranicza blast). `ENABLE_DRIVE_SPEED_TIER_CORRECTION=False` (G-3 parked) = `DRIVE_SPEED_MULT_BY_TIER` gold 0.78 na nodze jazdy (~0 błędu) — flip = −18..22% nogi → R6 luźniejszy; werdykt-tool sam nazywa ryzyko (`drive_speed_overshoot_verdict.py:~5-7`), flip 26.06 → rollback po ~15min.
- **Oś REALNA OFF/pod-wymiar — POTWIERDZONE flags.json DZIŚ:** `ENABLE_DRIVE_MIN_CALIBRATION_V2=False` (G-4 prawdziwa oś `drive_min_calibration.py:52 OFFSET_TABLE` assign→pickup +13..+35 OFF main); `PICKUP_DEBIAS_MIN=4.5` (`common.py:~3131`) konsumowany TYLKO przez `ENABLE_PICKUP_DEBIAS_SHADOW=True` (shadow, „ZERO zmiany committed") = pod-wymiar **4-6×** vs zmierzone +18-27; `ENABLE_PREP_BIAS_TABLE=False` (G-5 prep +11..+13 NIE-skorygowany) + **dwie mapy prep** (feasibility czyta ANTYK `prep_bias_table.json` mtime 20.06 vs shadow FRESH `restaurant_prep_bias.json` 30.06 = A1 D-smell).
- **Przyrząd NIE mierzy (G-2, aspekt-E):** `r6_gold4_gate_recovered` LIVE-ustawiany ale **0 w serializerze i 0 w `shadow_decisions`** → luzowanie R6 niemierzalne (anomalia: 66 gold≤4 would_pass JESZCZE-rejected = rozjazd shadow-recompute `worst_bt` vs live per-order `bag_time_min`, wymaga oracle).

**STAN DOCELOWY (kontrakt §4.5 + §4.8 + §4.1):**
1. **§4.5 / INV-FAIL-4** Kalibrować **oś poślizgu-odbioru + prep** (load-aware bufor ETA — segment po obciążeniu floty, nie po porze; review 04.07 z `pickup_slip_monitor`/`prep_bias`), zamiast skracać jazdę/delivery. Korekta zwalidowana joinem ground-truth na osi którą koryguje.
2. **§4.5 / INV-FAIL-5** Kierunek BEZPIECZNY: korekta osi-świeżości idzie GATE-STRICTER (bag_time ROŚNIE pod realny poślizg, R6 bije wcześniej), bias-ujemny→0 (wzór prep-anchor komentarz feasibility). G-1 luzowanie i G-4/G-5 pod-korekta NIE mogą pracować przeciw sobie na tej samej decyzji.
3. **§4.8 (co-design, ruszać RAZEM)** G-1 bagcap + SLA-anchor (E_dedup_1 ROOT5 / R7) + prep-bias rozstrzygnięte ŁĄCZNIE w **O2-review 02.07** (at#168/#200). Quantile-recovery na osi poślizgu (gdzie błąd siedzi), NIE delivery-pesymizmu selekcyjnego.
4. **§4.1** JEDNA mapa prep (usunąć ANTYK `prep_bias_table.json` 20.06 — feasibility i shadow czytają to samo źródło FRESH). G-3 parked-landmine: oznaczyć „flip=systemowy optymizm na osi bez błędu" lub usunąć stałe.
5. **(zależność E) §4.6** Serializować `r6_gold4_gate_recovered` (= R3-E4 serializer-kompletność) — bez tego luzowanie niemierzalne, target nie-weryfikowalny.

**INWARIANT RUNTIME:**
> Żadna kalibracja zmieniająca decyzję (zwłaszcza luzująca HARD-R6) nie jest LIVE bez outcome-join na osi-którą-koryguje (INV-FAIL-4); żadna nie obniża `bag_time` poniżej surowego tam, gdzie zmierzony bias jest DODATNI (INV-FAIL-5). Tripwire: `r6_gold4_gate_recovered` serializowany ⇒ % luzowania mierzalny w ledgerze.

**BRAMKA „ZERO NOWYCH KOPII":** przekierować JEDNĄ żywą kalibrację (G-1) na oś-realną + włączyć JEDNĄ parked-na-właściwej-osi (prep/pickup-slip) — NIE dodawać 4. mapy. Sekwencja: **gated O2-review 02.07** (rozstrzyga łącznie); flip = protokół ETAP 0→7, replay „pozytywny wpływ" (mniej realnych breachy R6 ON↔OFF, nie tylko brak-regresji), okno 2 dni. ⚠ G-1 to ŚWIADOMA flaga (gold≤4) — NIE rwać bez ACK, przekierować oś (inwersja HARD↔SOFT do rozstrzygnięcia z Adrianem, P0 „SOFT/kalibracja nie osłabia HARD").

---

### ▰ POD-RODZINA M (cicha awaria / sentinel-jako-dana)

### R5-M1 — `instrument-append-jsonl-silent-swallow` (P2, PLAUSIBLE, manifestacja, OTWARTY) — dedup2 R9

**CO DZIŚ (entropia, świeżo zweryfikowane — grep DZIŚ ciało-po-ciele):**
- **6 kopii `_append_jsonl` w `tools/`** (b_route_shadow `:327`, bundle_calib_shadow `:514`, fleet_position_snapshot `:57`, pending_global_resweep `:95`, prep_bias_shadow_monitor `:69`, reassignment_forward_shadow `:403`) + `dispatch_pipeline.py:~6047` (mda) + `courier_resolver` last_pos swallow → **≥8 instancji, ZERO wspólnego helpera** (twin-scatter).
- **Niespójność POTWIERDZONA (czytałem ciała DZIŚ):** klasa wyjątku rozjechana — `fleet_position_snapshot`/`reassignment`/`bundle_calib` łapią `except Exception`, `pending_global_resweep:~101` łapie **tylko `except OSError`** (węższy → inny błąd propaguje INACZEJ); trwałość rozjechana — większość `flush()+os.fsync()`, `pending_global_resweep` BEZ fsync; `prep_bias_shadow_monitor:69` ma **CAŁKIEM INNĄ implementację** (read-prev + temp+concat + mkstemp, nie tryb `append` — O(n²) read-rewrite, inna semantyka crash-safety). **Bliźniaki o tej samej nazwie liczą/piszą RÓŻNIE.**
- **Wzorzec-szkody:** każdy łapie wyjątek zapisu → `_log.warning` POŁYKA → **utrata danych przyrządu NIEWIDOCZNA** (instrument „milczy" zamiast krzyczeć; żaden licznik utraty, żaden alert). To czyni KAŻDY downstream-werdykt R3 (feas-carry, bundle_calib, b_route) potencjalnie ślepym na własną dziurę w danych — `source:false` w taksonomii BO to manifestacja głębszej wady: **brak egzekwowanego kontraktu fail-loud / niespójna polityka trybu-błędu** (ten sam root co `_v328` catch-all M2 i `is_on_shift` fail-open M3).

**STAN DOCELOWY (kontrakt §4.5 + §4.1 + INV-FAIL-1):**
1. **§4.1** JEDEN współdzielony helper `append_jsonl` (np. w `core/jsonl_appender` — który JUŻ ISTNIEJE dla learning_log z fcntl, B18 §0) importowany przez wszystkie ≥8 przyrządów — `copy-count` → 1; jednolita klasa-wyjątku, jednolity fsync, jednolity tmp.
2. **§4.5 / INV-FAIL-1** Helper **fail-loud**: utrata zapisu → counter (np. `<tool>_jsonl_drop_total` w telemetrii) + próg-alert, NIE samo `warning`. Przyrząd, który gubi własne dane, jest kłamiącym przyrządem (raportuje „N decyzji" gdy zapisał N−k) — niedopuszczalne dla instrumentu bramkującego flip.
3. Granica: fail-soft pozostaje DOZWOLONY (zapis przyrządu NIE może wywrócić silnika), ale MUSI być WIDOCZNY (licznik), nie cichy.

**INWARIANT RUNTIME:** każdy zapis-przyrządu używa wspólnego `append_jsonl`; utrata zapisu inkrementuje widoczny counter (INV-FAIL-1). Test: 1 definicja helpera (`grep -c "def.*append_jsonl"` = 1 w core), reszta = importy.

**BRAMKA „ZERO NOWYCH KOPII":** scal 6+ kopii → 1 helper (−kopie), NIE dodawaj 7. swallow. Niski harm-runtime (przyrządy nie-silnik), wysoki harm-poznawczy (instrument-prawda R3 zależy od kompletności danych). Measure-first per protokół; P2 — po fundamencie R3/R1.

---

### R5-M2 — `coord-sentinel-no-ingest-chokepoint` (P1, CONFIRMED, źródło, OTWARTY) — dedup3 F1 · KONSOLIDACJA = sentinel/K5-agent

**CO DZIŚ (entropia, świeżo zweryfikowane — B15/B16 + ŻYWY log DZIŚ):**
- Sentinel `(0,0)` PRODUKOWANY w warstwie danych BEZ fail-loud: `dispatch_pipeline.py:~3133-3135` (`_repair_bag_coords(...) or ... or (0.0,0.0)`) + `:~3470` (new-order delivery `or (0.0,0.0)`). Istnieje JEDEN kompletny walidator `common.coords_in_bialystok_bbox:~513` (odrzuca None/NaN/(0,0)/poza-bbox) — **NIGDZIE u granicy INGEST** (`gps_server:~328` range-check `(0,0)` PRZECHODZI).
- **Dwa ujścia tej samej trucizny (klasa B w M):** `haversine()` RAISE (fail-loud #81 DZIAŁA) → catch-all `_v328_eval_safe:~5695` `except Exception` → **ZAJĘTY kurier znika z puli** vs `osrm.route/table:~570` → sentinel 9999min → leg infeasible → **kurier cicho wycięty**. Truthy-guard `if _last_drop:` (`:~4822` wave-veto, `:~2147` repo_cost) NIE łapie `(0,0)` (truthy).
- **ŻYWY DOWÓD DZIŚ (logi):** **2046×** `V328_CP_SOLVER_FAIL` + **14456×** `COORD_GUARD` + 3885× haversine-sentinel-raise; **8 distinct ofiar 30.06** (cid=179×5, cid=492 Jakub W×3; smoking-gun `ll1=(0,0), ll2=real_pickup` = sygnatura `:4823`). **BRAK alertu/KOORD — jedyny ślad to ERROR w logu którego nikt nie czyta.** M-4 repo_cost: `(0,0)`-raise połknięty LOKALNIE → repo_km=None → kandydat z zatrutym workiem wygląda **TAŃSZY** (optymizm wzmacnia P0-A geometria-ślepa).
- Catch-all `:5695` NIE odróżnia 3 światów: data-poison `(0,0)` / realna niefeasybilność / PRAWDZIWY bug (NameError — dokładnie incydent 03.05 „rano" V3.27.6, ukryty 60s).

**STAN DOCELOWY (kontrakt §4.6 + §4.1 + §4.5 + INV-FAIL-2):**
> **Konsolidacja-rdzeń = sentinel/K5-agent + allocation (R1/R2):** (a) wpiąć `coords_in_bialystok_bbox` u KAŻDEGO ingest (`gps_server`, `state_machine`, panel-parse) = JEDEN chokepoint; (b) truthy-guard `if coords:`→`_valid(coords)` we WSZYSTKICH bezguardowych callerach RAZEM (`:4823`+`:2149`+osrm); (c) domknąć u ŹRÓDŁA geokodu (122 zatrute adresy, `geocode-centroid guard` ON). **TU dostarczam aspekt-M jako kontrakt:**
1. **§4.5 / INV-FAIL-1** Catch-all `_v328_eval_safe` ROZRÓŻNIA: ≥1 drop z `ValueError sentinel (0,0)` → **operator-widzialny sygnał** (KOORD/alert „zatruty adres w worku kuriera X"), NIE tylko `log.warning`; PRAWDZIWY bug (NameError/KeyError) → NIE połykać per-courier (maskuje regresje).
2. **§4.6 / INV-FAIL-2** Sentinel ≠ dana: produkcja `or (0,0)` → fail-loud/SKIP (jak new-order pickup `:3450`) lub re-geokod z markerem `data_quality_issue`. Placeholder `(0,0)` NIE persystowany do `courier_plans.json` (`panel_watcher:~474`, live 11/79 stopów).

**INWARIANT RUNTIME:** żaden `(0,0)` nie dociera do `haversine`/osrm bez `_valid`-guard; produkcja sentinela = fail-loud; częściowy drop puli (1/10) = operator-widzialny (INV-FAIL-2). Test: 8-ofiar-DZIŚ → 0 cichych dropów (alert na każdy data-poison).

**BRAMKA / SEKWENCJA:** **konsolidacja = sentinel-agent (1 chokepoint, NIE 17 łatek).** R5 wnosi INV-FAIL-1/2 + wymóg operator-alertu jako test akceptacji. Bliźniaki haversine↔osrm MUSZĄ iść RAZEM (parytet obsługi `(0,0)`). Most: K5 zasila geometry-blind-selection (pula kurczy się → pile-on) + position-twins + floor.

---

### R5-M3 — `schedule-fail-open-vs-fail-close-asymmetry` (P1, CONFIRMED, źródło, OTWARTY) — dedup3 F2 · KONSOLIDACJA = R7 schedule-data-3way + floor

**CO DZIŚ (entropia, świeżo zweryfikowane — B15/B16 + dedup3 F2):**
- TE SAME zepsute dane grafiku (literówka „11.00" zamiast „11:00", pusta godzina, fetch 06:00→flota bez grafiku 00:00-06:00) → **3 sprzeczne traktowania:** `is_on_shift` fail-OPEN CICHO (`return True` „24/7", `schedule_utils.py:~376/383/392/401`, ZERO log.warning; `:401 except ValueError "11.00"→True`) ‖ `_shift_start_dt`/`_shift_end_dt` fail-CLOSE→None (floor `max(now,shift_start)` = no-op) ‖ feasibility FAIL12 fail-open-LUB-close **GŁOŚNO** (`feasibility_v2.py:~701 log.warning` „SPRAWDŹ GRAFIK").
- **Skutek literówki:** kurier liczony on-shift (brak demote/warm-up) + floor martwy NA ZAWSZE cicho + feasibility próbuje NO_ACTIVE_SHIFT — niespójna decyzja per powierzchnia. **Poprawny GŁOŚNY wzorzec ISTNIEJE w tym samym systemie (FAIL12 „Z2 anti-silent-failure"), `is_on_shift` go NIE stosuje.**

**STAN DOCELOWY (kontrakt §4.8 + §4.5 + INV-FAIL-3):**
> **Konsolidacja-rdzeń = R7 `schedule-data-3way-failopen-failclose` (JEDNA polityka fail, spójna cross-warstwa) + floor-agent (is_on_shift = warstwa #2 floor).** TU aspekt-M:
1. **§4.5 / INV-FAIL-1** `is_on_shift` z GŁOŚNYM `log.warning` (jak FAIL12) przy fail-open — koniec cichego `return True`.
2. **§4.8 / INV-FAIL-3** JEDEN kontrakt fail-policy dla zepsutego wpisu grafiku (open LUB close, nie 3 różne) — rozstrzygnięcie własności R7 (I-koherencja). Walidacja wpisów U ŹRÓDŁA (arkusz Google) — literówka „11.00" odrzucona zanim wejdzie.

**INWARIANT RUNTIME:** zepsuty wpis grafiku → JEDNA polityka, GŁOŚNA, spójna w is_on_shift/dt-helpers/feasibility (INV-FAIL-3). Test: literówka „11.00" → ten sam werdykt (i log) we wszystkich 3 warstwach.

**BRAMKA / SEKWENCJA:** konsolidacja = R7 (polityka) + floor-agent. R5 wnosi INV-FAIL-3 + „replikuj FAIL12-loud do is_on_shift". NIE liczę 17-powierzchni floora (= E_dedup_1 ROOT6).

### (cross-ref M) R20 `commit-divergence-masking-and-silent-off` — własność R7
„Bezpieczny fallback OFF" = MISNOMER dla flag fail-open/floor/exempt (`FAIL12_SCHEDULE_FAILOPEN`/`PRE_SHIFT_DEPARTURE_CLAMP`/`PACZKA_R6_THERMAL_EXEMPT`): utrata KLUCZA z flags.json → `decision_flag` spada na const → cicho COFA świadomą decyzję (fail-CLOSED / brak floor / paczki R6-reject). **Aspekt-M = cichy niespójny tryb-błędu.** Target (R7): flagi fail-open/floor/exempt = const-default-ON JAWNIE + obecne w flags.json (utrata klucza ≠ flip). INV-FAIL-3 obejmuje.

---

### ▰ POD-RODZINA O (współbieżność / wyścig)

### R5-O1 — `shared-state-no-lock-rmw` (P2, CONFIRMED, źródło, OTWARTY) — dedup3 F4

**CO DZIŚ (entropia, świeżo zweryfikowane — B18 + grep `os.replace`-bez-fcntl DZIŚ):**
- Stan współdzielony pisany przez ≥2 procesy przez `os.replace` BEZ fcntl → brak torn-read, ale **lost-update RMW NIEzabezpieczony** (A czyta `{x}`, B czyta `{x}`, A pisze `{x,a}`, B pisze `{x,b}` → `a` zgubione). Potwierdzony grep DZIŚ: `courier_resolver.py` (O3 last_pos), `live_eta_cache.py` (O4), `global_alloc_store.py` (O10) — wszystkie `os.replace` bez `LOCK_EX`. + `pending_proposals_store.py:~46/51` (O1).
- **Rozsyp mityacji ad-hoc zamiast JEDNEJ dyscypliny:** O1 `pending_proposals.json` — store-shadow + postpone-timer LIVE lost-update; store+telegram współdzielą **STAŁY** `{path}.tmp` → **re-enable Telegrama = uzbrojenie tmp-clobber BEZ zmiany kodu** (C2-mina, protokół Załącznik B). O3 `courier_last_pos.json` — docstring KŁAMIE **„multi-proces safe"** (`courier_resolver:~172`, klasa E lying-comment), merge-by-ts ZAWĘŻA nie eliminuje; zasila no_gps rescue. O4 `live_eta_cache.json` (shadow+plan_recheck). O10 `global_alloc.json` (STAŁY tmp + feed fail-soft `{}` = M silent-vanish overlay).
- **Dyscyplina poprawna ISTNIEJE w tym samym silniku** (B18 §4): `plan_manager`/`state_machine`/`pending_pool`/`gps_server`/`event_bus` = fcntl LOCK_EX + atomic replace. Rodzina-B świadomie z niej zrezygnowała.

**STAN DOCELOWY (kontrakt §4.1 + §4.7 + INV-FAIL-6):**
1. **§4.1** JEDNA dyscyplina — fcntl LOCK_EX wrapper RMW (jak `plan_manager`/`state_machine` MAJĄ) zamiast 4 ad-hoc mityacji (merge-by-ts / latest-wins / single-writer / „Telegram muted"). UNIKALNY `mkstemp` tmp wszędzie (koniec STAŁEGO `{path}.tmp` w O1/O10).
2. **§4.5** Naprawić lying-docstring O3 („multi-proces safe" → faktyczny stan, albo dodać lock by stało się prawdą).
3. **C2-świadomość:** re-enable Telegrama (kiedyś) = uzbrojenie O1 tmp-clobber → fix MUSI poprzedzić re-enable (sprzężenie protokół C2/Załącznik B).

**INWARIANT RUNTIME:** każdy plik multi-writer = fcntl LOCK_EX RMW + unikalny tmp; zero docstringów twierdzących bezpieczeństwo bez locka (INV-FAIL-6). Test: `os.replace` bez `LOCK_EX` na pliku z ≥2 pisarzami = 0.

**BRAMKA „ZERO NOWYCH KOPII":** 1 wrapper-dyscyplina importowany przez 4 pliki (−4 mityacje ad-hoc), NIE 5. mityacja. P2 (self-healing zawęża zmierzoną częstość — następny tick re-zapisuje); strukturalnie otwarte. Faza C oracle: repro 2-proces, zmierz CZĘSTOŚĆ lost-update (dziś niezmierzona).

---

### R5-O2 — `cookiejar-threadpool-shared-session` (P2, CONFIRMED, źródło, OTWARTY) — dedup3 F5

**CO DZIŚ (entropia, świeżo zweryfikowane — B18 O5):**
- Współdzielony `opener`+CookieJar czytany w `panel_client._open_with_relogin:~472` i `opener.open()` BEZ `_session_lock` (lock tylko w `login():~278`), wołany z `ThreadPoolExecutor` (`dispatch_pipeline.py:~427-433` pre-proposal recheck, N wątków na WSPÓLNYM openerze). Przy 419/401 → `login(force=True)` podmienia opener w locie pod lockiem, ale inne wątki trzymają STARĄ referencję → wyścig cookies + kaskada 419.
- **ŁAMIE WŁASNĄ regułę NIGDY** (CLAUDE.md: „edit-zamowienie sekwencyjnie, nie ThreadPoolExecutor (CookieJar thread-safety)" + „urllib CookieJar nie thread-safe"). **Bezpieczny fix ISTNIEJE niepodpięty:** `panel_detail_prefetch.py:~53` = per-wątek opener+CookieJar (kontra-przykład). Asymetria: prefetch=safe, recheck=unsafe. Tolerowane od IV (1-retry 419 per-wątek self-healing).

**STAN DOCELOWY (kontrakt §4.7 + §4.1 + INV-FAIL-6):**
> Przepiąć recheck (`dispatch_pipeline:427`) na ISTNIEJĄCY bezpieczny wzorzec `panel_detail_prefetch` (per-wątek opener+CookieJar) LUB `_session_lock` na `.open()`. Koniec łamania reguły NIGDY. `copy-count` wzorca-sesji → 1 (jeden bezpieczny, reużyty).

**INWARIANT RUNTIME:** żadna współdzielona sesja HTTP pod ThreadPoolExecutor; recheck używa per-wątek openera (INV-FAIL-6). Test: grep `executor.submit` na ścieżce do `_open_with_relogin` z shared opener = 0.

**BRAMKA „ZERO NOWYCH KOPII":** reużyj istniejący `panel_detail_prefetch`-wzorzec (−1 unsafe-ścieżka), NIE 2. implementacja sesji. P2 (self-healing 419-retry). Faza C: repro ThreadPool+419 (czy kaskada).

### (cross-ref O) `load_plan` read-with-side-effect (O2 z B18) — własność R6a lifecycle
`plan_manager.load_plan:~121-160` domyślnie `invalidate_on_mismatch=True` → odczyt PERSYSTUJE `invalidate_plan(ORDER_DELIVERED_ALL)`; TOCTOU z `advance_plan` → spurious DRZE plan → oscylacja carried-first (Jakub W/Piotr K 29.06). Mitygowane `ENABLE_LOAD_PLAN_PURE_READ` przy 2 callerach, **default param wciąż True** (nowy caller = re-uzbrojenie). **Aspekt-O = read-side-effect.** Target (R6a): odwrócić default na pure-read (opt-IN side-effect, fix U ŹRÓDŁA nie per-caller) — §4.7 INV-FAIL-6 (zero read-with-side-effect). TU cross-ref; konsolidacja w lifecycle-agent.

---

## 3. PLAN KONSOLIDACJI (zależnościowo; każdy krok REDUKUJE ≥1 metrykę entropii; bramka „ZERO NOWYCH KOPII")

**Zasada anty-entropii:** konsoliduj-nie-dodawaj; każdy krok ściśle redukuje silent-swallow / sentinel-no-chokepoint / fail-open-silent / wrong-axis-live / no-lock-RMW. **Lekcja przewodnia R5 = „awaria GŁOŚNA i BEZPIECZNA"** (Lekcja #32 silent-except = invisible-bug; #81 fail-loud sentinel cross-codebase). Wszystko dotykające kodu = OSOBNY ACK + ETAP 0→7, off-peak, replay ON↔OFF, parytet bliźniaków (haversine↔osrm; 6 swallow razem), pełna regresja `pytest tests/` vs baseline.

> **Kolejność wymuszona naturą R5:** najpierw **M-fail-loud + sentinel-chokepoint** (klasa z LIVE-szkodą DZIŚ: 8 ofiar/dzień, kurierzy znikają z puli — najwyższy realny harm), bo bezpieczeństwo > obserwowalność > współbieżność > kalibracja-gated. G na końcu (gated O2 02.07 + load-aware review 04.07). O w środku (P2, self-healing, ale C2-mina przed re-enable Telegrama).

### FAZA 0 — FUNDAMENT fail-loud (wspólny z M-konsolidacją sentinel-agenta)
- **S0.1 (sentinel-agent, M2)** JEDEN chokepoint `coords_in_bialystok_bbox` u KAŻDEGO ingest (`gps_server`/`state_machine`/panel-parse) + truthy→`_valid` we WSZYSTKICH callerach geometrii RAZEM (`:4823`/`:2149`/osrm) + domknięcie u źródła geokodu. *Redukuje: sentinel-no-chokepoint, 8-ofiar/dzień→0.* Akceptacja = INV-FAIL-2. **Konsolidacja własności sentinel-agenta; R5 wnosi INV-FAIL-1/2 + operator-alert.**
- **S0.2 (M2)** Catch-all `_v328_eval_safe` rozróżnia data-poison/real-bug/infeasible + operator-alert na data-poison. *Redukuje: catch-all-undifferentiated→0; cichy-drop→widoczny.* Bramka: NIE połykać real-bug.
- **S0.3 (M3 + R7)** `is_on_shift` fail-loud (log.warning jak FAIL12) + JEDNA fail-policy grafiku (własność R7). *Redukuje: fail-open-silent→0.* Akceptacja = INV-FAIL-3.

### FAZA 1 — M-swallow konsolidacja (R5-M1) — przyrządy przestają cicho gubić dane
- **S1.1** 6+ kopii `_append_jsonl` → 1 wspólny helper fail-loud (counter+alert na utratę), jednolita klasa-wyjątku/fsync/tmp. *Redukuje: silent-swallow ≥8→1; copy-count→1.* Bramka: NIE 7. swallow. (Wzmacnia prawdę-przyrządów R3 — kompletność danych downstream-werdyktów.)

### FAZA 2 — O-dyscyplina (R5-O1/O2) — wyścigi zserializowane (PRZED re-enable Telegrama)
- **S2.1 (O1)** 4 RMW no-lock → 1 fcntl LOCK_EX wrapper (jak plan_manager) + unikalny tmp; napraw lying-docstring O3. *Redukuje: no-lock-RMW 4→0; lying-comment→0.* Bramka C2: PRZED re-enable Telegrama (uzbraja tmp-clobber).
- **S2.2 (O2)** Recheck → per-wątek opener (`panel_detail_prefetch`-wzorzec). *Redukuje: thread-shared-session→0; reguła-NIGDY-złamana→przywrócona.*
- **S2.3 (O3, własność R6a)** `load_plan` default→pure-read (fix u źródła). *Redukuje: read-side-effect→0.* Sekwencja: lifecycle-agent.

### FAZA 3 — G-kalibracja (R5-G1) — gated zewnętrznymi checkpointami
- **S3.1** (zależność R3-E4) Serializować `r6_gold4_gate_recovered` → luzowanie R6 mierzalne. *Redukuje: calibration-invisible→0.* Bramka: R3 FAZA 1.
- **S3.2** O2-review 02.07: G-1 bagcap + SLA-anchor + prep-bias ŁĄCZNIE; przekierować na oś poślizgu/prep (GATE-STRICTER). *Redukuje: wrong-axis-live→0; real-axis-parked→live.* Bramka: at#168/#200 02.07 + load-aware ETA review 04.07; flip = replay „pozytywny wpływ" (mniej realnych breachy) + ACK (inwersja HARD↔SOFT).
- **S3.3** JEDNA mapa prep (usuń ANTYK 20.06); G-3 parked oznaczyć/usunąć. *Redukuje: dwie-mapy-prep→1; parked-landmine→jawny.*

### Sekwencja zależności (skrót)
```
FAZA 0 (sentinel-chokepoint + catch-all-rozróżnia + is_on_shift-loud)
   [LIVE-harm DZIŚ: kurierzy znikają z puli — najwyższy priorytet]
        ├──> FAZA 1 (1 fail-loud append_jsonl)  [wzmacnia prawdę-przyrządów R3]
        ├──> FAZA 2 (1 fcntl dyscyplina + per-wątek opener)  [PRZED re-enable Telegrama: C2-mina]
        └──> FAZA 3 (G gated O2 02.07 + serializacja R3-E4 + load-aware 04.07)
```
FAZA 0 PIERWSZA (jedyna z LIVE-szkodą dziś). FAZA 1 tania (przyrządy nie-silnik). FAZA 2 przed re-enable Telegrama. FAZA 3 bramkowana zewnętrznie + ACK (świadoma inwersja).

---

## 4. DASHBOARD ENTROPII — rodzina R5 (DZIŚ → CEL)

| Metryka | Root | DZIŚ (zmierzone) | CEL | Krok |
|---|---|---|---|---|
| silent-swallow (przyrząd gubi dane bez licznika) | M1 | **≥8** `_append_jsonl` (6 tools + mda + last_pos), niespójne (Exception/OSError, fsync/nie, temp-rewrite) | **1** helper fail-loud (counter) | S1.1 |
| sentinel-no-ingest-chokepoint | M2 | 1 walidator istnieje, **0 u ingest**; `gps_server:328` przepuszcza (0,0) | **1** chokepoint @ każdy ingest | S0.1 |
| truthy-guard `if coords:` przed haversine | M2 | ≥2 (`:4822` wave-veto, `:2147` repo_cost) | **0** (`_valid` wszędzie) | S0.1 |
| cichy drop kuriera z puli (data-poison) | M2 | **2046** V328 + 14456 COORD_GUARD; **8 ofiar 30.06**, BRAK alertu | **0** cichych (operator-alert) | S0.2 |
| catch-all nie-rozróżnia poison/bug | M2 | `_v328_eval_safe:5695` (poison=bug=infeasible) | rozróżnia + nie-połyka-bug | S0.2 |
| fail-open silent (grafik) | M3 | `is_on_shift` 4× `return True`, 0 log | **0** (loud jak FAIL12) | S0.3 |
| 3 sprzeczne traktowania grafiku | M3/R7 | open/close/loud na jednym defekcie | **1** polityka (R7) | S0.3 |
| flaga fail-open/floor/exempt cicho→OFF | R20/R7 | utrata klucza cofa decyzję | const-ON+w-flags.json | R7 |
| kalibracja LIVE na złej/zatrutej osi | G1 | G-1 `ETA_QUANTILE_R6_BAGCAP=True` luzuje R6 (32,4% would_pass) | oś-realna (pesymistyczna) | S3.2 |
| oś realnego błędu OFF/shadow/pod-wymiar | G1 | pickup-slip +18-27 vs DEBIAS 4.5 shadow; prep +11-13 OFF; drive_min OFF | live na osi-realnej | S3.2 |
| kalibracja-luzowanie niemierzalna | G1/R3-E4 | `r6_gold4_gate_recovered` 0/ledger | serializowana | S3.1 |
| dwie mapy prep (A1 D-smell) | G1 | feasibility ANTYK 20.06 vs shadow FRESH | **1** mapa | S3.3 |
| no-lock RMW (lost-update) | O1 | **4** (pending/last_pos/live_eta/global_alloc) | **0** (1 fcntl dyscyplina) | S2.1 |
| stały `{path}.tmp` (clobber-mina) | O1 | 2 (pending O1 + global_alloc O10) | **0** (unikalny mkstemp) | S2.1 |
| lying-docstring „multi-proces safe" | O1 | 1 (`courier_resolver:172`) | **0** (prawda lub lock) | S2.1 |
| thread-shared session (łamie NIGDY) | O2 | recheck shared opener/ThreadPool | **0** (per-wątek) | S2.2 |
| read-with-side-effect | O3/R6a | `load_plan` default True | **0** (pure-read default) | S2.3 |

**Reguła zdrowia (samo-zachowawcza, rozszerzenie Przykazania #0):** żaden przyszły sprint nie pogarsza żadnej liczby. Anty-wzorce R5 = RED: „nowy `except` w hot-path/przyrządzie bez fail-loud-lub-licznika = RED · nowy caller geometrii z truthy-guard `if coords:` = RED · nowa flaga fail-open/floor bez const-default-ON+flags.json = RED · nowa kalibracja luzująca HARD bez outcome-join-na-osi = RED · nowy plik multi-writer bez fcntl LOCK_EX = RED · nowy read-with-side-effect = RED".

---

## 5. CROSS-REF / GRANICE / OTWARTE PYTANIA DO ADRIANA

**Sprzężenia z innymi rodzinami (rusza RAZEM lub gateuje):**
- **R5-M2 (coord-sentinel) = sentinel/K5-agent + allocation (R1 geometry-blind / R2 no-global-deconflict).** Most: K5 kurczy pulę → pile-on geometrii-ślepej (P0-B) + selekcja optymistyczna bo repo-km=None (P0-A). Fix-chokepoint = sentinel-agent; R5 wnosi fail-loud-aspekt.
- **R5-M3 (schedule-fail-open) = R7 (3-way fail-policy) + floor-agent (E_dedup_1 ROOT6, is_on_shift = warstwa floor #2).** NIE re-derywuję 17-powierzchni.
- **R5-G1 (calibration) ↔ R3-E4 (serializer) + R7 (R6-cap/anchor 35/40 + quantile-recovery) + E_dedup_1 ROOT5 (SLA-anchor).** G-2 niewidoczność = R3; inwersja HARD↔SOFT + anchor = R7; oś-kalibracji = TU. **O2-review 02.07 = wspólny węzeł** (bagcap + anchor + prep ŁĄCZNIE).
- **R5-O3 (load_plan) = R6a lifecycle (K2 plan_recheck-cofacz).** R20 silent-OFF = R7. Te są aspektami-O/M w innych rootach.
- **Wszystkie M-rooty ↔ K5 sentinel-produkcja** — fail-loud (R5) + sanityzacja sentineli u ingestu (sentinel-agent) wzmacniają się (sentinel ≠ dana w SUM/median/selekcji).

**OTWARTE PYTANIA (priorytet/inwersje — PYTAJ, nie zgaduj):**
1. **R5-G1 inwersja HARD↔SOFT:** G-1 (`ETA_QUANTILE_R6_BAGCAP`) to ŚWIADOMA flaga luzująca HARD-R6 (gold≤4). Kanon C5 „40=TYLKO ALARM, normalnie 35". Rekomendacja-DRAFT: NIE rwać — **przekierować oś** (kalibruj poślizg/prep = GATE-STRICTER, bezpieczne) w O2 02.07; ale to ZMIANA HARD-zachowania → wymaga ACK Adriana (P0 „SOFT/kalibracja nie osłabia HARD"). Czy G-1 zostaje do czasu O2, czy zamrozić wcześniej?
2. **R5-M2 operator-alert granularność:** data-poison `(0,0)` → KOORD per-order czy zbiorczy alert „N zatrutych adresów w peaku"? (8/dzień dziś — per-order może spamić). Rekomendacja-DRAFT: zbiorczy + domknięcie u źródła geokodu (122 adresy) jako priorytet (kasuje korpus, nie objaw).
3. **R5-O1 zakres dyscypliny:** wszystkie 4 RMW na fcntl LOCK_EX, czy niektóre (live_eta_cache O4 = display, self-healing) zostają na os.replace z jawnym powodem? Rekomendacja-DRAFT: pending_proposals (O1, C2-mina) + last_pos (O3, lying-doc + zasila rescue) PRIORYTET; live_eta/global_alloc niższy (display/overlay self-healing).
4. **R5-M1 vs R3 prawda-przyrządów:** czy fail-loud `_append_jsonl` (counter) to część FAZY 0 R3 (harness-prawdy) czy osobny krok R5? Rekomendacja-DRAFT: wspólny — 1 helper zasila kompletność-danych obu rodzin.
5. **C2 sekwencja:** czy re-enable Telegrama (kiedyś planowany) MUSI czekać na S2.1 (fcntl O1)? Rekomendacja-DRAFT: TAK — re-enable bez fixu = uzbrojenie tmp-clobber (protokół C2/Załącznik B); twardy gate.

---

## 6. POKRYCIE / CO NIE ROZSTRZYGNIĘTE (jawnie, nie cisza)

- **Częstość lost-update (O1/O3/O4) NIEZMIERZONA** — severity oparta na strukturze + self-healing, nie zmierzonej częstości. Faza C oracle: repro 2-proces współbieżnie → policz zgubione zapisy (B18 §6). Read-only audyt tego nie da.
- **G-2 anomalia 66-rekordów** (gold≤4 would_pass JESZCZE-rejected) = PLAUSIBLE, wymaga Fazy C: czy live-gate realnie odpala vs shadow liczy `worst_bt` zamiast per-order `bag_time_min` (B09 §luki). Wartość „pozytywnego wpływu" przekierowanej kalibracji NIE udowodniona w tym dok. (replay = osobny mini-sprint).
- **prep_bias_anchor znak/kierunek** — zaufanie do komentarza `feasibility_v2.py:~1056-1060` (gate-stricter), NIE prześledzony linia-po-linii pod znak (B09 luka). Przed flipem G-5: zweryfikować że +shift faktycznie ROŚNIE bag_time.
- **119× `except Exception` w dispatch_pipeline + 37× w plan_recheck** — zinspektowane TYLKO hot-path eval (V328) + sentinel-sites; reszta (~110+37) NIE 1:1 (większość udokumentowany fail-soft, ale pełny audyt fail-loud = osobny przebieg). plan_recheck except wokół `_apply_canon_order_invariants` szczególnie (cichy drift kanonu — B15 luka #1).
- **Cross-repo M-sentinele** (`fleet_state`/`courier_orders` BIALYSTOK_CENTER/(0,0) render) + parcel-lane = J-agent/poza SEL+ROUTE+FEAS (granica „STOP na dyspozytorni").
- **Numery linii dryfują** (≥3 sesje/dzień/repo) — zweryfikowane DZIŚ HEAD `8024705` (flags.json efektywny: QUANTILE_R6_BAGCAP=True/PREP_BIAS_TABLE=False/DRIVE_SPEED_TIER=False/DRIVE_MIN_V2=False/PICKUP_DEBIAS_SHADOW=True; `_append_jsonl` ×6 tools z ciałami; `os.replace`-bez-fcntl courier_resolver/live_eta/global_alloc; V328 2046/COORD_GUARD 14456/8-ofiar z B15 live-log), ale PoC/zmiana MUSI re-grepować (Przykazanie #0 ETAP 0). Sites G/M/O z B09/B15/B18 — re-grep `:linia` przed dotknięciem.
- **PoC = osobny ACK** — ten dokument NIE wybiera/nie pisze PoC. Kandydaci R5 wg dźwigni×ryzyka×LIVE-harm: **(a) S0.1 sentinel-chokepoint** — NAJWYŻSZY realny harm (8 kurierów/dzień znika z puli DZIŚ), ale konsolidacja = sentinel-agent (współwłasność); **(b) S1.1 `_append_jsonl` fail-loud** — niskie ryzyko (przyrządy nie-silnik), czysty „1 helper", wzmacnia R3; **(c) S2.1 fcntl O1** — gate przed re-enable Telegrama. Rekomendacja-DRAFT PoC dla R5 (jeśli R5 dostanie PoC): **S1.1 append_jsonl-fail-loud** (najczystszy „N→1 + fail-loud", zero ryzyka decyzyjnego, dowód „ON≠OFF" = drop-counter pojawia się na wymuszonym I/O-failu) LUB współudział w sentinel-agent S0.1 (najwyższy harm, ale cross-agent).
