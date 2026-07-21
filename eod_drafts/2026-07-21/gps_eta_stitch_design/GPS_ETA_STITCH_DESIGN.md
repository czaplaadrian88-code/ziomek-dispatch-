# Zszycie GPS → trening ETA — DESIGN (READ-ONLY, bez implementacji)

**Data:** 2026-07-21. **Autor:** subagent design (CTO fleet).
**Status:** projekt do wykonania przez Codexa, etapami; ZERO zmian w kodzie/silniku w tej fazie.
**Jedyny zapis tej fazy:** ten katalog (`eod_drafts/2026-07-21/gps_eta_stitch_design/`): `GPS_ETA_STITCH_DESIGN.md`, `coverage.json`, `measure_coverage.py`.
**Owner GO:** 19.07 „zszyj GPS→trening ETA"; 19.07 korekta „buduj+mierz w cieniu TERAZ"; 21.07 „proxy geofence DOZWOLONY jako jawnie oznaczony".
**Źródła kontekstu:** [[eta-gps-training-stitch-2026-07-19]], [[eta-gps-calibration-status-2026-07-19]], protokół #0 ([[ziomek-change-protocol]]).

---

## 0. TL;DR projektowe

- **Co uczy się dziś z klików:** trener kalibratora ETA `tools/eta_calibration/` — target nogi DOSTAWY `actual_deliver_min = delivered_at(klik) − picked_up_at(klik)` (`features.py:build` ~l.351) oraz nogi ODBIORU `pickup_slip_koord_min = picked_up_at(klik) − czas_kuriera(obietnica)` (~l.366). Oba pisane do `eta_calib.db`.
- **Co GPS może naprawić DZIŚ:** TYLKO endpoint DOSTAWY (`physical_delivered_at`, geofence 5b). **Fizycznej prawdy ODBIORU nie ma** (`gps_delivery_truth.jsonl` = delivery-only). ⇒ zszycie dotyka **wyłącznie nogi dostawy**; noga odbioru zostaje na kliku (poza zakresem tej fazy).
- **Materiał (świeży pomiar `coverage.json`):** 9464 wierszy trenera / 42 dni; **1805 (19,1%) ma high-conf GPS JUŻ TERAZ**, 2109 (22,3%) any-conf. To ~7× więcej niż „264 pary" z pomiaru 19.07 — bo target trenera bierze się z `sla_log` (realny kurier), **nie z `alternatives` skażonych A8-2**; filtr `best==real` z tamtego pomiaru był potrzebny tam, tu jest zbędny.
- **Rozjazd klik−GPS (dostawa, n=1805 high-conf):** mediana **+2,35 min**, 95,2% klików spóźnionych, 10,5% |Δ|≥5 min. **Skrócenie targetu po GPS: mediana +2,18 min.** Duży-N, pewny fakt.
- **Uczciwe oczekiwanie MAE (z pomiaru 19.07, wciąż aktualne):** sufit ~1,3% w oracle; na realnym held-out **nieodróżnialne od szumu** — dryf silnika tydzień-do-tygodnia dominuje. Pewny zysk = **higiena pomiaru** (dashboardy/SLA na kliku są +2,2 min pesymistyczne), nie skok trafności.
- **Architektura:** hybryda (GPS gdy jest, klik fallback), **additive + provenance + flaga OFF + cień równoległy**, osobne artefakty modelu GPS (nie ruszają frozen-support championa klikowego). 5 etapów wykonawczych; etap 1 = 100% read-only dowód „warto".
- **Największe ryzyko techniczne:** noga dostawy = `delivered − picked_up`; GPS naprawia tylko koniec, początek zostaje klikiem. Jeśli klik-odbioru też jest spóźniony (niezmierzone), skrócenie o ~2,2 min może być NETTO nie-całą-prawdą. Dlatego mierzymy przeciw GPS jako arbitrowi i kontrolujemy dryf — nie flipujemy „bo intuicja".

---

## 1. Pomiar jakości materiału (READ-ONLY) — `coverage.json`

Skrypt: `measure_coverage.py` (reprodukowalny, RO; join trenera `eta_calib.db` ⋈ `gps_delivery_truth.jsonl` po `order_id`). Wynik zapisany w `coverage.json`. Kluczowe:

| Metryka | Wartość |
|---|---|
| Korpus trenera (`eta_calib_features`) | 9464 wierszy, 42 dni (2026-05-08 → 07-20) |
| GPS-truth rekordów (unikalne zlecenia) | 2404 (2051 high-conf, 353 low) |
| Zakres GPS | 2026-06-10 → 07-21 (41 dni); **świeżość: ostatni fizyczny 22 min temu** |
| Pokrycie vs cała `sla_log` (17 500 zleceń) | any-conf 13,7% / high-conf 11,7% |
| **Pokrycie vs TRENER (delivery leg) — użyteczne DZIŚ** | **any-conf 22,3% (2109) / high-conf 19,1% (1805)** |
| Dni trenera bez żadnego GPS | 7 (05-08..05-17 przed startem 5b + 07-02 anomalia) |
| Rozjazd klik−GPS dostawy (high-conf, n=1805) | mediana **+2,35 min**, mean +2,65; 95,2% klik późno; 10,5% |Δ|≥5 |
| Przesunięcie targetu dostawy (klik−GPS) | mediana **+2,18 min** (target skróci się po GPS) |
| Kurierzy z ≥15 high-conf parami | **20** (top: cid 400→251, 123→180, 484→166, 370→145, 393→134, 541→110…) |
| Fizyczna prawda ODBIORU | **BRAK** (delivery-only) |

**Świeżość/dziury:** GPS rośnie ciągle (timer walidacji co 5 min; ostatni rekord 22 min temu). Dziury = wyłącznie majowe dni sprzed geofence 5b (06-10) + 07-02. Pokrycie floty rośnie (0%→~20% trenera w 6 tyg.), ale nadal mniejszość → **hybryda konieczna**, nie zamiana totalna.

**Reprezentatywność (uczciwie):** 20 kurierów ma ≥15 par → per-kurier GPS-kalibracja możliwa tylko dla topu; bundle nadreprezentowany; predykcje trenera to „z chwili przypisania" (~34 min przed dostawą), więc surowy błąd modelu (sd~17 min) >> bias klika (2,2 min) — to dlatego zysk MAE mały. Wszystkie te zastrzeżenia = z pomiaru 19.07, potwierdzone tu na 7× większym N.

---

## 2. Gdzie w kodzie: writer i konsumenci targetu (mapa kompletności)

### 2.1 WRITER targetów (jedno źródło) — `tools/eta_calibration/features.py:build()`
- **DOSTAWA** `actual_deliver_min`: `dl = parse_naive_warsaw(s["delivered_at"])`, `pu = parse_naive_warsaw(s["picked_up_at"])` → `actual_deliver = (dl−pu)/60` (**l.348–351**). Oba z `sla_log` = **KLIK**. Zapis do kolumny `actual_deliver_min` (krotka l.380, DDL l.236–248, INSERT l.391–393).
- **ODBIÓR** `pickup_slip_koord_min`: `slip = (pu − ck)/60`, `ck = czas_kuriera_dt(rutcom_ck, pu)` (**l.366–367**). `pu` = KLIK, `ck` = obietnica koordynatora. **Poza zakresem GPS** (brak fizycznego odbioru).

### 2.2 KONSUMENCI targetu (BLIŹNIAKI — muszą iść RAZEM)
Każde miejsce, które czyta `actual_deliver_min` / `pickup_slip_koord_min`, dziś czyta INLINE. Zmiana znaczenia targetu bez dotknięcia wszystkich = rozjazd:

| # | Plik:linia | Rola | Gałąź dostawy |
|---|---|---|---|
| C1 | `evaluate.py:190–191` `target(r)` | cel ewaluacji holdout | `r["actual_deliver_min"]` |
| C2 | `evaluate.py:147` `_conformal_deltas` | offset conformal | `r["actual_deliver_min"]` |
| C3 | `evaluate.py:181` | conformal calib resid | `r["actual_deliver_min"]` |
| C4 | `evaluate.py:199–200,239` | baseline naiwny/kurier | `r["actual_deliver_min"]` |
| C5 | `promotion.py:45–53` `target_value()` | **frozen-support gate** | `r["actual_deliver_min"]` |
| C6 | `calibrate.py:173` `write_shadow` `t=` | shadow log pred vs real | `r["actual_deliver_min"]` |
| C7 | `replay.py:91–98` | eligibility supportu | `r["actual_deliver_min"]` |

**Bliźniak nogi:** wszędzie wzorzec `pickup_slip_koord_min if leg==PICKUP else actual_deliver_min`. Zmieniamy **tylko gałąź DELIVERY**; PICKUP zostaje bez zmian, ale przechodzi przez ten sam selektor (żeby nie było dwóch dróg).

**Bliźniak artefaktu:** `calibrate.write_runtime_maps` pisze champion `delivery_map.json` + candidate `delivery_candidate_map.json` (config l.14–19). Model GPS MUSI mieć **własne** ścieżki (`delivery_candidate_gps_map.json`, `delivery_gps_map.json`), żeby nie ruszyć `target_fingerprint` frozen-support klikowego championa (inaczej `promotion.compare_on_frozen_support` rzuci `frozen_support_target_drift` — l.267 — bo target dostawy zmieniłby wartość pod zamrożonym kluczem).

### 2.3 Bliźniaki OBSERWACYJNE (nie-trener, ale „prawda") — utrzymać spójność, NIE mylić z trenerem
- `eta_calibration_logger.py` → `eta_calibration_log.jsonl` (metryka `matched_courier`, liczy błąd z `sla_log` = klik). To reporter, nie trener. Higiena pomiaru: docelowo dołożyć obok pole GPS (opcjonalnie, Etap 5+).
- `eta_ground_truth.py` (`KPI_BINDING_V1`, „GPS=kanon" 18.07) — JUŻ GPS-świadomy, obserwacyjny, **nigdy nie wpięty jako sygnał treningowy**. To jest dokładnie „spec", który ten stitch realizuje po stronie trenera.
- `tools/ziomek_pred_calibration.py` → `ziomek_pred_calibration.jsonl` — dostarcza `eng_deliver_pred_min` (baseline silnika = FEATURE, nie target). **Nie ruszamy.**

---

## 3. Projekt zszycia (noga dostawy, hybryda, provenance, flaga OFF)

### 3.1 Nowe kolumny w `eta_calib_features` (ADDITIVE)
```
actual_deliver_min_gps  REAL   -- physical_delivered_at − picked_up_at(klik); None gdy brak high-conf GPS
deliver_target_source   TEXT   -- 'click' | 'gps_geofence_proxy' | 'handoff_event'(future)
gps_conf                TEXT    -- 'high' | 'low' | None (audyt/segmentacja)
```
- `actual_deliver_min` (klik) **zostaje nietknięte** → flaga OFF = bit-identyczne zachowanie.
- Provenance wprost realizuje żądanie ownera (klik vs geofence-proxy vs przyszły handoff-event). Domyślnie `'click'`; `'gps_geofence_proxy'` gdy użyto GPS (owner 21.07: proxy = przyjazd do geofence, jawnie oznaczony); `'handoff_event'` zarezerwowane na przyszłe fizyczne wręczenie z apki.
- Filtr jakości GPS: tylko `confidence=='high'` (n_in_geofence≥2) zasila `_gps`; low idzie do audytu, nie do targetu.

### 3.2 Selektor = jedyny punkt wyboru (choke point)
`models.py` (albo nowy `targets.py`): `deliver_target(row, use_gps: bool) -> float|None`:
- `use_gps and row['actual_deliver_min_gps'] is not None` → GPS (hybryda: GPS gdy jest);
- w p.p. → `row['actual_deliver_min']` (klik fallback).
Wszyscy konsumenci C1–C7 wołają selektor (dla DELIVERY) zamiast inline. PICKUP przez ten sam selektor zwraca zawsze `pickup_slip_koord_min` (bez wariantu GPS).

### 3.3 Flagi (NOWE, wszystkie OFF; świat = `config.yaml` + env `ETA_CALIB_*`, NIE `flags.json` silnika)
| Flaga | Domyślnie | Efekt |
|---|---|---|
| `ETA_CALIB_WRITE_GPS_TARGET` | **false** | features.py wypełnia `_gps`/provenance (additive; OFF = kolumny NULL, zero różnic) |
| `ETA_CALIB_USE_GPS_TARGET` | **false** | selektor zwraca GPS-hybrydę zamiast klika (dotyka C1–C7) |
| `ETA_CALIB_GPS_SHADOW` | **false** | calibrate liczy RÓWNOLEGLE model GPS (osobne mapy `*_gps_*`) + loguje MAE(klik) vs MAE(GPS); serving bez zmian |

⚠ 3 światy flag: to narzędzie ma WŁASNY config (env override `ETA_CALIB_<KLUCZ>`), rozłączny z `flags.json` silnika i drop-inami panelu/apki. Nowa flaga = wpis w `config.yaml` + odczyt w `load_config`. (Reguła dwóch miejsc dotyczy dyrektyw sesyjnych CLAUDE.md↔AGENTS.md, nie tej configówki — ale wpis do `docs/` warto zrobić.)

### 3.4 Dowód „warto + BEZ REGRESJI" (protokół #0 krok 3–5)
- **BEZ REGRESJI (twarde):** flaga OFF ⇒ `actual_deliver_min` i cała ścieżka bit-identyczna. Test: pełen `pytest tests/` + `tools/eta_calibration/tests/` zielony vs baseline; `target_fingerprint` klikowego championa niezmieniony; kolumny `_gps` NULL przy OFF.
- **FLAGA ON≠OFF:** test na fixture pokazujący, że dla wiersza z GPS selektor zwraca inną wartość (dowód, że flaga faktycznie działa).
- **WARTO (pozytywny wpływ, nie tylko brak regresji):** na **held-out wyłącznie z high-conf GPS**, w **świeżym krótkim oknie** (kontrola dryfu — patrz niżej), porównać paired:
  - model uczony na targecie **klik** vs uczony na targecie **GPS**, oba oceniane przeciw **GPS physical jako arbitrowi**;
  - metryka: ΔMAE (GPS−klik) z paired bootstrap CI + Wilcoxon (te same przyrządy co `evaluate._paired_delta_ci` / `promotion._paired_stats`).
  - **Kontrola dryfu (kluczowe — inaczej powtórzymy szum z 19.07):** NIE jeden globalny split 5-tygodniowy. Zamiast: (a) okno ≤14 dni świeże, ORAZ (b) relatywny gain per-dzień (średnia z dziennych ΔMAE), ORAZ (c) walk-forward krótki. Zysk raportować jako rozkład dzienny, nie jeden punkt.
  - **Próg GO realistyczny:** przy sufity ~1,3% i n~1800 — nie oczekiwać `delivery_mae_improve_pct=5` (obecny próg promocji). Rekomendacja: dla wariantu GPS traktować jako **równoważność + higienę** (non-inferiority na MAE-vs-GPS + potwierdzony brak biasu), a nie „materialna poprawa MAE". Decyzja progu = Adrian, bo to zmiana definicji sukcesu.
- **Higiena pomiaru (pewny, natychmiastowy zysk, niski risk):** dołożyć do dashboardów/SLA prawdę GPS jako oznaczoną — dziś liczone na kliku są +2,2 min pesymistyczne. To osobny, bezsporny deliverable (Etap 5b).

### 3.5 Rollback
Każdy etap: flaga → false (hot przez env/config + następny bieg timera 05:20) lub `git revert`. Kolumny additive zostają (NULL nieszkodliwe). Champion klikowy nietknięty do samego końca (osobne mapy GPS).

---

## 4. Etapy wykonawcze dla Codexa (każdy = 1 pakiet z testami)

> Reguła: każdy etap = 1 PR/commit + testy + `pytest tests/` zielony vs baseline. `dod` skillem `ziomek-cto` przed commitem. Etapy 1 read-only; 2–4 dotykają narzędzia trenera (NIE silnika decyzyjnego — `tools/eta_calibration/` jest shadow-only, `runtime.shadow_only: true`); żaden nie dotyka ścieżki żywych decyzji ani `flags.json`.

### Etap 1 — DOWÓD „WARTO" offline (100% READ-ONLY, zero zmian repo trenera)
- **Zakres:** standalone skrypt w tym katalogu (lub `tools/eta_calibration/`, ale bez importu do ścieżki produkcyjnej) który: buduje in-memory model klik-target vs GPS-target na tym samym froz. supporcie z `eta_calib.db` + `gps_delivery_truth.jsonl`, ocenia przeciw GPS-arbitrowi, raportuje ΔMAE z kontrolą dryfu (dzienny/walk-forward/≤14d).
- **Read-only?** TAK. Czyta `eta_calib.db` (mode=ro) + GPS jsonl. Zero zapisu poza raportem/JSON w scratchpadzie.
- **Testy:** determinizm (seed), sanity: liczba par == coverage.json, arbiter=GPS.
- **Wyjście:** liczbowy werdykt „warto/nie" z uczciwym CI. **Bramka: jeśli ΔMAE nieodróżnialne od zera — i tak idziemy dalej dla higieny+KPI_BINDING_V1, ale z jawnie obniżonym oczekiwaniem i progiem równoważności (decyzja Adriana).**

### Etap 2 — DANE additive (dotyka `features.py`, flaga OFF)
- **Zakres:** DDL + populacja `actual_deliver_min_gps` / `deliver_target_source` / `gps_conf`; loader `gps_delivery_truth.jsonl` (RO, index po order_id, high-conf); gate `ETA_CALIB_WRITE_GPS_TARGET` (OFF).
- **Silnik?** Nie — tylko feature-store shadow (`eta_calib.db`). Zero wpływu na decyzje/serving.
- **Testy:** OFF ⇒ kolumny NULL + `actual_deliver_min` identyczny (regresja bit); ON ⇒ ~19% wierszy z `_gps`, provenance='gps_geofence_proxy', `_gps ≈ actual_deliver_min − delta_button_minus_physical`. Idempotencja INSERT OR REPLACE. `pytest tools/eta_calibration/tests/` zielony.

### Etap 3 — SELEKTOR + wszystkie bliźniaki (flaga OFF)
- **Zakres:** `deliver_target(row, use_gps)` + wpięcie C1–C7 (evaluate target/conformal/baseline, promotion.target_value, calibrate.write_shadow, replay). Flaga `ETA_CALIB_USE_GPS_TARGET` (OFF). Bliźniak nogi ODBIÓR przez ten sam selektor (bez wariantu).
- **Silnik?** Nie (shadow tool).
- **Testy:** OFF≡dziś (wszystkie 7 miejsc zwracają klik; `target_fingerprint` championa niezmieniony — twardy anty-drift test na `promotion`); ON≠OFF na fixture; pełna regresja `tests/` + `tools/eta_calibration/tests/`.

### Etap 4 — MODEL GPS w cieniu, RÓWNOLEGLE (osobne artefakty, flaga OFF→ON tylko dla cienia)
- **Zakres:** `calibrate.py` przy `ETA_CALIB_GPS_SHADOW=ON` liczy drugi wariant nogi dostawy na targecie GPS → pisze `delivery_candidate_gps_map.json` (+ `delivery_gps_map.json` po własnej bramce) + do `eta_calib_metrics.jsonl` dorzuca `delivery_gps: {mae, n, delta_vs_click, ci}`. Champion serwowany (klikowy) **bez zmian**. Osobne ścieżki w `config.yaml`.
- **Silnik?** Nie. Serving obietnic (D6a) czyta zamrożony champion klikowy — nietknięty.
- **Testy:** artefakt GPS nie nadpisuje `delivery_map.json` klikowego; frozen-support klikowy stabilny; metryka GPS pojawia się w logu. **Okno cienia ≥2 dni** przed jakąkolwiek rozmową o flipie.

### Etap 5 — POMIAR końcowy + ACK (decyzja Adriana) + higiena
- **5a (serving flip — TYLKO za ACK):** po 2 dniach cienia porównać MAE(klik)↔MAE(GPS) na świeżym oknie z kontrolą dryfu; przedstawić Adrianowi: propozycja, dowody, ryzyko (endpoint-tylko-dostawa), rekomendacja. Flip `ETA_CALIB_USE_GPS_TARGET` + promocja championa GPS **wyłącznie po owner-ACK** (zmiana sygnału treningowego serwowanych obietnic).
- **5b (higiena — niski risk, można wcześniej):** oznaczyć w dashboardach/SLA/`eta_calibration_log.jsonl` prawdę GPS obok klika (obie jawne). Pewny zysk niezależny od 5a.

**Zależności zewnętrzne / kolejka:** buduje się TERAZ w cieniu (owner 19.07). Niezależne od at#220/A8-2 — bo target trenera nie dotyka `alternatives`. Po A8-2/at#220 rośnie N obserwacyjnych metryk, ale delivery-target-stitch ich nie potrzebuje. Warunek 90/30 ([[forgotten-bugs-sweep-2026-07-19]] #4) budować DOPIERO po tym (lepsze ETA jako fundament).

---

## 5. Ryzyka (posortowane)

1. **Największe: endpoint-tylko-dostawa.** GPS naprawia koniec, początek = klik. Netto skrócenie ~2,2 min może nie być pełną prawdą, jeśli klik-odbioru też spóźniony (niezmierzone). Mitygacja: arbiter=GPS w pomiarze, provenance jawne, przyszły pickup-geofence/handoff-event (`courier_ground_truth` status 3→5 mógłby dać fizyczny odbiór — osobna faza).
2. **Dryf silnika >> bias klika.** Powtórka szumu z 19.07, jeśli walidacja globalna 5-tyg. Mitygacja: okno ≤14d + gain dzienny + walk-forward (§3.4).
3. **Frozen-support drift w promocji.** Zmiana targetu pod klikowym championem = `frozen_support_target_drift`. Mitygacja: osobne mapy `*_gps_*` (§2.2, Etap 4).
4. **Oczekiwanie „GPS naprawi ETA".** Sufit ~1,3%; realny zysk = higiena. Mitygacja: jawne kalibrowanie oczekiwań + próg równoważności zamiast „materialna poprawa" (decyzja Adriana).
5. **Pokrycie mniejszościowe (19%).** Model tylko-GPS ma ~5× mniej danych. Mitygacja: hybryda z fallbackiem klik (potwierdzona, nie zgadywana).

---

## 6. Artefakty tej fazy
- `coverage.json` — pełny świeży pomiar (2026-07-21).
- `measure_coverage.py` — reprodukowalny skrypt RO (join trener⋈GPS).
- `GPS_ETA_STITCH_DESIGN.md` — ten dokument.
