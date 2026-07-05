# SPRINT 0 / Zadanie 2 (A0-ROUTEORDER) — golden parytet kolejności trasy przed wygaśnięciem monitora (tmux15, 05.07 wieczór)

**Commit:** `5d24bc9` (master, FF z brancha `fix/route-order-golden-sprint0`; worktree usunięty, branch zostaje).
**Regresja:** kanon **4191 passed / 0 failed** (baseline dnia 4188/0 po A1-SERIALIZER tmux17; delta = dokładnie moje +3 testy, +1 gated skip) + panel `test_route_order_parity_golden.py` **4/4** na NOWYM korpusie.

## Kontekst i korekta liczb handoffu
Monitor `ziomek_time_route_monitor` (repo panelu, timer 10-min) wygasa SAM **2026-07-10** (`MONITOR_STOP_AFTER` w unicie) — decyzja: nie przedłużać. Handoff mówił „dziś 44-75 rozjazdów/dzień" — **NIEAKTUALNE**: agregat jsonl pokazuje 75/44/30 rozjazdów 26-28.06, a od **29.06 = 0 rozjazdów przez 7 kolejnych dni** (~500 checków/d; fixy 22-28.06 wygasiły klasę). Zadanie = czysta siatka bezpieczeństwa (ratchet), nie gaszenie pożaru.

## Co zbudowane (wszystko w zakresie tests/ + tools/; silnik/panel READ-ONLY)

**1. Korpus 25 case'ów (było 13; próg testu 10→20):** 16 syntetycznych + 9 żywych worków z wieczornego peaku 05.07 (worki do 6 zleceń, w tym czasówki i plany silnika). Parytet konsola==kanon w chwili generacji: **25/25**. Nowe klasy obowiązkowe (strażnik `test_corpus_covers_canon_edge_cases`): `syn_czasowka_far_ahead`, `syn_czasowka_carried_mix`, `syn_paczka_no_ck_last`, `syn_paczka_same_sender_bundle`, `syn_carried_two_relative_order`, `syn_plan_trust_canon_carried_relax` (relax „odbierz po drodze" verbatim), `syn_mixed_czasowka_paczka_carried`. (Paczki w route-order = zwykłe zlecenia — renderery nie czytają `address_id`; R6-exempt żyje w feasibility.)

**2. DWA NAPRAWIONE BUGI HARNESSU z 01.07 (klasa #17 — przyrząd mierzył co innego niż deklarował):**
- **(a) klucz planu:** syntetyczne plany miały `"sequence"`, a WSZYSCY konsumenci (`route_podjazdy._canon_order_from_plan`/`_plan_pickup_clusters`/`plan_drop_rank`, fleet_state) czytają `"stops"` (jak żywe courier_plans.json) → case „trust_canon verbatim" faktycznie testował FALLBACK czasowy (expected miało 900041 wg ck, plan mówił 900042 pierwsze). Po fixie golden = plan verbatim. Strażnik: `test_plan_docs_use_stops_key`.
- **(b) format ck:** syntetyczne `czas_kuriera_warsaw="12:10"` parsowało się do **None** (`_iso` wymaga pełnego ISO; żywe = `2026-07-05T19:55:00+02:00`) → sort committed-ascending i sklejanie ≤PICKUP_MERGE_MIN były w syntetykach MARTWE (fallback po order_id; „bundle w progu sklejania" dawał 4 stopy zamiast 3). Po fixie helper `o()` konwertuje HH:MM→pełne ISO Warsaw; semantyka wszystkich etykiet realna (committed 12:00→12:30→13:00; bundle sklejony w 1 pickup; paczka bez ck = sentinel na końcu).

**3. Trzecia noga parytetu — SILNIK (pełne silnik==konsola==apka):** `test_engine_plan_is_source_of_golden_for_covering_plans` — dla case'ów, gdzie plan Ziomka pokrywa cały worek, golden MUSI być wierną projekcją planu (niezależna referencja w teście: skip węzła pickup dla niesionych + merge kolejnych odbiorów tej samej restauracji + dedup dostaw). Razem z istniejącymi nogami: SILNIK(plan)==APKA(`order_podjazdy`)==KONSOLA(`_build_route`). Plus `test_golden_structural_invariants` (tripwire C9): dropoff nigdy przed pickupem, niesione bez pickupu, dokładnie 1 dropoff per zlecenie, zero obcych oid.

**4. Następca monitora (pion Q3) — ZAKODOWANY, aktywacja ZA ACK:**
- `tools/route_order_live_parity_check.py` — one-shot bez daty wygaśnięcia: parytet kolejności na ŻYWYCH workach (kanon vs konsola, efektywne flagi produkcyjne z drop-inów/env — wzorzec #15) + **dryf flag porządkotwórczych vs golden-pin korpusu** (legalny flip ⇒ regeneracja korpusu; czerwony bez regeneracji = dryf klasy #9/#15). Dowód działania: `{"verdict":"OK","checked_bags":9,"flag_drift":false}` exit 0.
- `tests/test_route_order_live_parity.py` — wrapper pytest odpalający tool panel-venvem; **domyślnie SKIP** (`ENABLE_ROUTE_ORDER_LIVE_PARITY=1` włącza; ON≠OFF udowodnione: ON=1 passed). **Aktywacja = decyzja Adriana** (żywy stan w regresji = zależność od produkcji; golden-rdzeń działa zawsze i deterministycznie).

## Jak to zastępuje monitor (i czego świadomie NIE zastępuje)
- **Q3 (parytet trasy):** golden-testy (CI, każda regresja — realnie kilka(naście) runów/dzień z sesji) + gated live-check. Timer 10-min umiera 10.07 bez straty klasy.
- **Q1 (przypisane bez czasu) i Q2 (drift czasu po przypisaniu): NIE są zastępowane tym sprintem** — świadome N-D. Istniejący strażnicy częściowi: tripwire R-DECLARED (ON od 03.07, łapie naruszenia `czas_kuriera≥czas_odbioru`) + frozen-pickup (`ENABLE_FROZEN_PICKUP_ETA`/`PANEL_FLAG_PIN_AGREED_PICKUP_TIME` ON). **Luka bez następcy: Q1** (dziś np. 2 zlecenia missing-time w ticku 17:03) — kandydat na mały tripwire w osobnym pasie, decyzja Adriana.

## Sekwencja ACK dla Adriana
1. (opcjonalnie, rekomendowane po 10.07) włączyć następcę do kanonicznej regresji: `ENABLE_ROUTE_ORDER_LIVE_PARITY=1` w komendzie regresji / conftest.
2. Monitor zostawić w spokoju — sam wygaśnie 10.07 (belt-and-suspenders do tego czasu; potem timer można wyłączyć `systemctl disable --now ziomek-time-route-monitor.timer` przy okazji sprzątania, bez pośpiechu).
3. Konsolidacja 4 kopii logiki kolejności do 1 źródła = OSOBNY sprint (poza zakresem, zgodnie z handoffem).

## Rollback
`git revert 5d24bc9` (czysto additive dla silnika — zero zmian w dispatch_pipeline/plan_manager/route_podjazdy/panelu; zmiany tylko tests/+tools/). Stary korpus w historii gita.
