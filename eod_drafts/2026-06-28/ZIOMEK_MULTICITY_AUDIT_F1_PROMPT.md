# ZIOMEK — AUDYT MULTI-CITY (F1): „co jest zaszyte pod Białystok i jak to wynieść do konfiguracji per-miasto"

> Cel (Adrian, 2026-06-28): Ziomek = moat firmy; w tym roku skalowanie **franczyzą na inne miasta**. Ten audyt to
> dedykowany, dogłębny przegląd gotowości multi-city — inwentaryzacja sprzężeń z Białymstokiem + projekt warstwy
> konfiguracji per-miasto + playbook uruchomienia nowego miasta. READ-ONLY (diagnoza, zero zmian).

## Premisa (zweryfikowana 28.06)
- **588 literałów „białystok"** w ~30 plikach `.py`. `BIALYSTOK_CENTER=(53.1325,23.1688)` zaszyte (bootstrap_restaurants, courier_resolver ×8 jako pozycja syntetyczna). `V326_OSRM_TRAFFIC_TABLE` (common.py:516) skalibrowana pod Białystok. `districts_data.py` 40KB ulic Białegostoku. `address_mismatch.py:66 city="bialystok"`. `EXCLUDED_CIDS` (daily_accounting.config). `flags.json` + `dispatch_state/` = GLOBALNE (jeden tenant). Ślad intencji: `scoring.py:28 # Future per-city scaling: DIST_DECAY_BY_CITY={bialystok,warsaw...}`.

## Zasady audytora (NIE łam)
1. **READ-ONLY.** Zero zmian. Wolno: Read, grep/rg, git, systemctl/atq read, czytanie konfiguracji/logów.
2. **Weryfikuj, nie zakładaj.** „Wygląda na zahardkodowane" ≠ „jest". Część rzeczy MOŻE być już env/config-driven albo „per-tenant ready" (np. `FIRMOWE_KONTO_ADDRESS_IDS` ma adnotację per-tenant). Cytuj `plik:linia`, sprawdź czy wartość przychodzi z configu/env, czy jest stałą.
3. **Klasyfikuj każde znalezisko:**
   - `coupling_type` ∈ {hardcoded-value, single-tenant-assumption, calibrated-from-data, external-dependency, global-universal}
   - `blocker` ∈ {P0 (blokuje URUCHOMIENIE 2. miasta), P1 (blokuje skalę/jakość), P2 (degraduje), P3 (kosmetyka), NOT-A-BLOCKER (uniwersalne / już konfigurowalne)}
   - dla każdego: **proponowany klucz konfiguracji per-miasto** + **jak nowe miasto go ustawia/uczy się go** (cold-start vs learned-from-data).
4. **Rozróżniaj „per-miasto" od „uniwersalne".** Fizyka stygnięcia jedzenia (R6≈35 min) jest uniwersalna; rozrzut km / korki / dzielnice / kurierzy — per-miasto. Każdą regułę zaklasyfikuj.
5. Istniejące dokumenty (CLAUDE.md, `project_overview.md`, audyt nocny `eod_drafts/2026-06-27/*`) = priors do weryfikacji. Nawigacja: `ZIOMEK_AUDIT_PROTOCOL_ENRICHMENT.md` §A (gdzie co żyje).

## Obszary (lanes) — każdy czyta głęboko i zwraca: findings + proposed_config + tenancy_notes
1. geo-coords (BIALYSTOK_CENTER, fallback coords, bbox, syntetyczna pozycja no-GPS, geocode default city)
2. districts-streets (`districts_data.py`, `district_reverse_lookup.py`, aliasy ulic, korytarze, drop_zone_from_address)
3. traffic-speed (`V326_OSRM_TRAFFIC_TABLE`, osrm_client, traffic_v2, drive_min_calibration, speed_tiers)
4. time-windows (peak/blackout, wave matrix tier×pora, okna zmian, czasówka triggery, bukiety czasu w scoringu)
5. couriers-tiers (courier_tiers.json, EXCLUDED_CIDS, build_v319h_courier_tiers, new_courier_pairing, ranking, manual_overrides, kurier_ids/piny)
6. restaurants-companies-bridges (restaurant_company_mapping, FIRMOWE_KONTO_ADDRESS_IDS, drtusz_bridge, panel-bridge 11 firm, pickup_rules, bootstrap_restaurants)
7. routing-infra (osrm host/instance/mapa, geocoding endpoint+bias+default, panel_client host gastro.nadajesz.pl, config)
8. panel-tenant-api (panel_watcher/panel_client/state_machine — założenie JEDEN panel/tenant/order-id-space; czy istnieje pojęcie tenant_id)
9. business-rules (feasibility_v2 R6=35 / R1 8km / R5 1.8km / corridor 2.5km / scoring weights / rule_weights.json — uniwersalne vs strojone)
10. calibration-ml-bootstrap (ml_inference LGBM trenowane na Białymstoku, eta/drive/prep-bias, auto_proximity progi — jak NOWE miasto z ZERO danych startuje? cold-start vs learned)
11. state-infra-deploy (`dispatch_state/*`, flags.json globalny, systemd 1 instancja, logi — model najemcy: proces/stan per-miasto? namespacing? co globalne vs per-miasto)
12. notifications-coordinator (telegram group/token, koordynator id_kurier=26, shift_notifications, raporty — założenia jeden-koordynator/jedna-grupa)

## Deliverables (synteza)
A. `ZIOMEK_MULTICITY_INVENTORY.md` — pełny spis sprzężeń: tabela (obszar → co zaszyte → coupling_type → blocker → klucz config per-miasto → cold-start/learned). Posortowane po blocker.
B. `ZIOMEK_MULTICITY_ARCHITECTURE.md` — (1) MODEL NAJEMCY: jak uruchomić miasto #2 — opcje (proces+stan+config per-miasto / multi-tenant z tenant_id / hybryda) z trade-offami + rekomendacja; (2) co GLOBALNE vs PER-MIASTO; (3) PLAYBOOK nowego miasta (cold-start config + co uczy się z danych + ile dni shadow przed go-live); (4) ROADMAPA fazowa (MVP „2 miasta" → pełny multi-tenant) + „minimum żeby ruszyć 2. miasto".
