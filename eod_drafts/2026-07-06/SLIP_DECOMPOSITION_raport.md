# DEKOMPOZYCJA POŚLIZGU ODBIORU — A (dorzucanie) / B (kaskada) / C (prep) / reszta

**Data:** 2026-07-06 · **Analiza READ-ONLY** (zero zmian w silniku, zero restartów)
**Skrypty robocze:** `/tmp/claude-0/-root/3d6c12f0-3921-44cf-ab53-8463da867c1b/scratchpad/{explore1,decompose,decompose2,decompose3}.py`

---

## 1. Populacja, okno, jakość joinów

**Okno:** 27.06 – 06.07.2026 (~9,3 dnia — tyle sięga ledger `shadow_decisions.jsonl` + rotacja `.1`; pierwszy rekord 27.06 07:53 UTC).

**Definicje (zgodnie ze zleceniem):**
- **Obietnica** = pierwsza (w praktyce JEDYNA — dystrybucja PROPOSE/oid = {1: 2115}) decyzja `PROPOSE` per oid: `best.eta_pickup_hhmm` (HH:MM Warsaw dnia decyzji, rollover po północy obsłużony).
- **Poślizg** = `picked_up_at` (eta_calibration_log, Warsaw naive) − obietnica.

**Lejek populacji:**

| krok | n |
|---|---|
| eta_calibration_log, odbiór w oknie | 2168 |
| join z obietnicą PROPOSE | **2040 (94%)** — wszystkie 2040 mają też plan (`plan.pickup_at`) |
| − czasówki | −23 |
| − paczki (po nazwie restauracji) | −41 |
| − jechał INNY kurier niż w pierwszej propozycji | −1693 |
| − \|slip\|>120 | −0 |
| **POPULACJA** | **283** |

**Poślizg globalny populacji: mediana +6,5 min** (mean +9,4; p25 +1,5; p75 +15,2; p90 +27,3).
Różnica vs raportowane +8,2: inne okno i inny przyrząd populacji (tam prawdopodobnie match kuriera po świeżej predykcji z eta_cal `matched_courier=True` (1286 szt.), tu twardo „jechał kurier z PIERWSZEJ propozycji"). Kierunkowo to samo zjawisko.

**Jakość joinów / zastrzeżenia:**
- Pokrycie obietnicą: 94%. Pokrycie planem: 100% obietnic. Pokrycie GPS-geofence przyjazdu do restauracji (`restaurant_dwell.json`): tylko **8% populacji (n=22)** — komponent C liczony na podpróbce + szeroko (n=142 bez wymogu zgodności kuriera).
- ⚠ Populacja „jechał proponowany" to **14% dostaw okna** (283/2040) — selekcja: zlecenia, które zostały przy proponowanym kurierze, mogą być systematycznie „spokojniejsze". Wnioski o A/B/C dotyczą tej populacji.
- ⚠ `committed_iso` (czas_kuriera z pickup_lateness_shadow, tylko zlecenia spóźnione ≥5 min — próbka biasowana): mediana |różnicy| vs nasza obietnica = 6,0 min, tylko 24% ≤1 min → **realny kontrakt z restauracją bywa RE-UMÓWIONY po decyzji**. Analiza trzyma się definicji zlecenia (kontrakt = pierwsza propozycja).
- Drobiazg pomiarowy: `eta_pickup_hhmm` obcina sekundy → slip zawyżony o ~+0,5 min mediany. Pomijalne.

---

## 2. Tabela komponentów

**Metody:** A = stopy wykonane przez naszego kuriera w oknie (decyzja → nasz odbiór), których NIE było w planie decyzji (`plan.sequence`) i których odbiór nastąpił PO naszej decyzji. B = spóźnienie (real − plan) OSTATNIEGO planowego stopu (pickup/delivery z `plan.pickup_at`/`predicted_delivered_at`) przed naszym planowym odbiorem, actuals z eta_cal tego samego kuriera. C = dwell GPS (odbiór − przyjazd geofence) minus baseline handover.

| Komponent | Udział dotkniętych | Mediana slip: Z komponentem | Bez | Δ | Ile tłumaczy z mediany globalnej +6,5 |
|---|---|---|---|---|---|
| **A — dorzucenie stopów po decyzji** | **31%** (87/283; med 1 dorzucony, max 4) | **+14,0** | +4,7 | **+9,3** | **≈1,8 min (~28%)** — kontrfaktycznie: globalna mediana bez efektu A ≈ 4,7. W ogonie więcej: p75 15,2→10,6 (−4,6), p90 27,3→21,4 (−5,9) |
| **B — kaskada wcześniejszych stopów planu** | 57% ma wcześniejsze stopy; kaskada >2 min u 43% z nich (~24% populacji) | B 2–10 min: +5,9 · B>10 min: **−0,6 (!)** | +3,7 (B≤2) | ~0 | **≈0 min mediany** (korelacja B↔slip = 0,05). Gryzie TYLKO w ogonie, gdy kolejność utrzymana (niżej) |
| **C — prep / czekanie w restauracji** (pomiar bezpośredni, GPS) | dwell >5 min: **31%**; >10 min: 11% (szeroka próbka n=142) | — | — | — | **≈1 min mediany** (dwell med 3,1, handover-baseline 1,8 → C_med ≈ 0,6–1,3; C_mean ≈ 3) |
| **Reszta — optymizm obietnicy w chwili decyzji** | grupa czysta (A=0, bez wcześniejszych stopów): 30% populacji | **+7,3** (n=85) | — | — | **największy pojedynczy klocek bazy** (patrz niżej) |

**Nakładanie się komponentów (grupy rozłączne):**

| grupa | n | med slip |
|---|---|---|
| czysta (A=0, bez wcześniejszych stopów) | 85 | +7,3 |
| tylko wcześniejsze stopy (A=0) | 111 | **+3,0** |
| tylko A (bez wcześniejszych stopów) | 37 | **+17,1** |
| A + wcześniejsze stopy | 50 | +11,4 |

### Komponent A — twarde detale
- **Kontrola ekspozycji** (dłuższe okno obietnicy = więcej czasu na dorzucki — to NIE tłumaczy efektu; Δ utrzymuje się w każdym kuble): okno 0–15 min: A>0 med **+39,0** vs A=0 +7,2 (Δ+31,8) · okno 15–30: +15,4 vs +4,6 (Δ+10,8) · okno >30: +9,7 vs +3,2 (Δ+6,5).
- **Dorzucka z INNEJ restauracji (detour): med +14,8 (n=73) vs tylko z tej samej: +7,5 (n=14).**
- Najgorszy scenariusz: dorzucka przy krótkim oknie obietnicy (<15 min) — kontrakt praktycznie nie do dotrzymania.

### Komponent B — dlaczego ~zero w medianie
1. Obietnice głębiej w trasie mają większy slack (okno obietnicy med 28,5 min vs 19,0 dla 1. stopu) — kaskada się amortyzuje; stąd „tylko wcześniejsze stopy" med +3,0 (NIŻSZA niż czysta!).
2. **Resekwencja ratuje**: przy kaskadzie >10 min w **11/15** przypadków nasz odbiór ODBYŁ SIĘ PRZED spóźnionym stopem (system/koordynator przetasował trasę) → med slip tych zleceń −0,6.
3. B gryzie, gdy kolejność UTRZYMANA mimo kaskady >5 min: **n=23 (8% populacji), med slip +14,5** (vs resekwencja: n=15, med −3,2). To zjawisko ogonowe, nie medianowe.

### Komponent C — co się dało, a czego nie
- Bezpośrednio zmierzone czekanie kuriera W restauracji (GPS geofence): **dwell med 3,1 min** (w tym normalny handover ~1,8–2 min), p90 10,4. Na podpróbce z obietnicą (n=22): **mean slip 10,9 = mean przyjazd-po-obietnicy 5,7 + mean dwell 5,1** (dekompozycja addytywna, dokładna).
- **UCZCIWIE: C nie daje się w pełni oddzielić od „reszty".** Powody: (1) pokrycie GPS 8% populacji; (2) kurier wiedząc, że jedzenie niegotowe, może opóźnić przyjazd — wtedy prep ukrywa się w „spóźnionym dojeździe", nie w dwell; (3) faktycznego czasu gotowości jedzenia nikt nie loguje.
- Poszlaki, że prep siedzi w bazie obietnicy: **58% obietnic jest zakotwiczonych DOKŁADNIE na deklarowanym ready** (slack ≤1 min), a silnik sam szacuje `prep_bias_min` med **10 min** (deklarowane ready ~10 min za optymistyczne). Czysta grupa z kotwicą ready: med slip +5,6 — mieści się w znanym silnikowi niedoszacowaniu prep. Korelacja per-restauracja med_slip↔med_prep_bias słaba (0,12, n=11 restauracji) — prep_bias jako sam w sobie nie rankuje restauracji, ale poziom (7–17 min wszędzie) potwierdza systematyczny optymizm deklaracji ready.

### Reszta (baza) — optymizm obietnicy 1. stopu
Czysta grupa (nic nie dorzucono, brak wcześniejszych stopów, 95% z pustym workiem): med **+7,3**. Na GPS: to głównie SPÓŹNIONY PRZYJAZD (med ~6 min po obietnicy) z małym dwell (~2,4) — czyli obietnica „ready / teraz+dojazd" jest strukturalnie za ciasna (reakcja kuriera, dojazd, stale-GPS, realny prep — nierozdzielne bież. danymi). Kotwica ready: +5,6 (n=67) vs kotwica dojazdu: +10,9 (n=10, mała próbka). Optymizm rośnie z horyzontem: okno >30 min → med +17,5 (n=7).

---

## 3. Zwalidowane przykłady (ręcznie, surowe rekordy)

**① oid 484175 — Sweet Fit & Eat, kurier 492 (29.06) — czyste A.**
Decyzja 11:17, obietnica odbioru **11:41** (ready 11:32, worek 2, plan: 484161→484175→484167). Plan szedł dobrze (stop 484161 dowieziony 11:25 vs plan 11:27, kaskada −2,1). Po decyzji DORZUCONO oid 484155 (Nadajesz.pl — paczka), odebrany **11:41 — dokładnie w minucie naszej obietnicy**. GPS: przyjazd do restauracji 12:10 (**+30,0 po obietnicy**), dwell 1,3 min (jedzenie czekało), odbiór 12:12:15. **SLIP +31,2 = ~30 dojazd-po-dorzucce + ~1 handover. A wprost.**

**② oid 484460 — Pizzeria 105 Galeria Biała, kurier 179 (30.06) — B (kolejność utrzymana) + A.**
Decyzja 13:17, obietnica **13:36** (ready 13:32, worek 3, plan: 484458→484440→484460→484433). Wcześniejszy planowy stop 484458 (pickup Miejska Miska) plan 13:30 → real **14:08 (+38,3 kaskady)**, kolejność utrzymana; do tego dorzucone 484469 (ta sama restauracja, odebrane 14:23:55 razem z naszym 14:23:56). **SLIP +47,9 ≈ 38 kaskady + reszta.** Tak wygląda ogon B — bez resekwencji.

**③ oid 484403 — Grill Kebab, kurier 492 (30.06) — czyste C (prep).**
Decyzja 10:46, obietnica **11:01 = deklarowane ready** (worek 0, nic nie dorzucono, prep_bias 5,0). GPS: kurier w geofence restauracji już 10:33 (był na miejscu 27 min PRZED obietnicą), **dwell 37,1 min**, odbiór 11:10:38. **SLIP +9,6 = w całości czekanie na jedzenie po deklarowanym ready.** (Bonus — mediana bazy: oid 484170, Chicago Pizza: obietnica=ready 11:21, odbiór 11:28, slip +7,3, prep_bias 9,0 — typowa „czysta" dostawa.)

---

## 4. Werdykt — która śruba najpierw

**1. OCHRONA OBIECANEGO ODBIORU PRZY INSERCJI (A) — pierwsza śruba.** Największy pojedynczy, czysto przypisywalny efekt: 31% zleceń dostaje dorzutkę przed swoim odbiorem i płaci za to **+9,3 min mediany** (po kontroli ekspozycji +6,5…+31,8); tłumaczy ~28% globalnej mediany i ~połowę nadwyżki ogona (p75/p90). Konkret: feasibility dorzucki powinno traktować JUŻ OBIECANE odbiory w worku jako constraint (szczególnie: dorzucka z innej restauracji = med +14,8; oraz zakaz/kara dorzucania, gdy do obiecanego odbioru zostało <15 min — tam med sięga +39). To jest dokładnie „SOFT nie osłabia HARD": obietnica odbioru dziś NIE jest hardem w insercji.

**2. KALIBRACJA OBIETNICY NA KOTWICY READY (C-w-obietnicy / prep) — druga śruba, na MEDIANĘ bazy.** 58% obietnic = deklarowane ready, a silnik sam wie (prep_bias med 10 min), że deklaracje są optymistyczne; baza poślizgu (grupa czysta) +7,3/+5,6 med. Uwaga: NIE podnosić ślepo o cały prep_bias (slip med 5,6 < bias 10 — kurierzy częściowo to już absorbują); kalibrować buforem per segment — przyrząd JUŻ ISTNIEJE (`pickup_slip_monitor.jsonl` z `recommend_buffer_min`, segmentacja ciasno/średnio/luźno × solo/bundle; solo słabsze niż bundle, spójnie z tą analizą: 1. stop +7,3 vs głębszy +3,0).

**3. B (kaskada = jakość planu) — NIE ruszać jako pierwszej.** W medianie ~zero (korelacja 0,05): slack głębszych obietnic + resekwencja (11/15 dużych kaskad uratowanych) już działają. Zostaje tail-case „kolejność utrzymana mimo kaskady >5 min" (8% populacji, med +14,5) — to materiał na trigger repromise/alert (pickup_lateness_shadow już to widzi na żywo), nie na przebudowę plannera.

**Czego NIE dało się zmierzyć / co nie wyszło:**
- **C nie oddziela się uczciwie od „reszty"** dla 92% populacji (GPS-geofence przyjazdu tylko 8%; realny czas gotowości jedzenia nielogowany; kurier może strategicznie opóźniać przyjazd pod niegotowe jedzenie → prep maskuje się jako spóźniony dojazd). Mediana bazy +7,3 to łącznie: prep-po-deklarowanym-ready + reakcja/dojazd kuriera — bez rozbioru.
- Populacja „jechał proponowany kurier" to tylko 14% dostaw okna (silna selekcja); dla pozostałych 86% pierwsza propozycja nie jest wykonanym kontraktem (przydział innego kuriera / re-umówienie czasu — committed czas_kuriera ≠ obietnica w ~76% biasowanej próbki late).
- Ledger shadow sięga tylko 9,3 dnia (rotacja) — brak drugiego tygodnia na replikację.
- Delivery-geofence 5b (gps_arrived_at) — adopcja zbyt mała, nieużyteczne tu.

**Liczby kontrolne do replikacji:** populacja 283; mediana +6,5; A: 87 szt., Δmed +9,3; B>10: 15 szt., resekwencja 11; dwell szeroki n=142, med 3,1; grupa czysta n=85, med +7,3.
