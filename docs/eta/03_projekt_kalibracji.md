# B1.3 — Projekt narzędzia kalibracji ETA per-leg (z warstwą per-kurier)

> **Faza:** B1 (projekt) · **Data:** 2026-07-07 · **Status:** DO AKCEPTACJI (STOP B1) · **Poprzednik:** `01_badanie_eta.md`, `02_research.md`
> **Zasada:** shadow-first, zero wpięcia w żywe ETA do osobnej decyzji; wszystkie obiekty `eta_calib_*`.

## Spis treści
- [0. Ramy od Adriana](#0-ramy-od-adriana-wiążące)
- [1. EDA — dowody decydujące o architekturze (B1.2)](#1-eda--dowody-decydujące-o-architekturze-b12)
- [2. Werdykt architektoniczny](#2-werdykt-architektoniczny-potwierdź-zmodyfikuj-odrzuć)
- [3. Wybrana metoda i architektura](#3-wybrana-metoda-i-architektura)
- [4. Segmentacja (obie nogi osobno)](#4-segmentacja-obie-nogi-osobno)
- [5. Personalizacja, shrinkage, cold-start](#5-personalizacja-shrinkage-cold-start)
- [6. Niepewność — kwantyle](#6-niepewność--kwantyle)
- [7. Zestaw cech (known-at-prediction-time)](#7-zestaw-cech-known-at-prediction-time)
- [8. Protokół walidacji + kryteria akceptacji Z GÓRY](#8-protokół-walidacji--kryteria-akceptacji-z-góry)
- [9. Runtime, champion/challenger, shadow](#9-runtime-championchallenger-shadow)
- [10. Obiekty DB, cache, budżet, monitoring](#10-obiekty-db-cache-budżet-monitoring)
- [11. Ryzyka i otwarte kwestie](#11-ryzyka-i-otwarte-kwestie)

---

## 0. Ramy od Adriana (wiążące)
1. Narzędzie ma być **kompletne, zdolne wydać uczciwy werdykt GO** (nie tylko obserwacyjne).
2. Kalibracja **OBU nóg osobno** — ODBIÓR (L1 dojazd + L2 czekanie) i DOSTAWA (L3).
3. **Najlepszy jakościowo kwantyl** — asymetryczny koszt (spóźnienie > zbyt wczesna obietnica).
4. **Bić baseline'y z pewnością** — CI + istotność (Diebold-Mariano / paired), uczciwie vs wszystkie.
5. Prawda per-leg z **Rutcom „oczekiwanie odbiór" + GPS z apki** (mało, ale jest).

## 0a. AKTUALIZACJA po STOP B1 — per-kurier FIRST (decyzja Adriana + nowy dowód)

Adrian **odrzucił** przeformułowanie „de-bias kontekstowy zamiast per-kurier" (pkt A). Domena: każdy kurier jeździ inaczej (prędkość, skłonność do łamania przepisów, umiejętność jazdy, praca pod presją = wielkość worka, styl) — sygnał jest i mamy dość danych. **Ma rację i potwierdza to nowy, mocniejszy probe** (`scratchpad/b12_probe2.py`, walk-forward OOT):

| Model (noga ODBIORU, baza czas_kuriera) | MAE | ±5 min |
|---|---|---|
| baseline (ufaj czas_kuriera) | 7.05 | 46.6% |
| globalny de-bias | 5.65 | 59.1% |
| per-kurier **płaski** offset | 5.55 | 58.5% |
| **LightGBM (load+godz+restauracja+KURIER)** | **5.11** | **63.7%** |
| LightGBM BEZ kuriera (ablacja) | 5.28 | 61.8% |

**Cecha kuriera dokłada ISTOTNIE** (Wilcoxon |err| p=0.009). Noga DOSTAWY (GBDT, bez dystansu): kurier też istotny (p=0.002, 8.73→8.62). **Wniosek: mój pierwszy probe użył najsłabszej formy (płaski offset na bazie, która wchłania kuriera) — dlatego zawiódł. Właściwa forma (kurier jako cecha × kontekst w GBDT-kwantyl + pooling) DAJE pozytyw OOT.** Dlatego personalizacja per-kurier = **rdzeń, nie warstwa dodatkowa**.

**Dane odblokowujące „styl jazdy" (dystans/tempo):**
- `drive_min_enriched.jsonl` — per-zlecenie: silnika `travel_min`/`drive_min` + `km_to_pickup` + realny `pickup_ts`/`delivered_ts` (per-leg L1).
- **Odtworzenie tras z Rutcom (idea Adriana):** grupuj zlecenia per (kurier, dzień), sortuj po czasie odbioru → łańcuch przystanków; join `nr zlecenia`→`sla_log`/`orders_state` po pełne coords → **OSRM na każdą realną nogę (drop_i→pickup_{i+1}, pickup→drop)** → **realne tempo = czas_Rutcom / OSRM_freeflow per kurier** = czysty, dystans-znormalizowany sygnał stylu jazdy (prędkość/agresja). To jest cecha, której brakowało.

**Rewizja projektu (§2-§7 czyta się z tą nadrzędną zmianą):** per-kurier modelowany przez **interakcje kurier×kontekst w GBDT-kwantyl + hierarchiczny pooling/shrinkage dla małej próby + tempo z odtworzonych tras**, per-leg. Reguły/empiryka = interpretowalny fallback + sanity (D). Walidacja walk-forward decyduje o finalnym kształcie; kryteria GO §8 bez zmian.

---

## 1. EDA — dowody decydujące o architekturze (B1.2)

Wszystko `[ZWERYFIKOWANE]`, skrypty `scratchpad/b12_eda.py`, `b12_probe.py`.

**1.1 Dekompozycja wariancji (η², one-way ANOVA):**

| Czynnik | Noga DOSTAWY (real_delivery_min, n=13 993) | Noga ODBIORU (poślizg vs `czas_kuriera`, n=3 300) |
|---|---|---|
| kurier | **0.039** (p=3e-86) | **0.117** (p=2e-65) |
| restauracja | 0.036 (p=1e-64) | 0.104 (p=4e-44) |
| obciążenie (worek) | 0.009 | **0.055** (p=2e-40) |
| godzina | 0.009 | 0.014 |
| dzień | 0.002 | — |

→ **Noga ODBIORU ma ~3× silniejszy sygnał kuriera i realny sygnał obciążenia.** Noga dostawy jest słabo tłumaczalna (max η² 0.04).

**1.2 Stabilność split-half (Spearman mean 1.poł↔2.poł):** kurier-odbiór **0.65** (p=0.003), kurier-dostawa 0.57 (p=0.002), restauracja-dostawa 0.63. → offsety per-encja PERSYSTUJĄ (umiarkowanie).

**1.3 Liczność (okno dostępne):** dostawa 52 kurierów (33 n≥100, 36 n≥50, 12 n<30); odbiór 35 kurierów (11 n≥100, 21 n≥50, 10 n<30). → część floty wystarczy na personalizację, część wymaga poolingu, nowi → prior. **Uzasadnia shrinkage hierarchiczny.**

**1.4 Stałe pary adresów (restauracja→dostawa, 196 par n≥5):** ta sama trasa waha się **mediana 21 min** (p90 35), a **obciążenie i godzina tego NIE tłumaczą** (Spearman ~0.00 / −0.10 w obrębie pary). → predykcja punktowa dostawy jest z natury szumna → **konieczne przedziały (kwantyle), nie punkt.**

**1.5 PROBE walk-forward (out-of-time) — czy per-kurier daje GO?**

Poślizg odbioru (ziomek_pred, test 07-01→07-07): baseline „ufaj czas_kuriera" MAE 7.05 → **globalny de-bias 5.65 (−20%)** → per-load 5.43 → per-kurier shrunk 5.56 (vs globalny p=0.11, **nieistotnie**) → per-kurier×load 5.48.

Rutcom (51 dni, n=6 586, test 06-28→07-06): baseline 7.19 → **globalny de-bias 5.92 (−17.7%, p=1e-37)** → per-restauracja 5.84 (+1.3% vs global, p=0.07) → **per-kurier 6.01 (−1.6% vs global, GORZEJ, p=0.06).**

Noga dostawy: per-kurier shrunk vs globalna mediana = −0.3% (p=0.30, **bez poprawy**).

**Wniosek liczbowy:** pewny, wielki GO to **kontekstowe de-biasowanie per-leg** (globalny/load/restauracja, −17…−23%, p<1e-37). **Addytywny per-kurier nie generalizuje out-of-time** (0…−1.6%, nieistotny/ujemny) — potwierdza werdykt advisory E-7 na OBUCH nogach. Powód: `czas_kuriera` już wchłania część info o kurierze, a wariancja per-dostawa topi sygnał.

## 2. Werdykt architektoniczny (potwierdź/zmodyfikuj/odrzuć)

Rekomendowana architektura wyjściowa = kalibracja residualna, segmentowana per-leg, personalizowana per-kurier z shrinkage, produkująca kwantyle.

- **POTWIERDZAM:** kalibracja **residualna** na istniejącym ETA (nie zastępujemy silnika); **segmentacja per-leg** (dane potwierdzają rozłączne czynniki — pkt 1.1); **kwantyle** zamiast punktu (pkt 1.4); **shrinkage** (pkt 1.3).
- **MODYFIKUJĘ:** oś personalizacji. Dowód (1.5) mówi, że **główny sygnał jest KONTEKSTOWY (leg × obciążenie × restauracja × pora), nie per-kurier.** Dlatego rdzeń = **de-bias per (leg, kontekst)**; per-kurier = **cienka warstwa mocno-shrunk z kwarantanną driftu**, włączona bo w zakresie, ale raportowana z WŁASNĄ deltą OOT (spodziewana ≈0) — bez udawania, że to ona daje GO.
- **ODRZUCAM (z dowodem):** naiwny addytywny residual per-kurier jako główny mechanizm (E-7 NO-GO + moje 1.5: −1.6% OOT na Rutcom). Odrzucam też kalibrację **do obietnicy koordynatora** jako celu — kalibrujemy do RZECZYWISTOŚCI (picked_up/delivered), a `czas_kuriera` traktujemy jako baseline do pobicia i cechę.

## 3. Wybrana metoda i architektura

**Nazwa robocza:** `eta_calib` — dwuwarstwowy kalibrator residualny per-leg, shadow-first.

```
Dla każdego zlecenia (w momencie predykcji):
  ODBIÓR (target = realny picked_up):
     baza      = czas_kuriera (obietnica) LUB pred_pickup silnika
     korekta   = q_pickup[leg=odbiór][kontekst] + shrunk_courier_pickup[cid]
     kontekst  = (obciążenie_kuriera, slot_daypart, restauracja)
  DOSTAWA (target = realny delivered):
     baza      = predicted_delivery_min silnika (ready-anchor) LUB OSRM(pickup→drop)×traffic + dwell
     korekta   = q_deliv[leg=dostawa][slot × solo/worek] + restauracja_resid + shrunk_courier_deliv[cid]
     (= rozszerzenie istniejącego „ETA warunkowego"/eta_cell_residual o oś kuriera)
  WYJŚCIE per noga: P50, P80, P90 (kwantyle empiryczne/pinball), nie tylko punkt.
```

- **Wariant bazowy = reguły/empiryka najpierw** (zgodnie z filozofią repo): korekty = **kwantyle empiryczne per (leg, kubełek kontekstu)** z partial-poolingiem (kolaps kubełka gdy n spada) + offset per-kurier z EB-shrinkage. Proste, audytowalne, O(n), tanie, łatwe do rollbacku.
- **Eskalacja do LightGBM-quantile** (τ∈{0.1,0.5,0.9}) na residuum **TYLKO jeśli** bije wariant empiryczny na hold-oucie (kryteria §8). Cechy jak §7. Warstwa per-kurier zostaje osobno (target-encoding + shrinkage), nie topiona w drzewie.
- **Zgodność z regułami Ziomka:** korekta ADDYTYWNA na predykcję/obietnicę; **NIE dotyka bramek HARD** (R6-35, R-DECLARED-TIME) — SOFT nie osłabia HARD (ADR-001). Feasibility czyta osobno (dziś: kwantyl p80 R6-bagcap).

## 4. Segmentacja (obie nogi osobno)

| Noga | Target rzeczywisty | Baza (known-at-pred) | Główne czynniki (η²) | Prawda |
|---|---|---|---|---|
| **L1+L2 ODBIÓR** | `picked_up_at` | `czas_kuriera` / pred_pickup | kurier 0.117, restauracja 0.104, **obciążenie 0.055** | sla_log + Rutcom „czas odbioru" + `oczekiwanie odbiór` |
| **L3 DOSTAWA** | `delivered_at` | pred_delivery / OSRM×traffic+dwell | kurier 0.039, restauracja 0.036 | sla_log + `gps_delivery_truth` (physical vs button, n=1056) |

Uwaga do L2: `oczekiwanie odbiór` (Rutcom) mediana **0 min** (76% zerowe) → poślizg odbioru to głównie **„kurier dojeżdża późno pod obciążeniem", NIE czekanie na jedzenie**. L1 (timing dojazdu pod restaurację, funkcja łańcucha/obciążenia) jest właściwym celem; L2 jako osobna korekta prep-bias per-restauracja (już zbudowana, shadow).

## 5. Personalizacja, shrinkage, cold-start

- **Empirical-Bayes shrinkage:** `offset_c = global + (mean_c − global)·n_c/(n_c+K)`, `K = within_var/between_var` z train (probe: K≈8 odbiór, ≈12 dostawa). Kurierzy n<min_n (30-40) → silnie ściągnięci do prioru klasy/globalu.
- **Cold-start (nowy kurier):** prior = tier/klasa prędkości (klaster po średnim dystansie/tempie) → global. Zero „halucynacji" offsetu z 3 zleceń.
- **Kwarantanna driftu:** jeśli |offset_c bieżący − poprzedni| > 5 min ∧ n≥40 → kurier do kwarantanny (offset=prior, alert). (Advisory C-04, wykrywa realnie C30 drift 8.8 min.)
- **UCZCIWOŚĆ:** warstwa per-kurier raportowana z osobną deltą OOT w każdym raporcie walidacji. Jeśli jej delta ≤0 istotnie — **domyślnie WYŁĄCZONA** w champion (zostaje de-bias kontekstowy). Włączamy tylko gdy udowodni dodatni wpływ (§8).

## 6. Niepewność — kwantyle

- Wyjście: **P50 + P80 + P90** per noga (kwantyle empiryczne per kubełek; przy LGBM — pinball loss).
- **Kwantyl operacyjny (asymetryczny koszt, wybór Adriana „najlepszy jakościowo"):** obietnica emitowana na **P80** nogi (spóźnienie droższe niż zbyt wczesna obietnica), przedział pokazywany [P50, P90]. Wybór P80 vs P90 kalibrowany empirycznie na pokrycie (§8) i koszt asymetryczny (pinball) — do potwierdzenia liczbą w B3.
- **Conformal (opcja eskalacji):** split-conformal / CQR na residuum dla gwarancji pokrycia przedziału niezależnie od modelu (patrz `02_research.md`).

## 7. Zestaw cech (known-at-prediction-time)

Tylko dane znane w momencie predykcji (zero wycieku z przyszłości):
- **Kontekst floty:** obciążenie kuriera (liczba aktywnych zleceń / rozmiar worka), pozycja/łańcuch, tier.
- **Czas:** slot daypart (peak_lunch/high_risk/peak_dinner/off), godzina, dzień, weekend.
- **Zlecenie:** restauracja (target-encode + shrink), dystans OSRM (routed), solo/worek, `czas_kuriera` (jako cecha bazowa odbioru), typ (elastyk/czasówka).
- **Kurier:** id (do warstwy shrunk), historyczne offsety per-leg (EWMA 14 d).
- **ZAKAZANE (wyciek):** `prediction_age`, cokolwiek pochodne od picked_up/delivered przy predykcji tego legu, dorzucanie zleceń PO decyzji.
- **Brak dziś:** live pogoda/ruch — kandydat na cechę (as-known cache), ale MVP bez (ruch = statyczna tabela).

## 8. Protokół walidacji + kryteria akceptacji Z GÓRY

**Podział:** wyłącznie **walk-forward / rolling-origin** (nigdy losowo). Train na oknie ściśle poprzedzającym holdout ~14 dni. Baseline'y: (a) obecne ETA @assign, (b) żywe @last, (c) obietnica koordynatora `czas_kuriera`, (d) naiwny (mediana kuriera).

**Metryki:** MAE, RMSE, MAPE (z ostrożnością — mały mianownik), bias (ME), %±5/±10/±15, **pokrycie P80/P90** (empiryczne w [76-84%] / [86-94%]). Istotność: **Diebold-Mariano** (lub sparowany Wilcoxon/t z poprawką na wielokrotne porównania), 95% CI delty MAE (bootstrap).

**Kryteria akceptacji (GO), zdefiniowane teraz:**
1. **ODBIÓR:** challenger bije „ufaj czas_kuriera" na OOT MAE o **≥12%** (probe daje −18…−23%) i **nie gorszy** od koordynatora; pokrycie P80 w paśmie. → **realistycznie osiągalne (GO prawdopodobny).**
2. **DOSTAWA:** challenger (obietnica @assign po korekcie) bije obecne @assign o **≥5% OOT MAE** i bije naiwny (mediana kuriera 9.6); pokrycie P80 w paśmie; **breach R6 bez wzrostu.**
3. **Warstwa per-kurier:** dołączana do champion **tylko jeśli** dokłada dodatni OOT MAE istotnie (DM p<0.05). Inaczej raport mówi „per-kurier neutralny/szkodliwy" i champion zostaje kontekstowy — **uczciwy NIE-GO tej warstwy, GO całości.**
4. **Całość:** end-to-end (odbiór+dostawa) obietnica poprawia OOT MAE o **≥15%** vs dziś @assign, 95% CI delty nie obejmuje 0.
5. Jeśli którekolwiek nie przejdzie → raport pisze to WPROST + rekomendacja NIE-WDRAŻAĆ / Z ZASTRZEŻENIAMI.

## 9. Runtime, champion/challenger, shadow

- **Batch nocny idempotentny** (Python/SQL, bez LLM w pętli): buduje mapy `eta_calib_*` z okna N dni, liczy walk-forward metryki, wybiera champion.
- **Champion/challenger:** nowa kalibracja promowana **tylko** gdy bije obecnego championa na hold-oucie (kryteria §8, DM p<0.05); inaczej zostaje stara mapa. Wersjonowanie map + rollback.
- **Tryb CIEŃ:** narzędzie **NIE modyfikuje żywej ścieżki ETA**. Pisze predykcje cieniowe + raport. Wpięcie w produkcję = osobna decyzja właściciela (jak dziś `ENABLE_ETA_CELL_RESIDUAL_CORRECTION`).
- **Determinizm:** `Date.now`/random tylko z jawnego czasu batcha; te same dane → ta sama mapa.

## 10. Obiekty DB, cache, budżet, monitoring

- **Nowe obiekty (prefiks `eta_calib_`, rozłączne od Ziomka):**
  - `eta_calib_pickup_map.json`, `eta_calib_delivery_map.json` — mapy runtime (jak `eta_quantile_map`), lookup kwantyli per kubełek + offset kuriera.
  - `eta_calib_shadow.jsonl` — predykcje cieniowe (pred vs real per noga).
  - `eta_calib_metrics.jsonl` — dzienne metryki champion/challenger + CI.
  - `eta_calib.db` (SQLite) — feature store + backtest (opcjonalny; osobny plik, zero kolizji).
- **Cache pogoda/ruch:** jeśli dołożymy — jeden strzał na kubełek (strefa, godzina, data), wynik lokalnie; MVP bez.
- **Budżet + kill-switch:** ≤100 zapytań zewnętrznych/dobę (dziś 0 — brak API zewn.), twardy licznik + stop.
- **Monitoring/alerty:** dryft parametrów (kwarantanna §5), degradacja jakości (dzienny OOT MAE vs baseline z `eta_calib_metrics.jsonl`), pokrycie przedziałów poza pasmem → alert.

## 11. Ryzyka i otwarte kwestie

1. **Per-kurier prawdopodobnie nie da GO sam** (dowód 1.5). Narzędzie da GO na **de-biasie kontekstowym per-leg** — trzeba to jasno zakomunikować, bo literalne „per kurier" nie jest źródłem wartości. (Do potwierdzenia w STOP B1.)
2. **Krótkie okno** dla nogi odbioru z obciążeniem (`ziomek_pred` od 23.06, 15 dni) — probe solidny ale krótki; Rutcom (51 dni) potwierdza globalny de-bias, ale bez osi obciążenia. B2 połączy oba.
3. **`czas_kuriera` już wchłania kuriera** → residual per-kurier na odbiorze słaby. Alternatywa: bazować odbiór na pred_pickup silnika (nie na obietnicy), by odsłonić sygnał kuriera — do przetestowania w B2.
4. **Prawda per-leg cienka:** `gps_delivery_truth` n=1056 (dostawa, nie odbiór); brak geofence PRZYJAZDU pod restaurację → L1 timing mierzony pośrednio (`fleet_position_history` rekonstrukcja = kandydat B2).
5. **Kotwice/strefy** (ready vs pickup; naiwny-Warsaw vs UTC) — twarde testy jednostkowe w B2.
6. **Adopcja GPS floty ~1 kurier** — prawdziwa per-kurier×per-leg personalizacja (advisory W3.4) pozostaje zablokowana; narzędzie działa na tym, co jest, i honestly to raportuje.

---

### Pytania do decyzji na STOP B1
- **A.** Akceptujesz przeformułowanie: narzędzie = **kalibrator per-LEG (de-bias kontekstowy) z cienką warstwą per-kurier**, zamiast „czysto per-kurier"? (Dane mówią, że tak da się dać GO; czysto per-kurier — nie.)
- **B.** Baza nogi odbioru: korygować **obietnicę `czas_kuriera`** (prościej, −18% pewne) czy **pred_pickup silnika** (może odsłonić per-kurier, ryzyko)? Proponuję OBIE w B2 i wybór na dowodzie.
- **C.** Emisja obietnicy na **P80** (asymetryczny koszt) — zgoda co do kwantyla operacyjnego?
- **D.** Zakres MVP B2: reguły/empiryka najpierw (kwantyle + EB-shrinkage), LightGBM-quantile dopiero gdy pobije — zgoda?
