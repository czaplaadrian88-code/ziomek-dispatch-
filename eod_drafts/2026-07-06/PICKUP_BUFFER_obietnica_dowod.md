# PICKUP-BUFFER — load-aware bufor OBIETNICY odbioru (zbudowane 2026-07-06, flaga OFF)

**Decyzja Adriana 06.07:** powierzchnia = OBIETNICA DECYZYJNA (opcje re-anchor i R6 odrzucone).
**Commity:** silnik `cd0bb25` (common.py + shadow_dispatcher.py + testy) · panel `fa27237` (coordinator-console, uśpiony fallback).

## Co robi
Przy propozycji silnik dolicza do OBIECYWANEGO czasu odbioru bufor per kubełek obciążenia × solo/worek
(semantyka 1:1 z `tools/pickup_slip_monitor`: luzno pf≥5 / srednio 2-4 / ciasno ≤1; solo = bag_after 1):

| cela | bufor (mediana 6d, review 04.07 n=1324) |
|---|---|
| ciasno solo | +25 | 
| ciasno worek | +13 |
| srednio solo | +24 |
| srednio worek | +12 |
| luzno solo | +17 |
| luzno worek | +7 |
Cap `PICKUP_BUFFER_MAX_MIN=30`. Brak danych (pf/bag None) → 0 = stara obietnica (fail-open).

**ADDYTYWNE pola** na serialized best: `pickup_buffer_min`, `eta_pickup_promised_utc`, `eta_pickup_promised_hhmm`
— obok starego `eta_pickup_hhmm`. **Wewnętrzne `eta_pickup_utc` NIETKNIĘTE** (scoring / feasibility HARD>60 /
R-LATE / extension_penalty bez zmian — wzorzec #8).

## Mapa kompletności
1. serializer best (`shadow_dispatcher._promised_pickup_fields`) — FIX ✓
2. panel `shadow_monitor.best_eta_pickup_hhmm` → 1-klik `time_arg` — FIX ✓ (uśpiony: preferuje promised gdy pole jest)
3. telegram_approver render — **N-D**: Telegram wyciszony 26.06 (nie wskrzeszać); przy ewentualnym re-enable C2 = pełny deploy, pola już są w decision dict
4. LOCATION A (kandydaci/alternatives) — **N-D**: obietnica dotyczy propozycji BEST (to ona jedzie w time_arg); ranking kandydatów = display
5. scoring/feasibility/R-LATE — **N-D celowo** (addytywność, wzorzec #8)
6. `pickup_slip_monitor` — bez zmian (mierzy wewnętrzną predykcję, nie obietnicę → baseline niezależny)

## Dowód (ETAP 5, replay okno 6d, n=1327)
- mediana błędu obietnicy per cela: **+7…+21 min → −3,9…+1,6** (obietnica przestaje systematycznie kłamać)
- `median|err|` **13,7 → 10,2 min (−26%)**, `mean|err|` 18,1 → 13,1 (−28%)
- NETTO per zlecenie: lepsza dla **914**, gorsza dla 413 (69/31) — cena centrowania mediany
- probe C14: mutacja bramki flagi zabita 2 testami; testy 6 + serializer-completeness + flag-registry zielone

## FLIP (za ACK Adriana; FLIPMASTER)
1. `flags.json`: `"ENABLE_LOAD_AWARE_PICKUP_BUFFER": true` (hot, silnik ≤tick)
2. `systemctl restart nadajesz-panel.service` (konsument fallbacku; poza peakiem)
3. Weryfikacja: `grep -c eta_pickup_promised_hhmm ../logs/shadow_decisions.jsonl` (świeże okno >0) + AI-HUB pokazuje późniejszy odbiór
4. Pomiar ON: offline join promised↔`picked_up_at` vs stary `eta_pickup` (pola w jsonl wystarczą)
⚠ Obserwacja 1. dnia: committed `czas_kuriera` będzie późniejszy → sprzężenie z R27 ±5 (soft) — sprawdzić,
czy TSP nie ODRACZA fizycznie możliwych odbiorów (jedzenie gotowe wcześniej); regres → rollback.
**Rollback:** flaga false (hot) — panel fallback wraca sam (brak pola).

---
# v2 (06.07 po południu) — DWIE KOREKTY ADRIANA (obowiązujące)
1. **„Lepiej żeby się spóźnił do 5 min, niż za ostrożnie i żeby czekał — każda minuta ważna"** →
   efektywny bufor = mediana − `PICKUP_BUFFER_LATE_TOLERANCE_MIN=5`.
2. **„To od wielu rzeczy zależy — punktualnemu nie doliczaj 25 min"** → tabela przeliczona TYLKO na
   populacji `matched_courier` (jechał TEN kurier, którego dotyczyła predykcja — obietnica jedzie w
   1-klik akcept TEGO kandydata) i **bez czasówek**. v1 była zawyżona rekordami z przydziałem INNEGO
   kuriera (med poślizgu 17-21 vs 8.6-11 dla matched).

**Tabela v2 (surowe mediany, matched-only, n=823; efektywnie po −5):**
ciasno-solo 16→**+11** (n<15, pożyczka od srednio-solo) · srednio-solo 16→**+11** · luzno-solo 8.5→**+3.5**
· ciasno-worek 11→**+6** · srednio-worek 12→**+7** · luzno-worek 7.5→**+2.5** (cap 30).

**Dowód v2 (populacja matched bez czasówek, n=821):** mediana spóźnienia vs obietnica **+9.1 → +5.0 min**
(dokładnie cel Adriana); spóźnieni >5 min: 64% → 50%; kurier przed obiecanym czasem: 20% → 33%
(koszt centrowania — ale bufor to teraz maks. +11, nie +25, więc czekanie krótkie); median|err| 10.8 → 9.4.
**Pre-flip check (nowy):** zweryfikować, że akcept czasówki w konsoli NIE bierze eta (keep-time) —
kalibracja czasówek wykluczona z tabeli.

---
# v3 KIERUNEK (Adrian 06.07 wieczór: „znaleźć wzór spóźnień") — ANALIZA 51 DNI, n=7381
Model kwantylowy LGBM (target=mediana err), walidacja CZASOWA (train 70% starszych dni, test 30% nowszych, n_test=1929):
- stała mediana: MAE 11.52 · **tabela worek×pora: MAE 11.42 (ZERO zysku — tabele odpadają)** · **LGBM 8 zmiennych: MAE 9.54 (−17%)**
- WAŻNOŚĆ: `pred_age` (wiek predykcji) ≈ `pred_min` (długość przewidywanej trasy) ≈ `r6max` (napięcie R6 worka) ≫ godzina/dzień tyg. > tier > bag ≫ weekend≈0.
  → prawdziwy sygnał = CIĄGŁE cechy KONKRETNEJ decyzji (wszystkie znane w chwili propozycji), nie kubełki.
- Jednowymiarowo: solo×rano/lunch med 12-13 vs solo×wieczór 4.7-6 (interakcja!); tier std/new ~+4 vs std+/gold.
- PER-KURIER: po odjęciu modelu zostają osobowe efekty ±3-8 min (508 −8.2 szybszy; 515 +4.2, 413 +3.1 wolniejsi) → korekta per-cid ze shrinkage jako składnik v3.
- Skrypt: scratchpad `slip_model_analysis.py` (sesja 06.07). ⚠ target = błąd DOSTAWY (proxy poślizgu odbioru per dekompozycja 29.06); w v3 przejść na bezpośredni poślizg ODBIORU (`picked_up_at` jest w logu).
**Propozycja v3:** nightly-trenowany model medianowy (jak kalibracje ETA) → silnik liczy bufor per DECYZJA: `clamp(pred_model − 5, 0, 30)` (zasada „do 5 min" zostaje); v2-tabela = tylko fallback gdy modelu brak. Osobny sprint protokołem za GO.

---
# v3 WERDYKT KOŃCOWY (06.07 wieczór) — „wzór spóźnień" z cech DECYZJI NIE ISTNIEJE w tych danych
- Wcześniejsze −17% MAE = ILUZJA Z PRZECIEKU: `pred_age` (wiek predykcji) znany dopiero PO fakcie; w chwili obietnicy zawsze 0. Po usunięciu: cechy decyzji (bag/hour/weekday/tier/r6max/total_dur/pred_min + offsety kurierów train-only) dają OOS **MAE 11,32 vs stała 11,52 (−1,7%)**.
- Target ODBIÓR (join eta_pickup_hhmm↔picked_up_at, n=1263, 30d): med +8,2 (p25 +2,5 / p75 +15,5); model **GORSZY od stałej OOS (9,69 vs 9,50)**.
- WNIOSEK: poślizg powstaje PO decyzji (dorzucone zlecenia, kolejki, prep) — z punktu propozycji jest ~szumem wokół +8 min. Różnice tier/pora (±3-4 od jednowymiarówek) NIE generalizują OOS ponad stałą.
- REKOMENDACJE: (1) obietnica statyczna = prosty bufor wg zasady Adriana (mediana odbioru 8,2 − 5 ≈ **+3 min stałe**, ew. v2-tabela jako nieszkodliwa); (2) PRAWDZIWA poprawa = OBIETNICA DYNAMICZNA — aktualizacja umówionego czasu, gdy PO decyzji zmienia się plan kuriera (dorzucony worek/ruszył w trasę) = osobny sprint mechanizmu, nie wzoru; (3) infrastruktura v3 (`tools/pickup_slip_model.py`: nightly-trainer, LGBM kwantylowy, offsety per-kurier ze shrinkage — NOWY KURIER wchodzi automatycznie, offset uczy się sam) ZOSTAJE w repo gotowa na moment, gdy dojdą cechy z sygnałem (np. telemetria stanu kuriera przy decyzji); nocny timer ŚWIADOMIE NIE zainstalowany (nie stawiamy maszynerii dla −1,7%).
