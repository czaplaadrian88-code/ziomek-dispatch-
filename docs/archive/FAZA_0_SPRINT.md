# ZIOMEK V3 — Sprint Plan po audytach (FAZA 0)
**Data:** 12.04.2026
**Źródło:** 4 audyty zewnętrzne (Gemini ×2, DeepSeek ×2) + weryfikacja Claude na kodzie
**Status:** Gotowe do wdrożenia jutro — nie rusza się dzisiaj

---

## 0. CO SIĘ WYDARZYŁO (kontekst sesji 12.04)

Po napisaniu SKILL.md V3 (26 reguł biznesowych + 12 faz), przesłaliśmy projekt do audytu zewnętrznego. Cztery opinie:

| Opinia | Model | Skope | Ocena | Wartość |
|--------|-------|-------|-------|---------|
| #1 | Gemini | SKILL.md + screeny panelu | 7/10 | 4 realne zarzuty, 2 halucynacje |
| #2 | DeepSeek | SKILL.md + screeny panelu | 6/10 | Dobre strategiczne, słabe technicznie |
| #3 | Gemini | SKILL.md + 5 plików kodu | **9/10** | Profesjonalny code review, 8 realnych bugów |
| #4 | DeepSeek | SKILL.md + 5 plików kodu | 6.5/10 | 2 genialne wyłapania, 3 halucynacje |

**Rezultat:** wyłapanych **8 realnych bugów** w obecnym kodzie, które muszą być naprawione przed uruchomieniem shadow dispatcher.

---

## 1. DECYZJE BIZNESOWE PODJĘTE DZISIAJ

### D13 — Premium SLA odroczone o 6 miesięcy

Pierwotna decyzja D5 mówiła: per-order SLA (standard 35 / premium 20 / premium+ 15) od dnia 1. **ZMIANA:**

- **Do października 2026:** jednolite SLA 35 min dla wszystkich orderów
- **Październik 2026+:** wprowadzamy premium tier jako feature sprzedażowy dla nowych restauracji
- **Powód:** Obecnie nie mamy podpisanych umów premium. Żadna restauracja nie płaci za 20-min gwarancję. Implementacja premium dziś = martwy kod. Robimy gdy będzie first customer.
- **Konsekwencja dla kodu:** `state_machine.py` zostaje z hardcoded 35 min (nie jest to już bug, jest zamierzone). `sla_minutes` w OrderSim zostaje przygotowane w modelu, ale wypełniane zawsze 35.

### D14 — Faza 0 przed Fazą 1

Pierwotnie planowaliśmy skakać z dokumentacją prosto w Fazę 1 (fix courier_resolver + route_simulator_v2). **ZMIANA:**

- **Faza 0** = 1.5 dnia pracy na 8 krytycznych fixów wyłapanych przez audyt
- **Faza 1** startuje dopiero po Fazie 0 ukończonej i zweryfikowanej
- **Powód:** Uruchomienie shadow z 6 realnymi bugami = seria wyjaśnień "czemu Ziomek źle zaproponował" w pierwszym tygodniu. Lepiej przesunąć start o 2 dni i mieć solidny fundament.

---

## 2. FAZA 0 — LISTA PATCHÓW (kolejność wykonania)

### Quick wins (łącznie ~1h, robić jako pierwsze)

**P0.1 — Ujednolicenie MAX_BAG_SIZE**
- **Bug:** `feasibility.py` ma `MAX_BAG_SIZE = 6`, `scoring.py` ma `MAX_BAG_SIZE = 4`. Scoring karze na 4, feasibility przepuszcza do 6 → niespójność
- **Źródło wykrycia:** Audyt #4 (DeepSeek code review)
- **Fix:** Centralny config w `common.py`: `MAX_BAG_SIZE = 6`. Import w obu plikach
- **Estymacja:** 5 min
- **Test:** `grep MAX_BAG_SIZE dispatch_v2/` pokazuje 1 definicję i 2 importy

**P0.2 — Fix scoring.py `time_penalty` dla assigned**
- **Bug:** Obecny `s_czas` używa `oldest_in_bag_min` bez rozróżnienia statusu. D4 mówi: assigned → 0 kary. Kod karze kuriera za order którego jeszcze nie ma w torbie.
- **Źródło wykrycia:** Audyt #3 (Gemini code review punkt #5)
- **Fix:** Parametr `bag_statuses: List[str]` w `score_candidate`. Liczyć `oldest_in_bag_min` tylko dla orderów z `status == "picked_up"`.
- **Estymacja:** 15 min
- **Test:** scenariusz z kurierem mającym 1 assigned (nowy propozycja) → time_penalty == 0

**P0.3 — Fix `courier_resolver.py` priority bug**
- **Bug:** Gdy kurier ma aktywny bag + wcześniejsze delivered, fallback bierze `last_delivered` zamiast pozycji aktywnego baga
- **Źródło wykrycia:** Planowane wcześniej + audyt #3 (punkt #3)
- **Fix:** W `build_fleet_snapshot` zmienić kolejność: (1) GPS fresh, (2) pozycja aktywnego baga (picked_up delivery lub assigned pickup), (3) dopiero potem last_delivered
- **Estymacja:** 20 min
- **Test:** kurier z bagiem 4 powinien mieć `pos_source: last_picked_up_delivery`, nie `last_delivered`

**P0.4 — Fix `panel_watcher` pickup_coords null**
- **Bug:** Wiele NEW_ORDER eventów emituje z `pickup_coords: null`. Shadow dostaje None i odrzuca zamówienie silent.
- **Źródło wykrycia:** Audyt #3 (punkt #8) — potwierdzone w `orders_state.json`
- **Fix:** Przed emit NEW_ORDER sprawdzić czy coords są. Jeśli nie — pobrać z `restaurant_coords.json` przez address_id. Jeśli brak i address_id → opóźnić event o 1 cykl (20s) na backfill.
- **Estymacja:** 25 min
- **Test:** Przez 1h produkcji zero NEW_ORDER z pickup_coords: null

### Core fixes (łącznie ~2h)

**P0.5 — OSRM fallback haversine**
- **Bug:** Brak fallback — OSRM down = Ziomek offline
- **Źródło wykrycia:** Audyty #1, #2, #4 wszystkie zgodnie
- **Fix:** W `osrm_client.table()`, gdy primary timeout (>3s) albo 5xx response → fallback `haversine × 1.4 / 25 km/h`. Flaga `osrm_fallback: true` w response dla monitoring.
- **Estymacja:** 40 min
- **Test:** Mock OSRM timeout → table() zwraca wartości z fallback + flaga

**P0.6 — Weryfikacja `prep_ready_at` w panelu**
- **Pytanie:** czy panel zwraca dokładny timestamp oczekiwanego odbioru, czy tylko minuty?
- **Źródło wykrycia:** Audyty #1 i #3 oba pytają
- **Task:** SSH na serwer, wywołać `fetch_order_details` na 3 realnych orderach (1 zwykły, 1 czasówka 60+, 1 peryferyjny). Sprawdzić co wraca w `zlecenie` dict.
- **Estymacja:** 30 min recon
- **Rezultat:** dokumentacja w TECH_DEBT co panel faktycznie daje — jeśli mamy `czas_odbioru_timestamp` to D3 jest implementowalne jutro; jeśli tylko minuty, musimy policzyć `prep_ready_at = created_at + czas_odbioru_min`.

**P0.7 — `gap_fill_restaurant_meta.py` skrypt historical analysis**
- **Cel:** uzupełnić brakujące 26 restauracji w `restaurant_meta.json` z danych historycznych
- **Źródło wykrycia:** Audyty #1, #3, #4 wszystkie zgodnie
- **Input:** `orders_state.json` + `sla_log.jsonl` (jeśli wypełniony) + ewentualnie CSV z panelu
- **Algorytm:**
  1. Per restauracja agreguj wszystkie delivered ordery z ostatnich 4 tygodni
  2. `avg_prep_time = mean(picked_up_at - created_at)` — stdev też
  3. `prep_variance = max(0, avg_prep_time - czas_odbioru_deklarowany)`
  4. `reliable = sla_compliance >= 92%`
  5. `parking` — default 2, manual override potem
- **Output:** draft JSON do ręcznej akceptacji Twojej
- **Estymacja:** 4-6h (pisanie + test na realnych danych)
- **Test:** dla Rukola Sienkiewicza (reliable znany) → `prep_variance ≤ 5, reliable = true`. Dla Baanko (unreliable podejrzewane) → `variance ≥ 8, reliable = false`.

**P0.8 — Restaurant meta integration w route_simulator_v2**
- **Bug obecnego v1:** hardcoded `2 min na wejście do restauracji`. Mama Thai z variance 8 → kurier spędza 10 min w kolejce, symulacja mówi 2 → przewidywany SLA false.
- **Źródło wykrycia:** Audyt #4 (punkt #2)
- **Fix:** `pickup_service_time = 2 + get_meta(restaurant).prep_variance`. Dla restauracji bez meta → default 5.
- **Estymacja:** to wchodzi już do v2 którego piszemy w Fazie 1 — zero dodatkowej pracy, tylko pamiętać o tym przy pisaniu
- **Status:** flaga w specyfikacji Fazy 1

---

## 3. FAZA 1 ZMODYFIKOWANA (co dodajemy ponad pierwotny plan)

### Dodatki do Fazy 1 wynikające z audytu

**F1.X1 — Deadhead component w scoring**
- **Cel:** scoring odróżnia kuriera 500m od restauracji vs 5km
- **Źródło wykrycia:** Audyty #1 (punkt #9) + #4 (punkt #10) — zgodnie
- **Implementacja:** nowy `S_deadhead` (waga 0.10), odjąć 0.05 z `S_dystans` i 0.05 z `S_kierunek`. Nowe wagi: `0.25/0.25/0.20/0.20/0.10`.
- **Testy przed commitem:** a/b diff na 50 realnych decyzjach — czy Ziomek po zmianie wybiera innych kurierów. Jeśli >20% przesunięć → dyskusja czy to poprawa czy degradacja.

**F1.X2 — Multi-agent impasse alert**
- **Cel:** gdy dispatch_pipeline zwraca NO dla WSZYSTKICH dispatchable kurierów → Telegram alert do Ciebie
- **Źródło wykrycia:** Moja wcześniejsza notatka + audyt #1 (punkt #6)
- **Implementacja:** w `shadow_dispatcher` po iteracji przez fleet, jeśli best_candidate is None → `send_telegram_alert(f"Order {order_id}: brak feasible kuriera. Rozważ dodatkowego.")`
- **Estymacja:** 15 linii + test

**F1.X3 — `prep_ready_at` parsing i integracja**
- Jeśli P0.6 potwierdzi że panel ma timestamp → dodać do `panel_watcher` → emit w NEW_ORDER payload
- Jeśli tylko minuty → policzyć `prep_ready_at = created_at_warsaw + timedelta(minutes=prep_minutes)` w state_machine
- Wypełnić pole `prep_ready_at` w OrderSim dla route_simulator_v2

---

## 4. PRIORYTET 1 — do Fazy 2 (nie blockery)

**P1.1 — `compute_courier_stats.py` automatyzacja**
- Skrypt nightly, bazując na `delivered_at - picked_up_at` z `orders_state.json` + sla_log
- Generuje `courier_ratings.json` z: tier (A/B/C/D), MST, consistency (100 - stddev(daily_sla))
- **Źródło:** Audyt #3 (punkt #12)
- **Wartość:** ratings działają zanim pełna `courier_ratings.py` z 4 wymiarami powstanie w Fazie 2
- **Estymacja:** 3h

**P1.2 — Test konfiguracji: MAX_PICKUP_REACH_KM**
- Audyt #4 sugerował 15 km to "samobójstwo biznesowe". Moja analiza: 15 km to upper bound cap, nie target. Ale warto sprawdzić empirycznie.
- **Task:** w shadow przez 1 tydzień logować ile % decyzji ma `pickup_dist_km > 8`. Jeśli <3% → zostawiamy 15. Jeśli 15%+ → rozważyć obniżenie do 10.
- **Estymacja:** 0h pracy (tylko analiza logów)

---

## 5. PRIORYTET 2 — do Fazy 4-5 (ulepszenia długoterminowe)

**P2.1 — Telegram inline buttons dla propozycji shadow**
- Zamiast tylko logowania do shadow_decisions.jsonl → wysyłanie na Telegram z przyciskami `[Przypisz X]` `[Przypisz Y]` `[Ignoruj]`
- **Źródło:** Audyty #1 (punkt #8), #2 (punkt #9), #3 (punkt #9) — wszystkie zgodnie
- **Status:** już zaplanowane w Fazie 4 — tylko potwierdzamy
- **Estymacja:** 0.5 dnia w Fazie 4

**P2.2 — "Silent Mode" dla koordynatora**
- Koordynator widzi propozycję, jedno kliknięcie → przypisanie przez API panelu (jeśli istnieje)
- **Status:** depend od API panelu. Jeśli Rutcom ma endpoint `POST /zlecenie/assign`, implementujemy. Jeśli nie, manual dopóki nie dogadamy z Rutcom.

**P2.3 — Dynamic timezone per miasto**
- Obecnie hardcoded `Europe/Warsaw`
- Przy wielomiastowości (Warszawa + Gdańsk) — ta sama TZ, zero problem.
- Przy ekspansji EU (Berlin, Praga) — trzeba refactor
- **Status:** backlog, reevaluate przy ekspansji zagranicznej
- **Audyt:** #4 (punkt #3)

---

## 6. CO ODRZUCAMY — z uzasadnieniem

| Sugestia | Źródło | Dlaczego NIE |
|----------|--------|--------------|
| Wstrzymanie projektu na 48h | Audyt #1 | Przesadzone. Fazę 0 robimy w 1.5 dnia, shadow z fallback pozycjami ma wartość. |
| +45° sector check dla free stop | Audyt #1 (punkt #5) | Overengineering. `<500m` jest pragmatyczne. Detour angle refaktorujemy tylko jeśli free stops generują realne problemy (obserwacja shadow). |
| "Gubienie pickupów przy assigned" | Audyt #4 (punkt #1) | Halucynacja oparta o komentarz. Reconcile PICKED_UP działa w 20s, edge case <3% orderów. Nie blocker. |
| "Nearest Neighbor słaby dla bag=4" | Audyt #4 (punkt #2b) | Irrelevant. `route_simulator.py v1` idzie do kosza. Piszemy PDP-TSP bruteforce w Fazie 1. |
| "MAX_PICKUP_REACH_KM=15 samobójstwo" | Audyt #4 (punkt #1) | Nieporozumienie. 15 km to safety cap, scoring i tak karze dystans eksponencjalnie. Default zostaje. |
| Premium SLA od dnia 1 | Pierwotne D5 | Odroczone do października 2026 (D13). Martwy kod bez klientów płacących za premium. |
| Przesunięcie Fazy 5 PWA GPS przed Fazę 1 | Audyt #1 (punkt #2) | Przesadzone. Fallback pozycjami wystarczą dla shadow validation. PWA GPS robimy mini-sprint między Fazą 1 a 2. |

---

## 7. JUTRZEJSZA PROCEDURA STARTU (jak wracać do tego)

### Zanim zaczniesz nową sesję Claude:

1. **Przeczytaj ten plik od początku** — przypomnij sobie decyzje D13, D14 i listę P0.1-P0.8
2. **Sprawdź stan produkcji** (rytuał z SKILL.md sekcja "START KAŻDEJ NOWEJ SESJI")
3. **Zdecyduj kolejność:**
   - Jeśli masz pełny dzień (6-8h) → leciemy wszystko P0.1 do P0.8
   - Jeśli masz 2-3h → tylko Quick Wins (P0.1-P0.4)
   - Jeśli masz 1h → tylko P0.1-P0.3 (unifikacja configu, time_penalty, courier_resolver)

### Start sesji — gotowa wiadomość do Claude:

```
Adrian wracamy do projektu. Przeczytaj:
1. docs/SKILL.md (V3 master)
2. docs/TECH_DEBT.md (bieżący stan)
3. FAZA_0_SPRINT.md (plan dzisiejszy)

Sanity check produkcji + fleet snapshot.

Dzisiaj robimy Fazę 0 — patche P0.1 do P0.X (do ustalenia ile
czasu mam). Zaczynamy od P0.1 (unifikacja MAX_BAG_SIZE) — napisz
dokładny patch Pythonem z backupem.
```

### Kryteria ukończenia Fazy 0 (exit criteria):

- [ ] Wszystkie P0.1-P0.8 ukończone z potwierdzonym testem
- [ ] `docs/TECH_DEBT.md` zaktualizowany o to co zrobione
- [ ] `systemctl is-active dispatch-panel-watcher dispatch-sla-tracker` = active przez 4h+ po ostatnim patchu
- [ ] Zero errors w logach przez 2h po patchu
- [ ] 0 NEW_ORDER z pickup_coords: null przez 1h
- [ ] `restaurant_meta.json` ma 53 wpisy (z gap_fill)

---

## 8. ZYSK TEJ SESJI (co dostajesz za dzień pracy)

**Bezpieczniejszy start Fazy 1:**
- Eliminacja 6 realnych bugów przed shadow
- Unifikacja konfiguracji (jedna definicja MAX_BAG_SIZE)
- Fallback OSRM — system nie pada przy awarii zewnętrznej
- Meta 53/53 restauracji — reguły R20/D3 działają dla wszystkich

**Wzrost zaufania:**
- Pierwszy tydzień shadow: decyzje oparte o kompletne metadane
- Alerty multi-agent impasse pokazują Ci gdzie flota ma realny deficyt
- Deadhead component pokazuje że Ziomek liczy ekonomię, nie tylko geometrię

**Zabezpieczenie przyszłości:**
- Premium przygotowane w modelu ale nie implementowane — wprowadzimy gdy będzie klient
- Timezone refactor jako backlog — niski priorytet, jasny trigger do aktywacji
- Ratings skrypt działa od P1.1, nie czekamy do Fazy 2

---

## 9. STATYSTYKI SESJI 12.04 (do zapamiętania)

- **Audyty zewnętrzne:** 4 (2× Gemini, 2× DeepSeek)
- **Bugów wyłapanych:** 8 realnych + 5 halucynacji (odrzuconych)
- **Nowych reguł architektonicznych:** 2 (D13 premium, D14 Faza 0)
- **Patchów do wdrożenia:** 8 w Fazie 0 + 3 rozszerzenia Fazy 1
- **Szacunkowy czas pracy Fazy 0:** 10-12h (1.5 dnia)
- **Ryzyko pominięcia Fazy 0:** wysokie — shadow by ruszył z 6 bugami, błędne propozycje obniżą Twoje zaufanie do systemu od dnia 1

---

## 10. KONTAKT DO ADRIANA / TYMCZASOWE POSTANOWIENIA

- Adrian Telegram: 8765130486
- **Dzisiaj (12.04 wieczór):** nie ruszamy produkcji, plan przeczytany
- **Jutro (13.04):** wracamy z tym dokumentem + SKILL.md do nowej sesji
- **Start fazy 1:** nie wcześniej niż 14.04 (po ukończeniu Fazy 0)
- **Shadow live target:** 16-17.04 po walidacji Fazy 0 i weekendowym monitoringu

---

**Ten plik to nie jest instrukcja projektu — to sprint plan na konkretny dzień pracy. Po ukończeniu Fazy 0 ten plik można zarchiwizować (git log będzie pamiętał). Aktualny master document pozostaje SKILL.md V3.**
