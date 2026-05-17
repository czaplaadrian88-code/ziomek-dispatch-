# Pełna diagnoza — order 474266, dlaczego trasa Borsucza-przed-Młynową

**Data:** 2026-05-17 · **Order:** 474266 Pani Pierożek → Aleja Piłsudskiego ·
**Kurier:** 518 Michał Ro (tier std) · **Decyzja:** 17:47:53 Warsaw (15:47:53 UTC)

## Stan wejściowy
- Bag 474235 Borsucza 10/33 (_500 stopni, ck 17:41) — **PICKED UP** (jedzenie w torbie)
- Bag 474239 Młynowa 70/11 (Sioux, ck 17:49) — **PENDING** (jeszcze nie odebrane)
- Wynik: `sequence=[Borsucza, Młynowa, Aleja]`, `strategy=ortools_rejected_v3274`

## Łańcuch przyczynowy (6 warstw)

**L1 — pozycja kuriera = proxy `last_picked_up_delivery`.**
Brak GPS → courier_resolver ustawia syntetyczną pozycję = drop ostatniego
odebranego ordera = **Borsucza 10/33** (peryferia). Realnie kurier jest w trasie,
~2 min od Siouxa — model sądzi że stoi pod Borsuczą.

**L2 — okno frozen Siouxa.** ck 17:49 → open=(17:49−17:47:53)≈1.1 min →
okno CumulVar **[0, 6.1] min** (V3274_FROZEN_PICKUP_WINDOW_MIN=5).

**L3 — macierz czasu z DWELL.** `ENABLE_V328_TIME_MATRIX_DWELL=1`:
time_matrix[i][j] = jazda(i→j) + DWELL(j). Std tier DWELL=3.5. CumulVar solvera
w węźle = przyjazd + obsługa = czas ODJAZDU; okno [0,6.1] to okno PRZYJAZDU →
systematyczny bias +3.5 min na każdym pickupie.

**L4 — windowed solve INFEASIBLE.** CumulVar_Sioux = jazda(courier→Sioux)+3.5.
Z proxy L1 jazda Borsucza→Sioux ≈ 8 min → 8+3.5=11.5 ≫ 6.1 → brak rozwiązania
(`OR-Tools INFEASIBLE z time windows`). Nawet bez DWELL 8>6.1 → infeasible;
pozycja-proxy to główny winowajca, DWELL dokłada.

**L5 — retry bez okien + V3274 reject = pętla sabotażowa.** Kod robi retry bez
okien ("gorsza sekwencja > brak proposal"). Bez okien solver minimalizuje sam
dystans → pickup Siouxa ląduje +41 min. Assercja V3.27.4 widzi Sioux poza ±5 →
odrzuca CAŁY plan OR-Tools. Retry i V3274 znoszą się wzajemnie.
Log: `V3274_OR_TOOLS_VIOLATION reject violations=[('474239', 41.39, 6.13)]`.

**L6 — greedy `lock_first`.** Fallback `_greedy_plan`: jedyny odebrany order =
Borsucza → `lock_first_picked` przypina ją na stop #1. Sekwencja
[Borsucza, Sioux-pickup, Młynowa, …] wymuszona, geometrycznie ślepa.

## Skala — to nie incydent
Dziś (`route_simulator.log`): 7 537× INFEASIBLE windowed, 9 209× V3274 reject,
2 233 propozycji `strategy=ortools_rejected_v3274`. Identyczny wolumen codziennie
od ≥09.05. **OR-Tools de facto wyłączony na całej flocie >10 dni** — trasy robi
greedy fallback.

## Ocena 2 dyrektyw Adriana

**D1 — pickup DWELL=1 min flat, dropoff dłuższy.** Trafne: GPS zmierzył ~3.7 min
postoju, ale to miesza OBSŁUGĘ (~1 min) z CZEKANIEM na jedzenie (osobny
mechanizm: pickup_ready_at). Skutek uboczny: zmniejsza bias L3. Implementacja:
`dwell_for_tier` → `(1.0, d)` zamiast `(d,d)`. Uwaga: DWELL idzie też do ETA —
pickup ETA stanie się ~2.5 min optymistyczniejsze (pętla ucząca złapie).

**D2 — OR-Tools: stopy restauracyjne sztywne, reszta optymalizowana.** Właściwy
kierunek (model koordynatora). Dziś to "implementowane" oknem ±5 + reject — i to
się sypie. "Sztywne" zrobić jako: CumulVar pickupów = ck (twarda kotwica), bez
blokowania feasibility przez reachability; albo pickupy fixed po ck-ascending a
OR-Tools wstawia tylko dostawy. Korekta zasady: "czasy nietykalne" ≠ "plan do
kosza gdy kurier nie trafi w ±5" — kotwica ma USTAWIAĆ trasę, nie ją kasować.

## Plan fixu (do ACK)
- E1 — pickup DWELL 1 min: patch `dwell_for_tier`. Mały.
- E2 — usunąć ścieżkę V3274-reject→greedy (plan OR-Tools zostaje, odchył logowany).
- E3 — pickupy restauracyjne jako sztywne kotwice (CumulVar=ck), reachability nie
  blokuje feasibility. Root-cause INFEASIBLE.
- E4 — pozycja kuriera: proxy `last_picked_up_delivery` przekłamuje; lepszy proxy.
- E5 — greedy fallback anchor-aware (safety net).
