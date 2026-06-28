# SPEC — czasówka-w-uwagach → live (parser deklarowanego deadline'u DOSTAWY z `uwagi`)

**Autor:** sesja tmux 20 (read-only prep) · **Data:** 2026-06-28
**Klasa zadania (per `ZIOMEK_PLAN_NAPRAW_I_PRIORYTETY_2026-06-28.md`):** „B. ślepota" — Ziomek nie widzi twardego deadline'u dostawy wpisanego w free-text `uwagi`.
**Status:** **STAGE 1a + ORACLE ZBUDOWANE I PRZETESTOWANE (sesja 20, 2026-06-28, po „audyt skończył, buduj").** DEPLOY (commit+restart+flip) = **STOP na ACK**. Decyzja HARD-vs-SOFT + wpięcie w 3 bliźniaki SLA + serializer (Stage 4) = osobny etap po ACK (poniżej „CO DALEJ").

---

## ✅ BUILD STATUS — 2026-06-28 (sesja 20, po zakończeniu audytu wf_29bb4804)

**Zbudowane (additywne, flag `ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW` OFF-default, observability-only, ZERO wpływu na decyzje):**
1. `czasowka_uwagi.py` (NOWY) — `parse_delivery_deadline()` (jedno źródło, regex-parytet z `bundle_calib_shadow` — test pilnuje).
2. `panel_client.normalize_order` — populacja `delivery_deadline_uwagi` (kotwica = data `pickup_at`), za flagą; OFF → klucz nie powstaje (bajt-identyczny ingest); order_type/czas_kuriera NIETKNIĘTE.
3. `state_machine.upsert_order` (NEW_ORDER) — persist pola obok `uwagi`.
4. `common.py` — flaga w `ETAP4_DECISION_FLAGS` + stała-fallback `= False`; `flags.json` += klucz `false` (atomic); `ZIOMEK_LOGIC_REFERENCE.md` udokumentowana (drift-test bez nowego wpisu).
5. `tools/czasowka_uwagi_oracle.py` (NOWY) — offline oracle (real `delivered_at` vs deadline).
6. `tests/test_czasowka_uwagi_deadline.py` (NOWY) — **11/11 PASS** (parser, parytet z shadow, ON≠OFF, additywność, persist E2E).

**Dowody (ETAP 4/5):**
- Regresja CAŁEGO Ziomka: **3465 passed, 10 failed** — te same 10 obcych pre-existing (8× courier_reliability FLT-04 + flag_doc_coverage 3 obce flagi + working_override time-flaky), **ZERO nowych failów z mojego kodu**. (baseline pre-build: 3446P/10F).
- ON≠OFF: OFF→brak klucza; ON→`delivery_deadline_uwagi`=17:10 (484034); order_type=`elastic`, czas_kuriera=`17:05` nietknięte.
- **Oracle (materiality, korpus 4114 zleceń) — wersja Stage-1 (wąski regex):** 48 deadline'ów + 6 recall-miss; raportowała „elastic mediana +13,2 min late". ⚠ **TA LICZBA BYŁA ARTEFAKTEM** — zawyżona przez (a) mis-parse czasu z przecinkiem/średnikiem („12,30"→„na 12"→12:00) i (b) niewykluczone deadline<pickup. **Lekcja measure-first: pierwszy odczyt potrafi kłamać; oracle ujawnił bug parsera.**

**Oracle Stage-2 (po broadening + separator `,;` + sanity-gate, KORYGUJE):**
  - recall-miss **6→0**; effective deadline'y **51** (~1,2% zleceń); deadline<pickup suspekty **3** (dropniete sanity).
  - **elastic-z-deadlinem (n=9): mediana −2,6 min (≈on-time), ale 33% late>3min, p90 +18,5 min.**
  - czasówka-z-deadlinem (n=42): mediana +2,0, 43% late>3min, p90 +17,4.
  - **Uczciwy wniosek:** zjawisko REALNE i powtarzalne (51 przyp.), **~1/3 mija deadline o >3 min, ogon p90 ~17–18 min** (case'y: 484034 +11, 477137 +18, 477492 +18, 476563 +15) — ale mediana ≈on-time i **n małe** → materialność dla flipu decyzyjnego NIE udowodniona; **potrzeba okna danych** zanim Stage 4. ⚠ `delivered_at`=klik ±3min (audyt #5) → `on_time_le_deadline_plus3`.

**STOP — ACK przed:** `git commit` (jawne pliki) + restart `dispatch-panel-watcher` (załadowanie kodu; OFF→bajt-identyczny) + ew. flip flagi ON (start populacji shadow). To niedzielny wieczór ~23:30 PL = **po peaku**, ale deploy/restart = brama ACK (Przykazanie #0 ETAP 6).

## CO DALEJ (po ACK — osobne etapy, NIE w tej sesji)
- **Stage 2/3 refinement (przed decyzją):** (a) poszerzyć regex empirycznie — recall-gap 6/48 (kolejność „na HH:MM … czasówka", „czasowe na", „czasowkaaaa"); (b) sanity-gate `deadline ≥ pickup` w konsumencie (12,5% suspektów).
- **Stage 4 (decyzyjne, za ACK + dane):** wpięcie w 3 bliźniaki SLA RAZEM (`_count_sla_violations` + `feasibility_v2` SLA-loop + `plan_recheck._o2_key`) + serializer LOCATION A+B (wzorzec #16 — jawnie, nie prefiks) + decyzja HARD-vs-SOFT (rekomendacja: SOFT/scoring najpierw, tier-aware 35/40, always-propose, SOFT-nie-osłabia-HARD, R27 nietykalny). Dowód flip ON↔OFF (replay) PRZED LIVE.

---

> ⚠ Poniższa mapa „TOUCH (post-audit)" dla 3 bliźniaków/serializera = Stage 4 (po ACK). Stage 1a (ingest+persist) zrobiony powyżej.

---

## 0. ETAP 0 — STAN NA ŻYWO (zweryfikowany 2026-06-28, sesja 20)

- **git HEAD:** `109e62e` (reassign-gate reserve-aware — lane sesji 17/19, NIE mój). Working-tree ma cudze niezacommitowane zmiany (`CLAUDE.md`, `daily_accounting/kurier_full_names.json`, kilka `eod_drafts/*`) — **nie moje, nie ruszam.**
- **Baseline `pytest tests/` (kanoniczna ścieżka, venv dispatch):** `3446 passed, 10 failed, 26 skipped, 6 xfailed` w 104.8s (exit 1).
  - **10 failów = obce/pre-existing, ZERO związku z czasówką-z-uwag** (nie wprowadziłem żadnej zmiany):
    - 8× `tests/test_courier_reliability.py` — lane FLT-04 ranking kurierów (inna sesja).
    - 1× `tests/test_flag_doc_coverage.py::test_no_new_undocumented_decision_flag` — lane F3 (rejestr ~24 flag, inna sesja).
    - 1× `tests/test_working_override_2026_06_01.py::test_13_real_shift_wins_over_working` — środowiskowy time-flaky (frozen-fixture + żywy `now`, znany z v3273).
  - **Konsekwencja dla BUILD-u:** baseline NIE jest dziś w 100% zielony. Sesja budująca MUSI najpierw ustalić czysty baseline (albo policzyć deltę vs te 10 znanych obcych failów), inaczej nie odróżni własnej regresji. Zapis dokładny: `3446P/10F/26S/6xf`.
- **Flagi EFEKTYWNE per proces (wzorzec #9):** dla czasówki-z-uwag będzie potrzebna NOWA flaga (jeszcze nie istnieje). Procesy w grze: `dispatch-shadow` (feasibility+scoring+selekcja+serializer, flagi z `flags.json`), `dispatch-plan-recheck` (`_o2_key`, drop-iny `*.service.d`), `dispatch-panel-watcher` (ingest/recanon), `dispatch-czasowka` (KOORD T-60/50/40). Build MUSI czytać `FLAG_FINGERPRINT` z logu właściwego procesu, nie env-default.
- **at-joby / shadow (atq + list-timers, reconcile vs `shadow-jobs-registry.md`):** 168 (Jul2 bundle-calib O2), 189 (Jun30 b2 address), 192 (dziś 21:00 feas-carry postflip), 193 (Jul1 reassign quality). `188` (bug4 reseq) już odpalony/poza kolejką. **Żaden NIE jest w lane czasówka-z-uwag.** `dispatch-bundle-calib-shadow` + `dispatch-bundle-calib-review` (168) = **POD AUDYTEM `wf_29bb4804` (O2) — NIE DOTYKAĆ.**
- **Multi-sesja recon (C1):** aktywne sesje `claude`: 15 (audyt techniczny), 17 (bug przydzielania tras), 18 (analiza trasy Bartka — autor mojego handoffu), 19 (przerzut Kuba↔Grzegorz), 20 (ja). **Mój lane (panel_client/panel_watcher/feasibility-SLA/plan czasówka) NIE pokrywa się z reassignment (17/19) ani z bundle_calib/O2 (audyt).** Bez kolizji.

---

## 1. PROBLEM + DOWÓD (ETAP 1 — źródło nie objaw)

**Dowód (sesja 18, zlecenie 484034 Sikorskiego):**
- `uwagi = "Dania \r\nPiętro 1\r\nCzasówka na 17:10"` → twardy deadline DOSTAWY **17:10**.
- `order_type = "elastic"`, `prep_minutes = 30`, `uwagi_pickup_parsed = null`, `proposed_delivery_time = null`, `expected_delivery_by = 17:34` (= odbiór 16:59 + 35 generyczny R6 — **NIE 17:10**).
- Silnik klasyfikuje czasówkę **wyłącznie po minutach przygotowania** — `panel_client.py:678`:
  `order_type = "czasowka" if prep_minutes >= CZASOWKA_THRESHOLD_MIN else "elastic"` (`CZASOWKA_THRESHOLD_MIN = 60`, l.52).
  Deklarowany w `uwagi` deadline DOSTAWY nie wchodzi do feasibility / scoringu / planu / KOORD-czasówki.

**Źródło (warstwa-przyczyna), potwierdzone grepem:**
1. **Ingest:** `panel_client.normalize_order` produkuje `uwagi` (l.741) i `order_type` (l.678), ale **nie parsuje** deadline'u dostawy z `uwagi`. To jest właściwy seam u źródła (tu już jest `uwagi` + kotwica daty z `pickup_at`, analogicznie do `czas_kuriera` l.663-671).
2. **Parser frazy ISTNIEJE, ale TYLKO w shadow** — potwierdzone: symbole `_DEADLINE_RE` / `_parse_deadline` żyją **wyłącznie** w `tools/bundle_calib_shadow.py` (l.91-115) + jego test `tests/test_bundle_calib_shadow.py`. (Trafienie w `telegram/templates.py` to fraza display „Czasówka T-{n}" istniejących szablonów KOORD prep≥60, **nie** parser.)
   - Regex: `czas[oó]wk[a-zą]*\s*(?:na\s*)?(\d{1,2})(?:[:.](\d{2}))?` (IGNORECASE). Łapie „Czasówka na 17:10", „czasowka 14", „CZASOWKA NA 16.30". Godz. 0-23, min opcjonalne. Kotwiczy do `day_warsaw` → zwraca aware UTC.
   - ⚠ `tools/bundle_calib_shadow.py` = **POD AUDYTEM `wf_29bb4804` (O2)** → parsera **NIE wycinam z niego ani nie modyfikuję go**. Build = wyniesienie logiki do współdzielonego/żywego modułu (additywnie), shadow zostaje.
3. **Klasa „czasówka governs pickup, NOT delivery"** — `feasibility_v2.py:1016` (komentarz w kodzie): *„Czasówka-paczka też bypass R6 (paczka), czasówka rządzi tylko czasem pickupu nie delivery."* → deadline DOSTAWY z `uwagi` to **NOWA klasa ograniczenia** (delivery), nie pokrywa jej istniejąca czasówka (pickup, prep≥60).
4. **Żywy gap KOORD:** `czasowka_scheduler._is_czasowka` (l.127-129) = *„Czasówka = prep_minutes >= 60 AND held by Koordynator (id_kurier=26)."* → zlecenie `elastic` z deadlinem w `uwagi` (jak 484034) **nie jest** czasówką dla schedulera → **brak osłony T-60/T-50/T-40**.

**Wniosek:** `uwagi` (surowy tekst) dociera end-to-end (panel_client → state_machine persist → konsumenci). Brakuje WYŁĄCZNIE **ekstrakcji deadline'u** i **wpięcia go jako zmiennej decyzyjnej** w SLA/feasibility/plan/KOORD. Fix u źródła = ingest (panel_client) + state persist + 3 bliźniaki SLA, **additywnie** (nowe pole, NIE nadpisywać `order_type`/`czas_kuriera`).

---

## 2. ETAP 2 — HARD vs SOFT (do rozstrzygnięcia oracle z audytu)

- **OTWARTA DECYZJA (NIE zgaduję — czeka na oracle `wf_29bb4804` + ACK Adriana):** czy złamanie deadline'u z `uwagi` ma być:
  - (A) **HARD-reject** (jak SLA) z obowiązkowym fallbackiem `_best_effort_fastest_pickup_key` (always-propose — Ziomek NIGDY „BRAK KANDYDATÓW"), albo
  - (B) **SOFT-penalty** w scoringu (kara rosnąca z ryzykiem spóźnienia), albo
  - (C) najpierw **SHADOW/observability** (zmierz częstość i materialność na realnym ruchu) → potem decyzja A/B na danych.
- **Domyślna rekomendacja sesji 20 (do akceptacji):** **C → potem B/A**. Powód: ryzyko „geometryczne optimum łamie czasówkę" z case'u Bartka (handoff) — twardy gate bez skalibrowanego oracle może zacząć masowo odrzucać/eskalować. Najpierw shadow (flaga `_SHADOW`, metryka serializowana), dowód materialności (≥20%), potem flip.
- **SOFT nie osłabia HARD (P0):** deadline z `uwagi` jako SOFT/scoring **nie może** rozluźniać istniejących HARD (R6 tier-aware 35/40, SLA 35, committed R27 ±5). Jest **dodatkowym** zacieśnieniem, nigdy poluzowaniem.
- **Reguły zakodowane do uszanowania (z `ziomek-change-protocol.md`):**
  - **R6 TIER-AWARE** — 35 (T1/2) / 40 (T3, `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN`). Deadline z `uwagi` to ABSOLUTNY czas dostawy (np. 17:10), **niezależny** od R6-thermal — ale logika łącząca oba MUSI być tier-aware, nie flat 35.
  - **R27 ±5 committed = SOFT, `czas_kuriera` po przypisaniu NIETYKALNY** — nowe pole **NIE** nadpisuje `czas_kuriera`/`czas_kuriera_warsaw`.
  - **Always-propose** — żaden wariant nie tworzy „BRAK KANDYDATÓW".
  - **Paczki** — czasówka-paczka bypass R6 (brak termiki), ALE deadline dostawy z `uwagi` dotyczy KLIENTA, nie temperatury → czy paczka z „Czasówka na HH:MM" ma honorować deadline? **OPEN — do oracle.**

---

## 3. ETAP 3 — MAPA KOMPLETNOŚCI (klasa → wszystkie miejsca; bliźniaki RAZEM)

**Fix ADDITIVE:** nowe pole `delivery_deadline_uwagi` (aware UTC ISO, albo None). **NIE** nadpisuje `order_type` ani `czas_kuriera*` (wzorzec #8 — pole „display" bywa zmienną decyzyjną; tu robimy NOWE pole obok). Każdy konsument udowodniony grepem niżej.

| # | Klasa | Miejsce (plik:linia ~, dryf — grepuj) | Dotknięte? | Uwaga |
|---|---|---|---|---|
| 1 | **Ingest/source (parse)** | `panel_client.normalize_order` ~678/741 — dodać `delivery_deadline_uwagi = _parse_deadline(uwagi, anchor_date)`, anchor = data `pickup_at` (primary), fallback today Warsaw (jak `czas_kuriera` ~665) | **TOUCH** | Jedyny seam u źródła. Parser wyniesiony do wspólnego modułu (NIE z bundle_calib_shadow). |
| 2 | **Parser (wspólny moduł)** | NOWY `czasowka_uwagi.py` (lub `common.py`): `DELIVERY_DEADLINE_RE` + `parse_delivery_deadline(uwagi, day_warsaw)` — lift z `bundle_calib_shadow._parse_deadline` ALE jako kopia/wspólny import; bundle_calib_shadow (audyt) potem może go importować, nie odwrotnie | **TOUCH** | Empirical-first (Lekcja #82): zsampluj realne `uwagi` PRZED finalizacją regexu — obecny łapie tylko frazę „czasówka…". Adrian bywa pisze „na HH:MM"/„do HH:MM"/„dowóz HH:MM"? → sprawdzić korpus. |
| 3 | **State/boundary persist** | `state_machine.upsert_order` zbiór pól ~481-512 (persist `uwagi`/`uwagi_pickup_parsed`/`order_type`) — dodać `delivery_deadline_uwagi` do persisted set | **TOUCH** | Lekcja #80: nowe pole na granicy state → tabela pole×konsument + E2E „pole dociera do końca". |
| 3b | **State update handlers (re-parse)** | `state_machine` PICKUP_TIME_UPDATED ~694-731 / inne update'y zmieniające `uwagi` lub datę pickupu | **TOUCH (audit)** | Jeśli `uwagi` lub data odbioru się zmieni → re-parse deadline (kotwica daty!). Sprawdzić czy update'y niosą `uwagi`. |
| 4 | **Klasyfikacja `order_type`** | konsumenci `order_type` (`dispatch_pipeline` 3016/3417/5321/5325, `czasowka_scheduler` 324/452, `state_machine` 481/505) | **N-D** | Additywne — `order_type` NIETKNIĘTY. Wypisane jako dowód że nie nadpisujemy (wzorzec #8). |
| 5 | **Feasibility/HARD — twin 1** | `feasibility_v2` SLA-loop: `DEFAULT_SLA_MINUTES=35` (l.53), SLA sim ~784/815, `metrics["sla_violations_count"]` ~825, gate `plan.sla_violations>0` ~1135; uwaga `czasówka=pickup-only` ~1016 | **TOUCH (post-audit)** | Wpiąć per-order ABSOLUTNY deadline (`delivered_at > deadline`) OBOK sla_minutes. HARD-vs-SOFT = sekcja 2 (OPEN). |
| 6 | **Simulator/HARD — twin 2** | `route_simulator_v2._count_sla_violations` (l.635-660) + wołacze (768, 950) — dziś tylko jednolite `sla_minutes`; brak per-order deadline | **TOUCH (post-audit)** | Per-order absolutny deadline. Parytet semantyki z twin 1 (#15: ten sam guard/kotwica/coords). |
| 7 | **Plan-recheck/O2 — twin 3** | `plan_recheck._o2_key` (l.683-722), `simulate_bag_route_v2(... sla_minutes=35 ...)` l.699 | **TOUCH (post-audit, PO bramie O2 02.07)** | **SPRZĘŻENIE:** współdzieli wątek z `ENABLE_O2_READY_ANCHOR_SWEEP` (R3, brama 02.07) + `ENABLE_ETA_QUANTILE_R6_BAGCAP` + `ENABLE_PACZKA_R6_THERMAL_EXEMPT`. **NIE kolidować z lane O2 (audyt).** Co-design 3 bliźniaków RAZEM. |
| 8 | **czasówka KOORD scheduler** | `czasowka_scheduler._is_czasowka` (l.127-129, prep≥60+KOORD); czyta już `uwagi` (l.332, 490) | **OPEN (post-audit)** | Czy rozszerzyć trigger T-60/50/40 na `elastic` z deadlinem w `uwagi`? Dziś taki order = brak osłony KOORD. Decyzja z oracle. |
| 9 | **Scoring/SOFT** | nowy term + `bonus_penalty_sum` (19 kar) + serializer A+B + test obecności + materialność | **TOUCH (jeśli ścieżka B)** | Tylko gdy decyzja = SOFT. Kara rosnąca z ryzykiem spóźnienia vs deadline. |
| 10 | **Serializer LOCATION A+B** | `shadow_dispatcher._serialize_candidate` (l.271, LOCATION A) + `_serialize_result.best` (l.500, LOCATION B); prefiksy `_AUTO_PROP_PREFIXES` l.190 | **TOUCH** | **wzorzec #16:** HARD-metryki znikają bez JAWNEJ serializacji (rodzina `sla_violations_*` NIE ma auto-prefiksu). Nowe `delivery_deadline_uwagi` / `deadline_uwagi_breach` / `sla_target_source` — dodać JAWNIE w A i B (albo nadać prefiks). Test `grep -c <key> shadow_decisions.jsonl > 0`. |
| 11 | **Flaga** | NOWA `ENABLE_CZASOWKA_UWAGI_DEADLINE` (+ `..._SHADOW`): `ETAP4_DECISION_FLAGS` (common.py 61-142) + stała OFF default + `decision_flag()`/`flag()` (NIE `os.environ`) + `flags.json` OFF | **TOUCH** | Test ON≠OFF. Wejść do rejestru (inaczej conftest-leak + niewidoczne w fingerprint; F3 to właśnie naprawia). |
| 12 | **plan / scheduled_at** | `state_machine` `proposed_delivery_time` ~573, `expected_delivery_by` ~740-757 (picked+35); `czasowka_scheduler` proposed_time | **TOUCH (audit)** | Czy deadline z `uwagi` ma seedować `scheduled_at`/`proposed_delivery_time`? Dziś `expected_delivery_by`=pickup+35 (źródło rozjazdu 17:34 vs 17:10). |
| 13 | **Config/stałe** | `common.py`: regex + ew. `DELIVERY_DEADLINE_*` stałe; `CZASOWKA_THRESHOLD_MIN` NIETKNIĘTY | **TOUCH** | — |
| 14 | **Tests** | ON≠OFF (flaga), parytet 3 bliźniaków SLA, metryka-w-jsonl, **oracle-case** (sekcja 4), E2E `assess_order` na 484034 | **TOUCH** | Pełna regresja vs baseline `3446P/10F`. |
| 15 | **Konsumenci `uwagi` (audyt kompletności)** | live (non-test): `panel_client:741`, `state_machine:506`, `czasowka_scheduler:332/490`, `panel_watcher:1193/1291` (firmowe address parser L0 — inny parser, nie kolidować) | **N-D / weryfikacja** | Potwierdzić że dodanie pola nie psuje firmowego parsera adresu (osobna ścieżka `uwagi_pickup_parsed`). |

**NIE DOTYKAĆ (poza moim lane / pod audytem):**
- `tools/bundle_calib_shadow.py` + `dispatch-bundle-calib-*` (O2 — audyt `wf_29bb4804`).
- `reassignment_*` / console ghost / fidelity #15 (sesje 17/19).
- `ENABLE_O2_READY_ANCHOR_SWEEP` build (R3, brama 02.07) — tylko KOORDYNOWAĆ przy twin 3.

---

## 4. JAK WALIDOWAĆ (oracle + dowód pozytywnego wpływu) — ETAP 5

> ⚠ **CZEGO oracle MUSI pilnować — DOPISZE audyt `wf_29bb4804` (lane oracle-completeness): R6 tier-aware 35/40, czasówka-z-uwag, committed R27.** **ZACZEKAĆ na to PRZED budową** — inaczej powtórzymy błąd „geometryczne optimum łamie czasówkę" z case'u Bartka. Poniżej szkielet do uzupełnienia po audycie.

- **Oracle (C9/C11, druga metoda — NIE ten sam silnik):** realny `delivered_at` (z `sla_log`/panel) vs deadline z `uwagi`. Dla próbki zleceń z deadlinem: ile dowieziono ≤ deadline ON vs OFF; **bias symetryczny**, apples-to-apples (1× `assess_order`, ta sama kotwica/coords/`now` co live — wzorzec #15).
- **Realna próbka-kotwica:** 484034 (Sikorskiego, „Czasówka na 17:10") + korpus historycznych `uwagi` z frazą czasówka/deadline (zsampluj z orders_state/sla_log).
- **Inwarianty-tripwire:** deadline-aware plan NIE może pogorszyć R6/SLA istniejących (SOFT nie osłabia HARD); `delivery_deadline_uwagi`≥`pickup_at` (deadline przed odbiorem = typo/następny dzień → ignore+flag); ten sam zbiór+liczba stopów.
- **Pozytywny wpływ (≥20% materialność):** metryka docelowa = % dostaw ≤ deadline (lub spadek spóźnień vs deadline) MIERZALNIE lepsza ON↔OFF na korpusie; okno +2 dni shadow. Nie „1 case", nie „brak regresji".
- **Determinizm:** ≥2 odpalenia oracle; migotanie = przyrząd niestabilny.
- **Rejestr:** wpisać shadow/at-job do `shadow-jobs-registry.md` z polem „instrument zwalidowany? (oracle-case)".

---

## 5. KOLEJNOŚĆ BUDOWY (po audycie + ACK) — propozycja

1. **Parser + ingest + persist (additywne, log-only):** `czasowka_uwagi.py` + `panel_client` pole + `state_machine` persist + serializer A+B. Flaga `ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW` ON, decyzyjna OFF. Metryka w jsonl. Zero wpływu na decyzje. **Dowód: pole dociera end-to-end na 484034.**
2. **Shadow-pomiar (C):** zmierz częstość + materialność (ile zleceń/dzień ma deadline, ile by się spóźniło) na oknie. Empirical-first regex (Lekcja #82).
3. **Oracle (sekcja 4) wg specyfikacji z `wf_29bb4804`.** Werdykt: czy WARTO + BEZ regresji.
4. **Wpięcie decyzyjne (A lub B) w 3 bliźniaki SLA RAZEM** (+ ew. KOORD scheduler #8), tier-aware, always-propose, SOFT-nie-osłabia-HARD. Flaga decyzyjna OFF→ON za dowodem + ACK.
5. **Deploy ETAP 6** (.bak→py_compile→test kanoniczny→git log -3→commit jawne pliki→1 restart, NIGDY telegram/peak bez OK) + rollback (flaga=false / .bak / git revert).

---

## 6. DoD (Definition of Done — dla sesji budującej, NIE dla tej)
Każde miejsce mapy dotknięte lub N-D+powód · flaga ON≠OFF (test) · metryka w `shadow_decisions.jsonl` (`grep -c >0`) · parytet 3 bliźniaków SLA (test) · checkery flag + invarianty zielone · **pełna regresja Ziomka zielona vs baseline `3446P/10F/26S/6xf`** (odjąć 10 znanych obcych failów) · **dowód POZYTYWNEGO wpływu** (oracle, ≥20%, nie tylko brak regresji) · +2 dni shadow · `ZIOMEK_LOGIC_REFERENCE`/registry zsynchronizowane · rollback gotowy · **brak kolizji multi-sesja** (reassign 17/19, O2/bundle_calib audyt) · oracle-completeness przejrzany z `wf_29bb4804`. **Częściowe = niezakończone. Wątpliwość → PYTAJ Adriana.**
