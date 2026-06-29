# PROJEKT — Faza 2 Etap 3: realna DYSPOZYCJA paczek przez Ziomka

Status: PROJEKT (design only, 2026-06-29). NIC nie wdrożone. Wymaga ACK przed budową.
Kontekst: most paczki→konsola [[parcel-bridge-console-2026-06-29]]. Etap 1 (coords nadawcy)
i Etap 2 (shadow, walidacja: Ziomek umie wolny odbiór) = DONE/LIVE.

---

## 1. Cel
Paczka z `bialystok.nadajesz.pl` (tabela `delivery`, source api:parcel, ze `sender_lat/lng`)
ma być **realnie dyspozycjonowana**: Ziomek wybiera kuriera, kurier widzi ją w apce, odbiera
u nadawcy, dowozi do odbiorcy; konsola i śledzenie pokazują realny stan. Tak jak zlecenie
gastro — ale paczka NIE jest w gastro i ma WOLNY punkt odbioru (adres nadawcy, nie restauracja).

## 2. Fundament mechaniczny (ustalone w kodzie)
- **`orders_state.json` = wspólny HUB.** Czytają go: silnik (`dispatch_pipeline`/`shadow_dispatcher`),
  konsola (`fleet_state.read_orders/read_fleet`), **apka kuriera** (`courier_api /api/courier/orders`
  → „Dane z plików Ziomka: orders_state.json + courier_plans.json"). Klucz = id zlecenia gastro
  (numeryczny string), pickup_coords liczone z adresu (`_resolve_pickup_coords`).
- **`panel_watcher` ODBUDOWUJE orders_state co tick z panelu gastro** (zapis przez
  `state_machine` z lockiem/sanity-guard/atomic). **Brak pojęcia „klucz nie-gastro/chroniony"** —
  cokolwiek nie z gastro zostałoby nadpisane/usunięte. To CENTRALNE ograniczenie.
- **Przydział:** silnik AUTO (`auto_assign_gate` AUTON-01 + `auto_assign_executor`) albo człowiek
  z konsoli (`gastro_assign.py` → panel gastro). Obie ścieżki celują w GASTRO → dla paczki rzucają
  HTTP 500 (paczki nie ma w gastro; Etap-1.5 już to ugrzecznił w konsoli).

## 3. Decyzja architektoniczna — NATYWNY TOR PACZEK (na orders_state)
Dwie drogi:
- **A. Wepchnij paczkę do gastro** (jak papu-bridge). ❌ ODRZUCONE: gastro trzyma odbiór jako
  STAŁE konto restauracji → traci wolny adres nadawcy (sedno opcji 1). Działa tylko dla
  nadawców-firm na stałych kontach (≈ istniejący Panel Bridge) — NIE dla dowolnego nadawcy.
- **B. Natywny tor paczek na orders_state.** ✅ WYBRANE: paczka wchodzi do orders_state jako
  natywne zlecenie z `pickup_coords=nadawca`, `delivery_coords=odbiorca`, `source=parcel`.
  Wtedy WSZYSTKIE 3 hub-konsumenty (silnik, konsola, apka) obsługują ją bez własnego kanału.
  Niesie wolny odbiór poprawnie (Etap 2 udowodnił, że `assess_order` to liczy).

## 4. MAPA KOMPLETNOŚCI (wszystkie warstwy + bliźniaki — protokół PRZYKAZANIE #0)
Każdy punkt to osobny element do zrobienia; bliźniacze ścieżki RAZEM.

1. **Klucz/id paczki w orders_state** — MUSI być numeryczny i bezkolizyjny z gastro-zid
   (apka/silnik/fleet robią `int(oid)` w wielu miejscach; `"PCZ-..."` z nakładki konsoli NIE
   nada się jako klucz orders_state). Propozycja: dedykowany zakres `parcel_oid = 900_000_000 + delivery.id`
   (audyt: gastro-zid << 900M). Mapowanie `parcel_oid ↔ delivery.id` trzymane obok. **Completeness:
   przejrzeć wszystkie `int(order_id)` w dispatch_v2 + courier_api + fleet_state — czy 9-cyfrowy
   klucz nie psuje żadnego założenia (np. parse panelu).**
2. **Ingest do orders_state (parcel-aware writer)** — `panel_watcher`/`state_machine` MUSZĄ przy
   każdym tick DOKLEJAĆ aktywne paczki (z panelu, source=parcel) do orders_state i NIE usuwać ich
   sanity-guardem (który dziś tnie wszystko spoza gastro). Wariant bezpieczny: panel pisze
   `parcel_orders_snapshot.json` (świeże aktywne paczki w kształcie orders_state-entry), a writer
   merge'uje je po rebuildzie z gastro. **Bliźniaki: sam zapis + sanity-guard prune + lock.**
3. **Silnik liczy paczkę** — `dispatch_pipeline`/`feasibility_v2`/scoring muszą przyjąć entry bez
   restauracyjnego `aid` (pickup_coords podany wprost). `pickup_rules` paczek = brak (lub per-nadawca-firma
   później). `prep_minutes`/gotowość = parametr paczki (asap / slot z `promised_pickup_at`). R6 cap,
   R27 — jak dla zwykłego zlecenia. **Etap 2 shadow już to potwierdził dla shadow_quote; pełny pipeline
   = ta sama ścieżka (assess_order), ale zweryfikować feasibility/plan/recheck.**
4. **Commit przydziału (write-back) — NIE gastro, lecz PANEL** — gdy silnik/konsola wybierze kuriera:
   zapisz `delivery.courier_id` + status `assigned` w panelu (przez `deliveries_svc.transition_status`)
   ORAZ odzwierciedl w orders_state (courier_id na parcel_oid), żeby apka kuriera pokazała. **Bliźniaki:
   ścieżka AUTO (auto_assign_executor) i ścieżka KONSOLA (`/coordinator/assign`) — obie parcel-aware.**
5. **Apka kuriera** — czyta orders_state → paczka z courier_id pokaże się SAMA. Zweryfikować pola
   wymagane przez `/api/courier/orders` (pickup/delivery coords+adresy, czas, etykiety) + plan stopów
   w `courier_plans.json` (parcel_oid w planie). **Twin: orders_state entry + courier_plans entry.**
6. **Konsola** — nakładka już pokazuje paczki; po realnym przydziale `read_fleet` (worki kuriera) musi
   ująć paczkę (dziś nakładka dokłada tylko do `read_orders`). **Twin: read_orders + read_fleet.**
7. **Status flow-back** — kurier w apce: odebrał/dowiózł → `courier_api/status_store` → routować
   statusy parcel_oid z powrotem na `delivery.status` (status_event) + tracking (DEL-04) + konsola.
   **Bliźniaki: pickup, delivered, failed, cancelled.**
8. **Anulowanie/przerzut** — cancel paczki już parcel-aware (panel). Reassign/duch przerzutu
   ([[reassignment-forward-shadow-v2]]) musi rozumieć parcel_oid albo je pomijać świadomie.

## 5. Plan przyrostowy SHADOW→FLIP (nigdy big-bang; każdy krok flaga + rollback)
Uwaga: cały Ziomek jest DZIŚ w cieniu (`ENABLE_AUTO_ASSIGN=False`), więc paczka w LIVE orders_state
i tak NIE jest auto-dispatchowana — jest auto-PROPONOWANA (shadow), a działa tylko przez ręczny
przydział z konsoli. „Live orders_state" tu = bezpieczne, bo nic nie rusza kuriera bez człowieka.
- **3a — ingest shadow (osobna kopia):** writer pisze paczki do `orders_state.parcels_shadow.json`
  (NIE live). Weryfikacja: silnik (replay) + konsola (read na kopii) + apka (dry) renderują dobrze.
  Zero wpływu na live. Flaga `PARCEL_LANE_INGEST_SHADOW`.
- **3b — console-assign write-back (paczka):** `/coordinator/assign` na PCZ-/parcel_oid → zamiast
  gastro_assign (500) zapisz `delivery.courier_id`+status w PANELU i odzwierciedl w orders_state.
  Shadow-first: loguj „CO BY zapisano", potem flip. (To JEDYNY realnie nowy kawałek dla paczek dziś,
  bo reszta = istniejący tryb cienia gastro.)
- **3c — status flow-back:** zdarzenia apki kuriera (odebrał/dowiózł) na parcel_oid → `delivery.status`
  (status_event) + tracking + konsola. Shadow→flip.
- **FLIP ingest live (ACK):** paczki w PRAWDZIWYM orders_state — `shadow_dispatcher` auto-proponuje
  je jak gastro (wciąż globalny cień, brak realnego auto), człowiek przydziela z konsoli (3b live).
- **Auto realny = NIE decyzja paczek:** przychodzi GLOBALNIE z `ENABLE_AUTO_ASSIGN=True` (cały Ziomek).
  Przed tym globalnym flipem: `auto_assign_executor` MUSI być parcel-aware (write-back panel, nie gastro).
Replay przed każdym FLIP: metryka lepsza ON↔OFF + brak regresji gastro (PEŁNA regresja pytest tests/
+ e2e przez wszystkie dotknięte warstwy).

## 6. Ryzyka + rollback
- **Zatrucie orders_state** (parcel psuje gastro dispatch): largest. Mitygacja: parcel_oid w osobnym
  zakresie + sanity-guard ROZSZERZONY (nie tnij parcel-keys, ale waliduj je) + flaga ingest = kill-switch.
- **Wyścig writer↔panel_watcher** (kto wygrywa zapis orders_state): merge MUSI iść przez ten sam
  lock/atomic co watcher (`state_machine`), nie obok.
- **Kolizja id** (9-cyfrowy klucz w `int(oid)` sites): audyt completeness #1 przed flipem.
- **Apka pokaże paczkę bez pełnych pól** (czas/adres) → źle dla kuriera: weryfikacja #5 w shadow.
- Rollback każdego kroku: flaga OFF + restart odpowiedniego serwisu; ingest off → orders_state wraca
  do czysto-gastro w 1 tick.

## 7. Otwarte pytania (do ACK przed budową)
1. Schemat id: zakres `900M+delivery.id` OK, czy wolisz mapę `parcel_seq`? (wpływa na audyt int(oid)).
2. Ingest: rozszerzyć `panel_watcher` (ryzyko: dotykamy serca silnika) vs sidecar+merge w `state_machine`?
3. Timing paczki: asap czy sloty (`promised_pickup_at`/`slot_id`) jako `delivery_in_min`?
4. Auto-assign paczek od razu po FLIP-1 czy długie okno human-only?
5. Nadawcy-firmy (opcja 2): zostają na Panel Bridge (gastro), czy migrują do toru paczek?

## 7b. DECYZJE Adriana (2026-06-29)
1. **Id `900M+delivery.id` — ZATWIERDZONE.**
2. **Ingest — czeka na decyzję** (objaśnione prostym językiem; rekomendacja: sidecar + MINIMALNY,
   chirurgiczny dotyk watchera = „nie kasuj kluczy source=parcel"; reszta logiki w osobnym programie).
3. **Timing — wg typu usługi z formularza:** `direct` → ASAP; pozostałe usługi → SLOT czasowy
   (z `slot_id`/`promised_pickup_at`). Build: zmapować service→delivery_in_min/desired_delivery.
4. **Auto vs human — ROZSTRZYGNIĘTE (Adrian): paczki dziedziczą GLOBALNY tryb Ziomka, brak
   osobnej bramki dla paczek.** Dziś `ENABLE_AUTO_ASSIGN=False` (common.py:834) = Ziomek
   AUTO-PROPONUJE w cieniu (liczy would_auto_assign, NIE wykonuje), człowiek przydziela z
   konsoli (`COORDINATOR_ASSIGN_LIVE=1`). Paczka wchodzi do orders_state → **realny
   `shadow_dispatcher` auto-proponuje ją tak samo jak gastro** (propozycja w feedzie konsoli,
   joint bundling z gastro — lepsze niż izolowany shadow_quote z Etapu 2). Gdy GLOBALNIE
   flip `ENABLE_AUTO_ASSIGN=True` → paczki też auto, ten sam tor, ZERO parcel-specific kodu
   poza tym, że `auto_assign_executor` musi być parcel-aware (write-back do PANELU, nie gastro
   — inaczej gastro_assign rzuci 500). Nie ma więc „FLIP-1 human / FLIP-2 auto" dla samych paczek.
5. **Nadawcy-firmy: NA RAZIE zostają na Panel Bridge (gastro); MIGRUJĄ do toru paczek gdy Ziomek
   autonomiczny** (docelowo wszyscy zamawiają przez bialystok.nadajesz.pl). Tor paczek budujemy
   tak, by w przyszłości przyjął też firmy (nadawca-firma = po prostu znany adres odbioru).

## 8. Rekomendacja kolejności
3a (ingest shadow) → 3b → 3c → audyt completeness #1 (int oid) → FLIP-1 human-only → obserwacja
2-3 dni realnych paczek → replay → FLIP-2 auto. Najpierw zebrać shadow z PRAWDZIWYCH paczek (Etap 2
już zbiera) — bez danych nie ma replayu „warto+bez regresji".
