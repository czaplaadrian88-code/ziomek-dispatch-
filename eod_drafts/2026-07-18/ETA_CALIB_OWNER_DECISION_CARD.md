# KARTA DECYZYJNA — wpięcie kalibratora ETA per-kurier (dla Adriana)

**Po co ta karta.** 18.07 dałeś GO „Dawaj flip kalibratora per-kurier". Flip jest dziś
zablokowany DWIEMA bramami, których nie wolno mi ominąć: (1) fail-closed gate promocji
(champion = artefakt legacy sprzed remediacji; kontrakt: „nigdy bootstrap GO" — deadlock
do rozcięcia wyłącznie jawną decyzją), (2) brama właścicielska ze Sprintu 3 / OD-01..03:
definicje eventów i progi KPI, które może związać tylko właściciel. Poniżej 6 decyzji —
każda z opcjami i moją rekomendacją. Odpowiedź w stylu „D1a, D2a, D3b…" wystarcza,
żeby następna sesja wykonała wpięcie pełnym protokołem w ~1 dzień + cień.

**Uczciwy stan liczb (żeby decyzje miały grunt):**
- Historyczne „−52%/−20%" z 07.07 = **WYCOFANE** przez audyt A360-A0 (model podglądał
  wynik: godzina/obciążenie odtwarzane z faktycznego odbioru; porównania na różnych
  holdoutach). Nie są dowodem.
- **Świeże, leak-free (nocny gate 18.07, cechy tylko decision-time):** odbiór MAE
  **5,14 min vs silnik 11,21 (−54%)**, vs koordynator 6,55 (−21%), vs naiwny 5,29 (−3%);
  dostawa **7,38 vs silnik 8,92 (−17%)**, vs naiwny 8,88 (−17%); ONTIME operacyjny
  84-87% przy targecie 80%; holdouty ~3,1k (odbiór) / ~2,5k (dostawa) nóg.
- **Pokrycie PRAWDY (pipeline Z-P1-02, uczciwy GPS):** last-inside 99/188, arrival
  86/188, **complete-case obu nóg 41%** — prawda-GPS jest jeszcze dziurawa; kliki to
  proxy. To jest realne ograniczenie każdego progu KPI.

---

## D1. Fizyczny event ODBIORU (possession) — co uznajemy za „kurier MA jedzenie"?
- **a) (REKOMENDUJĘ na start)** `restaurant_last_inside_at` z geofence GPS jako
  **proxy possession** z jawną etykietą proxy; klik „odebrane" tylko fallback z niższą
  rangą; żadnej promocji KPI na klikach.
- b) Czekać na twardszy event (np. potwierdzenie w apce przy odbiorze) — odsuwa
  wpięcie o osobny sprint apki.
- Dlaczego a): OD-01 pozwala na proxy, jeśli jest NAZWANE proxy; last-inside ma już
  kontrakt, hash i lineage ze Sprintu 3.

## D2. Event DOSTAWY — arrival czy handoff?
- **a) (REKOMENDUJĘ)** `delivery_arrival_at` (przyjazd GPS pod adres) jako kotwica
  KPI **arrival**; handoff pozostaje OSOBNYM, niezwiązanym KPI (OD-02) do czasu
  twardszego sygnału.
- b) Klik „dostarczone" jako handoff-proxy — odradzam do KPI (klik-lag,
  batch-kliki znane z pomiarów).

## D3. KOTWICA predykcji — którą predykcję oceniamy?
- **a) (REKOMENDUJĘ)** predykcja istniejąca **w momencie decyzji assignment**
  (kontrakt `eta_truth.dataset.v1`, bez fallbacku do rekordów po przypisaniu).
- b) Ostatnia predykcja przed eventem — łatwiejsza liczba, ale mierzy co innego
  (odświeżanie, nie obietnicę); nie nadaje się na KPI obietnicy.

## D4. Polityka unknown-package + MINIMALNE coverage
- Paczki: tylko `is_paczka_order(address_id)`; **unknown NIE jest zgadywane** —
  wypada z mianownika i raportujemy jego udział (dziś ~3%).
- **Minimalne coverage do ZALICZENIA komórki KPI (event×source×cohort):** proponuję
  **≥60% complete-case w komórce i n≥200**, poniżej = komórka `HOLD` (OD-03
  fail-closed). Dziś complete-case=41% → pierwsze tygodnie część komórek będzie
  uczciwie HOLD — to akceptowalne i widoczne, nie maskowane. **Zatwierdź lub podaj
  własne progi.**

## D5. PROGI KPI dla flipu obietnic (pierwszy konsument — patrz D6)
Propozycja (konserwatywna, wszystkie muszą przejść na leak-free holdoucie
i na oknie cienia):
- odbiór: MAE ≤ 6,0 min ORAZ poprawa vs baseline silnika ≥25%;
- dostawa: MAE ≤ 8,0 min ORAZ poprawa ≥10%;
- spóźnienia (obietnica P80): 15-22% (target 20%, pasmo tolerancji);
- bias mediany |Δ| ≤ 1,5 min; ogon p90 błędu ≤ 20 min; min n jak w D4;
- outliery: winsoryzacja raportowa na p99, bez wycinania z mianownika.
**Zatwierdź / skoryguj liczby.**

## D6. ZAKRES pierwszego wpięcia (najmniejszy bezpieczny krok)
- **a) (REKOMENDUJĘ)** Tylko **warstwa OBIETNIC/prezentacji**: kalibrowane P80 zasila
  proponowany czas odbioru w konsoli (tam gdzie dziś liczy silnik) + ETA pokazywane
  w apce/konsoli. **Feasibility/R6/scoring NIETKNIĘTE** (decyzje liczą jak dotąd).
  Za flagą, hot-rollback, cień 2 dni z parytetem starego vs nowego na tych samych
  zleceniach.
- b) Od razu także do scoringu/feasibility — odradzam: to zmiana decyzji o dużym
  promieniu; najpierw 2-4 tyg. obietnic + pomiar override/parytetu.

## D7. Rozcięcie deadlocka championa (techniczne, wymaga Twojego podpisu)
Kontrakt promocji słusznie zabrania cichego bootstrapu. Proponuję **jednorazowe,
jawne ustanowienie pierwszego championa v2** = dzisiejszy kandydat leak-free
(sha256 w `eta_calib_metrics.jsonl` z 18.07), z wpisem provenance „bootstrap za
zgodą ownera DD.MM" w artefakcie; od jutra gate działa już normalnie
(challenger vs v2-champion, fail-closed).

---

**Po Twoich odpowiedziach:** sesja wykonuje (pełny #0): związanie kontraktów D1-D3 →
konfiguracja progów D4-D5 → bootstrap D7 → implementacja konsumenta D6a za flagą →
replay + 2 dni cienia z parytetem → flip za końcowym ACK. Rollback na każdym kroku hot.
