# ROADMAPA „po naprawach poaudytowych" — wdrożenie rekomendacji z analizy deep-dive

**Data:** 2026-07-02 · **Bazuje na:** `ANALIZA_DEEPDIVE_DOKUMENTY_REKOMENDACJE.md` (ten sam katalog) + decyzja Adriana 02.07: „czasy raz zbyt optymistyczne, raz pesymistyczne, ostatnio w miarę ok — trzeba DOKŁADNIE zmierzyć, per kurier, tier, bundle/solo".
**Zasada nadrzędna:** NIC nie flipujemy na starych liczbach. Bias −18 min z analizy 02.07 był liczony na n=75 i oknie głównie sprzed 26.06 — a baza ETA się od tego czasu zmieniała (uplift ruchu ~23.06, fix osrm-double-traffic 28.06, rekalibracja DWELL). Pomiar z Kroku 0 jest BRAMKĄ dla całej Fali A: jeśli pokaże, że czasy są już OK, Falę A zamykamy jako „no-op, nie ruszać" — to też jest wynik.
**Rytm każdego punktu:** protokół #0 (ETAP 0→7) + „przed każdym tematem: zweryfikuj stan → udowodnij pomiarem że warto" + prosty polski opis przed kodem + ACK na flipy.

---

## KROK 0 — można ZARAZ, równolegle do napraw (wszystko READ-ONLY, zero dotykania silnika)

### 0a. POMIAR CZASÓW 2.0 — segmentowana prawda o ETA (bramka Fali A) ⭐ najważniejszy punkt roadmapy
**Po co (po ludzku):** dziś wiemy, że czasy „bywały złe w obie strony", ale nie wiemy GDZIE. Jedna średnia (−18 min) to za mało — optymizm u jednego kuriera w worku 3-zleceniowym w peak może się znosić z pesymizmem solo poza szczytem i w średniej wyjdzie „w miarę ok", a naprawdę oba segmenty są złe. Potrzebujemy mapy: bias + rozrzut osobno per segment.

**Wymiary segmentacji (minimum):**
- **kurier** (cid) — czy poszczególni kierowcy systematycznie szybsi/wolniejsi niż model zakłada
- **tier kuriera** (gold/std/slow — KLASA, nie poziom eskalacji)
- **solo vs bundle**, a w bundlach **bag_size** (2 vs 3+) i pozycja stopu w worku (1. vs ostatni)
- **noga trasy osobno**: dojazd-po-odbiór vs jazda-z-jedzeniem vs dwell dostawy (kalibracja 29.06 pokazała, że błąd siedzi w poślizgu ODBIORU, nie w jeździe — sprawdzić, czy po fixach nadal)
- **obciążenie floty** (pool_feasible / liczba aktywnych worków — segmentacja „po obciążeniu, nie po porze", wniosek z 29.06)
- **restauracja** (prep bias — zasila też P6)
- **pora dnia / peak** (kontrolnie, spodziewamy się że load to wyjaśnia)

**Co mierzymy w każdym segmencie:** mediana błędu (bias, znak!), p10/p90 (rozrzut), n. Wynik = tabela + werdykt per segment: OK / optymizm / pesymizm.

**Jak (etapy):**
1. **Recon logowania (0,5 dnia):** czy `eta_calibration_log.jsonl` + shadow ledger (po L1.1 serializer klucze `eta_source`/detale docierają) + `gps_delivery_truth.jsonl` + `restaurant_dwell.json` wystarczą do joinu committed-ETA↔ATA per noga i per segment. Sprawdzić confidence truth i pokrycie okna PO 28.06.
2. **Jeśli brakuje danych:** dołożyć logging-only zapis „obiecanego czasu w momencie przydziału" (committed ETA per zlecenie per noga) — mały, za flagą, nic nie zmienia w decyzjach. To jedyny element Kroku 0, który dotyka kodu silnika (append-only log) → mini-protokół.
3. **Zbierać ≥7-14 dni na świeżym oknie** (po deployach FALA-1 i restartach, żeby mierzyć obecny silnik, nie historyczny).
4. **Raport + werdykt** — to jest wejście do Fali A. Narzędzie ma być POWTARZALNE (tools/, jak eta_error_report), nie jednorazowy skrypt — bo czasy będą się zmieniać dalej i chcemy tę mapę odświeżać po każdej większej zmianie.

**Uwaga na własny przyrząd:** lekcja z entropy-dashboardu — przyrząd też podlega regule anty-kłamstwa. Werdykt-tool z bajt-parytetem do kanonu ledger_io, timestampy przez parse_sla_ts (świeża mina TZ), jawne n per segment, segmenty n<20 oznaczone „za mało danych" zamiast liczby.

### 0b. Lower bound LAP — „ile tracimy na greedy" (P4)
Replay historycznego dnia → optymalne przypisanie kurier↔zlecenie (scipy `linear_sum_assignment`, pula mediana 3, trywialne) vs to co zrobił greedy+lex. Twarda liczba w minutach SLA / kursach dziennie. Jeśli mała → temat globalnej optymalizacji zamykamy na lata; jeśli duża → wiemy o co gramy w Fali C. Read-only, zero ryzyka.

### 0c. Metryka churn propozycji do shadow (przygotowanie P2)
Sam POMIAR migotania jako stały monitor (dziś liczba 83% jest z reassignment_shadow ad-hoc): ile razy top-1 proponowany kurier zmienia się między tickami, per zlecenie, per przyczyna (zmiana stanu floty vs czysty przelicz). Baseline PRZED histerezą = bez tego nie udowodnimy, że histereza pomaga.

---

## FALA A — „czasy mówią prawdę" (start: po naprawach + po werdykcie 0a)

**A1. Kalibracja ETA wg mapy z 0a** (dawne P1, ale szersze):
- jeśli 0a pokaże optymizm skoncentrowany w segmencie load-high/bundle → flip load-aware bufora (mechanika shadow gotowa: `_compute_loadaware_shadow` + pickup_slip_monitor), skalibrowanego per segment, nie jedną stałą;
- jeśli pokaże pesymizm gdzieś (np. solo poza peak — „raz pesymistyczne") → korekta W DÓŁ tam, bo pesymizm też kosztuje (feasibility za ostra → 14% zleceń bez wykonalnego kuriera → KOORD, scarcity się nakręca);
- jeśli pokaże „wszędzie OK" → zamykamy jako no-op z werdyktem i liczbami, zostaje sam monitoring driftu.
- Rollout: shadow ON≠OFF → replay „warto + bez regresji" (SLA, wolumen KOORD, R6 breach) → ACK → flip → okno 2 dni.

**A2. Prep-bias per restauracja (P6):** flip `ENABLE_PREP_BIAS_TABLE` dla restauracji z |bias| dużym i n wystarczającym (progi z 0a). Reszta zostaje na flat.

**A3. Drift-monitor czasów na stałe:** raport 0a jako cotygodniowy verdict-job (wpis do rejestru at-jobów) — „czasy się rozjechały w segmencie X" ma wyskakiwać samo, a nie czekać na następny audyt.

## FALA B — „decyzje przestają migotać" (po A, przed autonomią)

**B1. Histereza + koszt zmiany kuriera w scoringu (P2):** nie zmieniaj propozycji, jeśli nowa nie jest lepsza o próg; próg skalibrowany replayem na danych z 0c. Pilnować: nie zamrażać ewidentnie lepszych zmian (konflikt z inwersjami P-1..P-7 → tabela rozstrzygania kanonu; wątpliwość = pytanie do Adriana). Cel na start: churn z ~83% do <20% bez pogorszenia SLA w replayu.
**B2. Werdykt-bramka autonomii:** po B1 przeliczyć od nowa plaster auto-assign (kalibracja 30.06 dawała ~12% wol., breach 2,5% vs 9% u ludzi) — stabilne propozycje najpewniej POWIĘKSZĄ bezpieczny plaster.

## FALA C — „oszczędzamy kursy" (po B; wymaga werdyktu zasady od Adriana)

**C1. Delay-dispatch okienkowy par (P3):** hold 60–120 s dla zlecenia z restauracji X, gdy świeża/prawdopodobna para z X, TYLKO gdy margines SLA pozwala. Potencjał ~5-10 odzyskanych bundli/dzień (pomiar 02.07: 37% par ≤6 min jedzie różnymi kurierami). **Najpierw werdykt Adriana co do samej zasady „wolno chwilę przytrzymać zlecenie"** (styk z R-DECLARED-TIME). Mechanika: domknięcie `pending_global_resweep` do live w wąskim oknie (⛔ dziś NIE flipować — zakaz w todo aktualny do czasu tej fali).
**C2. Variance penalty w scoringu SOFT (P5):** kara za niepewność (dane o wariancji per segment już będą z 0a). Dopiero po A — najpierw prostuje się średnią, potem karze rozrzut.
**C3. (warunkowo) Globalna selekcja okienkowa** — TYLKO jeśli 0b pokaże istotną stratę greedy. Inaczej skreślamy.

## FALA D — „domknięcie pętli" (tanie, na koniec)

**D1. Error-budget/burn-rate SLA (P7):** jeden agregat na istniejących jsonl + reguła „budżet spalony → freeze zmian silnika".
**D2. Dashboard 4 sygnałów w AI-HUB (P8):** stabilność decyzji (0c/B1) · zdrowie ETA (A3) · infra (perf-SLO p95) · ekonomia (D1).

---

## Kolejność i zależności (jedno spojrzenie)

```
TERAZ (równolegle do napraw):  0a pomiar czasów  ·  0b LAP lower-bound  ·  0c churn baseline
                                    │ (bramka: werdykt z liczbami)
PO NAPRAWACH:      FALA A: A1 kalibracja ETA → A2 prep-bias → A3 drift-monitor
                                    │
                   FALA B: B1 histereza (baseline z 0c) → B2 re-kalibracja plastra autonomii
                                    │
                   FALA C: C1 delay-dispatch (werdykt Adriana!) · C2 variance penalty · C3 tylko-jeśli-0b-każe
                                    │
                   FALA D: D1 error-budget · D2 dashboard
```

Zależności twarde: A1/A2/C2 po L3/L5 (warstwy ETA/feasibility z Fazy 3); B1/C1/C3 po L6 (scoring/selekcja); 0a-0c nie kolidują z niczym (read-only / logging-only). Każdy flip: osobny ACK, rollback = flaga OFF + restart.

## Co świadomie POZA roadmapą
Pełny MIP floty, AWS-scaling, własny graf edge-based, transactional outbox pełny, p(akceptacji) kuriera, bayesian tuning — uzasadnienie w `ANALIZA_DEEPDIVE_DOKUMENTY_REKOMENDACJE.md` §6.
