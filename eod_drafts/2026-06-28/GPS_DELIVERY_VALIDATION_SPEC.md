# SPEC #5 — fizyczne potwierdzenie dostawy (GPS), żeby „realny breach" był prawdą fizyczną, nie przyciskową
**Sesja 18, 2026-06-28 | przez Przykazanie #0**

## ✅ STATUS — #5a DONE + LIVE (2026-06-28 ~22:43 UTC, ACK Adriana „5a")
- Walidator wpięty `sla_tracker.py` handler COURIER_DELIVERED (po `rec`, przed zapisem): flaga `ENABLE_GPS_DELIVERY_VALIDATION` (common.py:208, flags.json=true LIVE) → dopisuje `physical_verified`/`gps_source`/`gps_delivered_epoch`/`button_vs_gps_delta_s` do `sla_log`. SHADOW, zero wpływu na SLA/decyzje, OFF=byte-identyczny.
- Dowody: `py_compile OK`, import OK, **34/34 testy sla_tracker**, oracle-test (0/385 physical_verified — poprawnie, inwariant manual_button≠fizyczny trzyma), restart sla-tracker czysty `NRestarts=0`.
- Backup: `sla_tracker.py.bak-pre-gps-deliv-validation-2026-06-28`, `common.py.bak-...`, `flags.json.bak-pre-gps-deliv-2026-06-28`. Rollback: flaga=false + restart / `.bak`.
- **Pokrycie dziś = ~0%** (apka pali geofence na dojeździe/odbiorze, NIE na dostawie) → miernik gotowy, czeka aż #5b da mu co mierzyć.

## ⏭ #5b PENDING (prawdziwa dźwignia, POZA dispatch_v2)
Apka kuriera + `courier_api/status_store.py` muszą odpalać **geofence STREFY DOSTAWY** na status 7 (GPS-potwierdzony `delivered_at`, source=auto_geofence). To jest „jak wszyscy będą jeździć z GPS" — odblokowuje fizyczny pomiar feas_carry (#1) i weryfikację O2. Cross-repo (Android + courier-api), większe.

---
**(poniżej: oryginalny spec ETAP 0-3, zrealizowany w #5a)**

## Po co
Każdy nasz „realny breach X%" (feas_carry 8%, decision_outcome, eta_error) stoi na `delivered_at`, który dziś = **czas przycisku z panelu**, nie fizyczne przybycie. Audyt 28.06: 0/377 dostaw zwalidowanych GPS. To skaża pomiar #1 (feas_carry) i każdy przyszły GO/flip pod „kompound". #5 to domyka.

## ETAP 0-1 — stan + źródło (zweryfikowane plik:linia)
- **Panel button-truth:** `delivered_at` w orders_state ustawia `state_machine.py:793` z `czas_doreczenia` panelu (lub `now_iso()`), naiwny Warsaw (`courier_resolver.py:44`).
- **GPS-prawda JUŻ ISTNIEJE per-order:** writer `courier_api/status_store.py:67-71` pisze `courier_ground_truth.json` — na status=7 ustawia `delivered_at` (epoch) z apki, `source=auto_geofence` (geofence apki = fizyczne wejście w strefę) vs `manual_button` (klik ręczny, gdy GPS fail). Dziś **auto_geofence 177/422**.
- **Komparator panel-vs-GPS JUŻ ISTNIEJE:** `courier_gps_commitment_shadow.py` (okno ~8h) — stąd próbka n=8 audytu (panel ~192s przed GPS).
- **Luka:** GPS-delivered NIE jest spięty **per-order** z panel-delivered jako jawna walidacja, a pokrycie częściowe (tylko apkowi z GPS).
- **Pułapka:** brak HISTORII pozycji (`gps_positions.json` = snapshot, nadpisywany ~30s) → **NIE** używać last-known-pos do post-hoc (zły timing). Prawdą fizyczną jest **`courier_ground_truth.delivered_at` z `source=auto_geofence`** (apka odpaliła geofence), NIE odległość do ostatniej pozycji.

## ETAP 3 — mapa kompletności (co dotknąć)
**Producent (SHADOW, read-only, flaga `ENABLE_GPS_DELIVERY_VALIDATION`):**
- Walidator w `sla_tracker.py:~183` (handler COURIER_DELIVERED): złącz `orders_state.delivered_at` (panel) z `courier_ground_truth` (GPS) per order →
  - `physical_verified = (source == 'auto_geofence')`
  - `button_vs_gps_delta_s = panel_delivered − gps_delivered`
  - `gps_source` (auto_geofence / manual_button / brak)
  → dopisz do `sla_log.jsonl`. ZERO wpływu na scoring/decyzje.
- **Oracle-test walidatora (C11):** na próbce z `courier_gps_commitment_shadow` (gdzie znamy oba czasy) — czy `physical_verified` + delta zgadza się z faktem. Inwariant: `manual_button` NIGDY nie liczony jako fizyczny.

**Konsumenci (NIE za teraz — po 7-dniowej obserwacji):**
- `daily_briefing.py:285` / `courier_ranking.py:78` — raportuj **% physical_verified** (pokrycie GPS) + breach OSOBNO na GPS-confirmed vs button-only.
- **feas_carry deferred (#1):** gdy pokrycie GPS pełne („jak wszyscy z gps") → liczyć realny breach na `physical_verified` → **to odblokowuje pomiar #1**.
- `eta_calibration_logger.py:203` — opcjonalnie GPS-delivered jako truth.

## Caveat-lift (kompound)
Dla zleceń `physical_verified=true` caveat „button-truth" ZNIKA — `delivered` = fizyczny. Pokrycie rośnie z adopcją apki/GPS. To warunek, by „każde 5%" było realne (nie button ±3min).

## Ryzyka / pułapki
- TZ naiwny — przejść przez `parse_panel_timestamp` (`ENABLE_CHECKPOINT_TS_WARSAW_PARSE`).
- Reassign A→B: phantom wpis A w GT (`observability/ground_truth_gc.py` prune bez picked/delivered).
- Retencja: orders_state 12h vs shadow 8h — liczyć w oknie ≤8h.
- `manual_button` ≠ fizyczne; `delivery_coords=None` przy geocode-fail (część zleceń) → wtedy tylko porównanie CZASU, bez odległości.

## Ścieżka (Przykazanie #0)
- **ETAP 0-3 = ten spec (zrobione, read-only).**
- ETAP 4 (build): walidator shadow + flaga + oracle-test + regresja vs baseline.
- ETAP 5: 7 dni `sla_log` → % pokrycia, rozkład delty button-vs-GPS.
- ETAP 6-7: deploy `sla-tracker` (1 serwis) + rollback flagą.
- **Czeka ACK Adriana przed ETAP 4.** Nie koliduje z sesją 15 (feas_carry/would_hard_cap/conftest) ani 20 (czasówka).
