# NadajeSz — Flow systemu dispatcha

Źródło: rozmowa Adrian 11.04.2026 wieczór + screeny panelu + podręcznik kuriera PDF.

## Flow end-to-end zlecenia

### 1. Restauracja tworzy zlecenie
- Wybiera "najwcześniej za X min" (15/20/25/.../60 min)
- Albo dokładny czas odbioru (czasówka, np. 21:46)
- Wpisuje: adres dostawy, telefon, kwota, uwagi
- "Zamów kuriera"

Kluczowe: wybrany czas to MINIMUM prep — kurier nie może wcześniej, może później.

### 2. Panel koordynatora
Zlecenie pojawia się w kolumnie "Nowe" (lewy panel).
Koordynator widzi flotę z aktualnymi bagami per kurier.

### 3. Koordynator przypisuje
- Dropdown czas: wybiera za ile kurier REALNIE może być (wylicza w głowie)
- Dropdown kurier: lista z timestampami "odbiór: XX:XX"
- Wybiera jednego kuriera

ETA kuriera >= restauracja_min. Może być wcześniej (kurier czeka do 5 min) jeśli brak lepszej opcji.

### 4. Restauracja dostaje zwrotkę
Kto kurier, ETA, status. Synchronizuje produkcję pod ETA (nawet 50 min na peaku).

### 5. Kurier
Panel /admin2017/kurier2, loguje nr telefonu.
Zakładka "Moje" — zlecenia przypisane, statusy: dojazd/oczekiwanie/odebrane/doręczone.
Przycisk "NIE BIORĘ" NIE UŻYWANY — kurier bierze co dostanie.

## Kluczowe reguły (potwierdzone Adrian 11.04)

### SLA
- Hard: 35 min od picked_up do delivered (bufor na kurierów, podręcznik mówi 30)
- Max 5 min spóźnienia, więcej = kurier informuje restaurację

### Łączenie zleceń
- Nie realizujemy pojedynczo
- 2-4 zlecenia w fali (bag operacyjny)
- Od najbliższego do najdalszego
- "Strategia doboru": bliskie pierwsze, dalekie ostatnie w fali

### Reguły kanoniczne dispatchu
1. Nearest-first domyślnie
2. Unikaj backtrackingu (TSP globalnie, nie local-greedy)
3. SLA PRIORYTET przed dystansem
4. Strategia doboru: bliskie pierwsze w fali

### Operational params
- MAX_BAG_SIZE = 6
- Cel throughput: 3+ zleceń/h/kurier, realnie 2.5-5
- Max pickup reach: 15 km (do rewizji dla long-haul)
- Okno fali: 15 min

### Long-haul bundling
- Brak sztywnego limitu km
- >10 km: kurier bierze więcej TYLKO jeśli delivery do tego samego miejsca
- SLA check automatycznie to wyłapie

### Czasówki (czas_odbioru >= 60 min)
Progressive: 60 min → 50 min → 40 min przed odbiorem
- 60 min: szukaj kuriera który NATURALNIE będzie blisko
- 50 min: zacieśnij kryteria, mały detour OK
- 40 min: urgent mode, bierz cokolwiek feasible (deadline)

### Natężenie ruchu
3 poziomy (małe/średnie/duże) ustawiane w panelu globalnie.
Algorytm: avg_load = active_orders / dispatchable_fleet
- <2.5 → małe, 2.5-4.5 → średnie, ≥4.5 → duże
Wpływa na dostępne czasy prep pokazywane restauracji.

Dziś: Ziomek obserwuje only. Jutro: auto-set z hysteresis 5 min.

## Przykłady kanoniczne

### A — zwykłe zlecenie, natychmiast
now=18:00, restauracja 20 min → czas_odbioru_timestamp=18:20
K1 bag=2, kończy 18:15, dojeżdża 18:17
Decision: K1 ETA 18:20 (max(18:17, 18:20))
Predicted delivered 18:35 → SLA OK

### B — czasówka 60 min przed
now=19:00, czasówka 20:00
Szukamy kuriera naturalnie kończącego bliskiej restauracji o ~19:55
Jeśli tak → przypisz. Jeśli nie → czekaj 10 min, iteracja 50 min przed

### C — czasówka 40 min przed (urgent)
now=19:20, czasówka 20:00, wcześniejsze iteracje fail
Bierz dowolny feasible z ETA ≤ 20:00
Priorytet: pusty bag > bag z najmniejszym SLA impact
Hard-assign nawet nieoptymalne

## Mapowanie panel → kod

### fetch_order_details zwraca:
- id_status_zamowienia: 1..9
- id_kurier: int (26 = Koordynator bucket)
- czas_odbioru: int minut prep (restauracja wybór)
- czas_odbioru_timestamp: Warsaw TZ timestamp
- dzien_odbioru: kiedy kurier FAKTYCZNIE odebrał
- czas_doreczenia: kiedy FAKTYCZNIE dostarczył
- address.id: stable address_id do lookup

### Przepływ w Ziomek state
1. panel_watcher wykrywa NEW_ORDER z panel HTML
2. Ziomek event_bus emit NEW_ORDER z address_id + pickup_coords
3. shadow_dispatcher konsumuje NEW_ORDER
4. dispatch_pipeline wywołuje feasibility dla każdego dispatchable kuriera
5. Wybór best score → log do shadow_decisions.jsonl
6. Koordynator (dziś człowiek) widzi zlecenie, przypisuje kuriera
7. panel_watcher wykrywa COURIER_ASSIGNED
8. state_machine aktualizuje
9. reconcile picked_up/delivered wyłapuje transitions
10. sla_tracker liczy delivery_time + sla_ok
