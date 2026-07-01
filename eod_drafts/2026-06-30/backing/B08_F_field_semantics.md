# B08 — KLASA F: DRYF SEMANTYKI PÓL (display=decyzja / pola-sprzężone / pole-na-granicy)

**Agent:** B08-F-field-semantics · **Lane B** · **Faza 1 audyt spójności Ziomka** · **READ-ONLY**
**Data:** 2026-06-30 ~14:1x UTC · **HEAD:** `8024705` · sesja tmux 2.
**Wszystkie `plik:linia` ze ŚWIEŻEGO grepu dziś** (linie dryfują — ≥3 żywe sesje na repo). Zero edycji/restartów/flipów.

**Zakres klasy F (dryf semantyki pól):** dla KAŻDEGO pola przekraczającego granicę warstw/repo — WSZYSCY writerzy + WSZYSCY konsumenci. Trzy pod-osie zlecenia:
- **F1** pole-display-JEST-zmienną-decyzyjną (`eta_pickup` karmi scoring `extension_penalty` + HARD-reject >60min + cross-repo `time_arg`→committed `czas_kuriera`).
- **F2** pola-sprzężone-pisane-asymetrycznie (`delivery_coords` bez `delivery_address`; regeocode).
- **F3** pole-na-granicy-warstw-gubione (`uwagi`/notes).

**Relacja do Fazy A:** A6 GRUPA 7 (root **R5 — display≠decision**) zaczęła `eta_pickup` jako seed i JAWNIE oddała F2/F3 („bliźniacze pola sprzężone `delivery_address`↔`delivery_coords`") do „Fazy B-F (sweep semantyki pól)". Ten dokument = ten sweep: potwierdzam+rozszerzam `eta_pickup` (każdy konsument grepem), dokładam pełne mapy writerów/konsumentów dla `delivery_coords`/`delivery_address` i `uwagi`, oraz cross-field reuse `delivery_coords`→pozycja-kuriera. **Nie re-derywuję** A1/A2 route-order/floor (inne agenty).

---

## TL;DR — 7 instancji klasy F

1. **F1-A (CONFIRMED, LIVE):** `eta_pickup_utc` napędza **HARD REJECT** (`verdict=NO` gdy `extension_penalty()`→None przy ekstensji >60min) ORAZ karę scoringu `v324a_extension_penalty` w `final_score`. To NIE display — to twarda zmienna decyzyjna na 2 warstwach (feasibility-verdict + scoring).
2. **F1-B (CONFIRMED, LIVE, cross-repo J):** `best.eta_pickup_hhmm` (pole DISPLAY, derywat z `eta_pickup_utc`) na akceptacji w konsoli → `time_arg` → `assign.py --time` → `przypisz-zamowienie` ustawia **committed `czas_kuriera`** (potem R27-nietykalny). Naiwna „zmiana napisu" = zmiana committed-promesy. Dokładnie wzorzec #8 protokołu.
3. **F1-C (CONFIRMED, PLAUSIBLE-materialność):** `eta_pickup_utc` ma **2 komputacje** rozjeżdżalne — main-loop lokalna (karmi `extension_penalty`/score) vs post-loop nadpis metryki `c.metrics["eta_pickup_utc"]` (no_gps:5862 / pre_shift:5877, karmi display+`time_arg`+serializer). Dla pre_shift/no_gps wartość scorująca ≠ wartość serializowana/committed.
4. **F2-A (SOURCE-fixed, FRAGILE):** `gastro_edit.regeocode_and_update` pisze `delivery_coords` ZAWSZE, `delivery_address` TYLKO gdy `ENABLE_REGEOCODE_SYNC_TEXT`. Flaga **=true LIVE** (fix asymetrii 484269 „Można"≠„Mroźna" 4,26km), ALE poza ETAP4/fingerprintem (A3 leak) + const-default OFF → cichy rewert przy usunięciu klucza. Twin-asymetria zamknięta tylko za żywą flagą-leakiem.
5. **F2-B (CONFIRMED):** rozdział konsumentów `delivery_address`(tekst)→district `drop_zone_from_address` (SOFT: trajektoria/wave/bundling) vs `delivery_coords`(pin)→geometria `feasibility` R1/R5/R7 (HARD/SOFT). Writer aktualizujący JEDNO bez drugiego = split-brain: HARD-geometria widzi nowy pin, SOFT-district stary tekst.
6. **F2-C (CONFIRMED, LIVE cross-field):** `delivery_coords` (pole „gdzie dowieźć") REUŻYTE jako **pozycja kuriera** (`cs.pos = tuple(order["delivery_coords"])`, `pos_source=last_picked_up_delivery`) → karmi km/ETA/feasibility KOLEJNEGO ordera. Błąd/zatrucie `delivery_coords` (regeocode-asym, `(0,0)`) propaguje na pozycję. Most do K5 (sentinele) + F2.
7. **F3-A (CONFIRMED, twin-asym persist):** `uwagi`+derywaty (`uwagi_pickup_parsed`, `delivery_deadline_uwagi`) persystowane w GŁÓWNEJ ścieżce NEW_ORDER (`state_machine:533-538` — Lekcja #80 naprawiona), ale **DROPOWANE w fallbacku `CorruptedTimestampError`** (`state_machine:495-514` — brak tych kluczy). + parse pickup-z-uwagi tylko przy NEW_ORDER (temporalna luka #18: edycja uwagi nie re-parsuje).

---

## F1 — `eta_pickup` (DISPLAY ∧ DECYZJA) — pełna mapa writer×konsument (świeży grep)

**Pola:** `eta_pickup_utc` (decision, datetime) → `eta_pickup_hhmm` (display, derywat `_eta_hhmm_warsaw`). Jedno pole, ≥3 role decyzyjne.

### F1.1 — WRITERZY `eta_pickup_utc` (6 site, 3 warstwy)
| Plik:linia | Co pisze | Warstwa |
|---|---|---|
| `dispatch_pipeline.py:4057` | `= arrive_pickup_utc` (`plan.pickup_at[oid] − DWELL_PICKUP_MIN`), `eta_source="plan"` | per-kandydat feasibility-loop (lokalna) |
| `dispatch_pipeline.py:4061` | `= drive_arrival_utc` (`now+drive_min`), gdy brak plan | jw. |
| `dispatch_pipeline.py:4067` | `= r07_chain_eta_utc` (override, `ENABLE_V326_R07_CHAIN_ETA`) | jw. |
| `dispatch_pipeline.py:4077` | `= now+timedelta(travel)` soon_free | jw. |
| `dispatch_pipeline.py:5287` | `metrics["eta_pickup_utc"] = eta_pickup_utc.isoformat()` | serializacja lokalnej do metryki |
| `dispatch_pipeline.py:5862` | `c.metrics["eta_pickup_utc"] = no_gps_eta_utc.isoformat()` | **post-loop nadpis** (no_gps) |
| `dispatch_pipeline.py:5877` | `c.metrics["eta_pickup_utc"] = shift_eta` (shift_start) | **post-loop nadpis** (pre_shift) |
| `shadow_dispatcher.py:291,627` | `eta_pickup_hhmm = _eta_hhmm_warsaw(eta_pickup_utc)` | derywacja display (A=kandydat, B=best) |

### F1.2 — KONSUMENCI DECYZYJNI (to czyni pole zmienną decyzyjną, nie display)
| Plik:linia | Użycie | Klasa decyzji |
|---|---|---|
| `dispatch_pipeline.py:5172-5174` | `_eta_v324 = eta_pickup_utc`; `extension_min = (_eta − pickup_ready_at)/60`; `extension_penalty(_eta, _pra)` | wejście do bramki+kary |
| `dispatch_pipeline.py:5175-5178` → **`:5610-5612`** | `_pen is None → v324a_extension_hard_reject=True` → `if … and verdict=="MAYBE": verdict="NO"` (`v324a_extension_too_large >60min`) | **HARD REJECT (feasibility-verdict)** |
| `common.py:3338` `extension_penalty()` → `:3378-3379` `if extension_min > V324_HARD_REJECT_EXTENSION_OVER_MIN: return None` (`=60`, `common.py:1823`) | None gdy ekstensja >60min | driver hard-rejectu |
| `dispatch_pipeline.py:5199` | `final_score = … + v324a_extension_penalty` (gradient −10/−50/−100/−200) | **kara SCORINGU** |
| `dispatch_pipeline.py:3189-3195` | paczka: `eta_pickup = plan.pickup_at[oid]`; `overrun = (eta_pickup−created)/60 − PACZKA_PICKUP_SOFT_CAP_MIN`; `penalty −= overrun*PACZKA_FLEX_PENALTY_PER_MIN` | **kara SCORINGU (paczka)** |
| `dispatch_pipeline.py:3196-3202` | paczka delivery overrun (analogicznie, `predicted_delivered_at`) | kara scoringu (paczka) |
| **cross-repo** `Ops13Console.tsx:835` | `assign.mutate({…, time_arg: best.eta_pickup_hhmm \|\| undefined })` na akceptacji propozycji | **→ committed (J)** |
| **cross-repo** `assign.py:42-43` | `if time_arg: cmd += ["--time", str(time_arg)]` → subprocess `przypisz-zamowienie` | ustawia `czas_kuriera` (R27-frozen) |

### F1.3 — KONSUMENCI DISPLAY (czysty render)
`telegram_approver.py:347` / `:871` / `:1318` (`eta = c.get("eta_pickup_hhmm") or c.get("eta_drive_hhmm")` — linia ETA), `shadow_dispatcher.py:291/627` (hhmm), konsola `feed.py` passthrough.

### F1.4 — Werdykt F1
- **F1-A:** `eta_pickup_utc` jest twardą zmienną decyzyjną (hard-reject `:5610` + score `:5199` + paczka `:3195`). „Display-only" = OBALONE grepem. **CONFIRMED, LIVE.**
- **F1-B (J):** derywat display `eta_pickup_hhmm` → `time_arg` → committed `czas_kuriera`. Edycja „napisu" = regres committed-promesy. Brak osobnego pola display vs decision. **CONFIRMED.**
- **F1-C (skew):** `eta_pickup_utc` liczone DWAKROĆ: (a) main-loop lokalna `eta_pickup_utc` (4057/4061/4077) karmi `extension_penalty`/score w `:5172`; (b) post-loop nadpis `c.metrics["eta_pickup_utc"]` (5862 no_gps / 5877 pre_shift) karmi display+`time_arg`+serializer. Dla pre_shift/no_gps wartość scorująca (przed nadpisem) ≠ serializowana/committed (po nadpisie). Komentarz autora `:5163` zna problem („clamp aktywny w post-loop override"). **CONFIRMED strukturalnie; materialność rozjazdu = PLAUSIBLE** (wymaga runtime: czy plan-based 4057 dla pre_shift już = shift_start, czy realnie różny od 5877; Faza C oracle).
- **Nuans-N (minor, nie liczę osobno):** `extension` (5172) kotwiczy na `plan.pickup_at − DWELL`, paczka-overrun (3193) na `plan.pickup_at` (bez DWELL). Dwa konsumenci, dwie kotwice z tego samego źródła — semantycznie obronne (przyjazd-pod vs moment-odbioru), odnotowane.
- **Coverage-PLAUSIBLE (display↔display):** konsola `Ops13Console:835` bierze `best.eta_pickup_hhmm` SUROWY; Telegram floruje display do plan/committed (`telegram_approver` + `ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN/_COMMITTED`, testy `test_proposal_eta_floor{,_to_plan}.py`). → ten sam order może mieć **inny ETA floruje-Telegram vs surowy-konsola**, a konsola commituje surowy przez `time_arg`. NIE potwierdzone runtime (Faza C).

---

## F2 — `delivery_coords` ↔ `delivery_address` (pola sprzężone, ta sama lokalizacja 2 formy)

**Reguła (protokół Załącznik A, near-miss 29.06):** gdy dwa pola reprezentują TO SAMO w różnej formie — writer aktualizujący JEDNO bez drugiego = ciche kłamstwo które przeżywa (stan utrwalony). Twin-audit = grep WSZYSTKICH WRITERÓW obu.

### F2.1 — WRITERZY (świeży grep `delivery_coords`/`delivery_address`)
| Writer (plik:linia) | `delivery_coords` | `delivery_address` | Symetria |
|---|---|---|---|
| `panel_watcher.py:1296`+`:1305` (NEW_ORDER ingest payload) | ✅ `:1305` | ✅ `:1296` | **PARA** (oba z `norm`) |
| `state_machine.py:524`+`:529` (NEW_ORDER happy persist) | ✅ `:529` | ✅ `:524` | **PARA** |
| `state_machine.py:500`+`:505` (NEW_ORDER `CorruptedTimestampError` fallback) | ✅ `:505` | ✅ `:500` | **PARA** (coords/addr — ale gubi `uwagi`, p. F3) |
| `state_machine.py:822`+`:825-826` (COURIER_DELIVERED) | ⚠ `:826` TYLKO gdy `deliv_coords` (geocode OK) | ✅ `:822` zawsze | **ASYM** (tekst zawsze / pin warunkowy) — guarded (komentarz `:816` „nie nadpisuj dobrych None'em"), terminal-state |
| `gastro_edit.py:154`+`:158` (`regeocode_and_update`) | ✅ `:154` ZAWSZE | ⚠ `:158` TYLKO gdy `ENABLE_REGEOCODE_SYNC_TEXT` | **ASYM by-default** (fix flagą) |

### F2.2 — Flaga-fix `ENABLE_REGEOCODE_SYNC_TEXT` (F2-A)
- `gastro_edit.py:157`: `if C.flag("ENABLE_REGEOCODE_SYNC_TEXT", False) and display_address.strip(): upsert["delivery_address"]=…; upsert["delivery_city"]=…`. const-default **OFF**.
- **flags.json:225 = true** (LIVE). Test `tests/test_regeocode_sync_text.py` (case 484269 Mroźna).
- **ALE (klasa D, A3 §3a):** flaga w **leaku decyzyjnym** — POZA `ETAP4_DECISION_FLAGS`, POZA `flag_fingerprint()`. Konsekwencje: (a) conftest `_isolate_flags_json` jej NIE stripuje → test z const-OFF i tak biegnie ON; (b) brak parytetu cross-proces; (c) **usunięcie klucza z flags.json → spada na const-OFF → cichy rewert asymetrii** (pin bez tekstu wraca). `gastro_edit` biega jako subprocess z konsoli (A5) — czyta flags.json venva dispatch → dziś OK, ale krucho.
- Detektor: `address_mismatch.py:225-239` (`ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW`=true, `:130` komentarz wprost o regeocode-asym) — shadow-only, mierzy rozjazd tekst↔pin po edycji (review at-198 01.07 TEKST↔PIN).

### F2.3 — KONSUMENCI (rozdział tekst↔pin = oś split-brain)
**`delivery_address` (TEKST) → district/strefa (`drop_zone_from_address`):**
| Plik:linia | Użycie | Klasa |
|---|---|---|
| `same_restaurant_grouper.py:84-85` | `addr = getattr(o,"delivery_address") or drop_address` → grouping district | SOFT (bundling po dystrykcie) |
| `dispatch_pipeline.py:940-941` | `last_drop_addr = bc[-1]["delivery_address"]`; `last_drop_district = drop_zone_from_address(...)` | SOFT (trajektoria/wave) |
| `insertion_anchor.py:127` | `getattr(anchor_order,"delivery_address")` | SOFT (anchor wstawienia) |

**`delivery_coords` (PIN) → geometria:**
| Plik:linia | Użycie | Klasa |
|---|---|---|
| `feasibility_v2.py:473-474` | `r7_ride_km = _road_km(pickup_coords, delivery_coords)` | HARD-kształt R7 (martwy stałą, ale czyta pin) |
| `feasibility_v2.py:499-500` | `_max_deliv_spread_km(bag, new.delivery_coords)` (R1) | SOFT/metric R1 |
| `feasibility_v2.py:518-562` | R5 pickup-spread / cross-quadrant z `delivery_coords` | SOFT/metric R5 |
| `plan_manager.py:262,286-289` | `delivery_coords` do stopa planu (lat/lng) | KANON (route) |
| `feasibility_v2.py:174,277` / `obj_replay_capture.py:39` | geometria worka / replay | feasibility/INSTR |

**Werdykt F2-B:** tekst karmi SOFT-district, pin karmi HARD/SOFT-geometrię. Asymetryczny writer (regeocode flag-OFF, lub delivered geocode-fail) = **HARD-geometria na nowym pinie, SOFT-district na starym tekście** = niespójna decyzja w obrębie jednego ordera. Near-miss 484269 udokumentowany. **CONFIRMED** (fix żywy, ale leak-fragile).

### F2.4 — `delivery_coords` jako POZYCJA KURIERA (F2-C, cross-field reuse)
`courier_resolver.py:740-741`: `if order.get("delivery_coords"): cs.pos = tuple(order["delivery_coords"]); cs.pos_source="last_picked_up_delivery"` (+ bliźniak `:1004-1005`). Pole „gdzie dowieźć order" → **pozycja kuriera** dla liczenia km/ETA/feasibility KOLEJNEGO ordera (F4 fallback, gdy brak `pickup_coords`/interp). `:1011` log „bez delivery_coords — data quality alert (P0.4)".
- **Semantyka:** delivery-destination = courier-position-proxy. Jedno pole, dwie role (jak `eta_pickup`, ale przestrzenne).
- **Sprzężenie z F2-A/K5:** zatrute/stałe `delivery_coords` (regeocode-asym pin, sentinel `(0,0)`/`BIALYSTOK_CENTER`) → zła pozycja → złe km/feasibility następnego. A6 GRUPA-7/K5 notuje `(0,0)` `delivery_coords`→haversine sentinel→`V328_CP_SOLVER_FAIL` wyrzuca zajętego kuriera. **CONFIRMED LIVE** (flagi `ENABLE_F4_COURIER_POS_*` ON w ETAP4 effective — A3 §2d). Severity: nie-bug gdy coords dobre, ale propaguje błąd F2 w pozycję.

---

## F3 — `uwagi` (pole-na-granicy-warstw, free-text z osadzonymi derywatami)

**Semantyka:** `uwagi` (gastro free-text) niesie DWA osadzone payloady decyzyjne: (1) ADRES PICKUP firmowego konta (aid∈FIRMOWE_KONTO_ADDRESS_IDS) → `parse_pickup_from_uwagi`→coords; (2) DEADLINE DOSTAWY czasówki → `delivery_deadline_uwagi`. Lekcja #80: `panel_client` parsował `uwagi`, `state_machine` DROPOWAŁ → audit konsumentów przy nowym polu na granicy source-of-truth.

### F3.1 — Ścieżka pola (writer→persist→konsument)
| Etap | Plik:linia | `uwagi` | Derywaty |
|---|---|---|---|
| ingest payload | `panel_watcher.py:1311` | ✅ `norm.get("uwagi")` | ✅ `:1312` `uwagi_pickup_parsed` |
| persist HAPPY | `state_machine.py:533` | ✅ | ✅ `:534` `uwagi_pickup_parsed`, `:538` `delivery_deadline_uwagi` |
| **persist FALLBACK** (`CorruptedTimestampError`) | **`state_machine.py:495-514`** | ❌ **BRAK klucza** | ❌ **BRAK** `uwagi_pickup_parsed`/`delivery_deadline_uwagi` |
| konsument (parse pickup) | `panel_watcher.py:1210-1281` | czyta `norm["uwagi"]` → coords | TYLKO NEW_ORDER |
| konsument (deadline) | `czasowka_uwagi.py:53` `parse_delivery_deadline` | shadow (`ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW`=OFF, **brak konsumenta decyzyjnego**) | — |
| konsument (czasówka render) | `czasowka_scheduler.py:332,490` | `order_state.get("uwagi")` | — |

### F3.2 — Werdykt F3
- **F3-A (twin-asym persist):** ta sama funkcja `state_machine` ma DWIE ścieżki persist NEW_ORDER; happy (`:519+`) niesie `uwagi`+3 derywaty, fallback corrupt-ts (`:495+`) je gubi. Wzorzec #1 (fix w 1 z N ścieżek) zastosowany do POLA. **CONFIRMED strukturalnie.** Impact dziś NISKI: (a) `pickup_coords` persystowane w obu (`:504`/`:528`) → główna decyzja firmowego przeżywa; (b) `upsert_order` MERGE'uje (`state_machine:815` komentarz) → brak klucza ZACHOWUJE istniejący `uwagi` (strata tylko dla PIERWSZEGO-w-życiu NEW_ORDER trafiającego w corrupt-path, bo event_id `{zid}_NEW_ORDER_first` idempotentny `panel_watcher:1323`); (c) `delivery_deadline_uwagi` = shadow bez decyzji. **Latentny P2** gdy czasówka-uwagi deadline dostanie konsumenta decyzyjnego (flip `ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW`). still_open.
- **F3-B (temporalna luka #18):** parse pickup-z-uwagi (`panel_watcher:1210`) odpala TYLKO przy NEW_ORDER. `gastro_edit.regeocode_and_update` re-geokoduje DELIVERY (nie pickup-z-uwagi). → edycja `uwagi` firmowego konta (zmiana adresu pickup) NIE re-parsuje `pickup_coords`. Wąski edge (firmowe + edycja uwagi), ale klasyczna „sygnał rodzi się downstream, hook tylko przy tworzeniu". P3.
- **Granica cross-repo (osobne pole, nie ten sam):** panel ma kolumnę `notes` (deliveries DB, `ebc6a1f...baseline_full_panel_schema.py`) — parcel pakuje nadawcę/rozmiar do `notes` (`PARCEL_TODO.md`), board-parser rezerwuje frazę `kurier:` w notes (MEMORY). To INNE pole niż gastro `uwagi`; konsola edit pcha `uwagi`→gastro przez `update-zamowienie` (`flags.systemd.env:85`). Odnotowane jako rozdzielne — NIE mylić.

---

## TABELA POKRYCIA (jawne — co zbadane, czego NIE)

| Pole | Writerzy sprawdzeni | Konsumenci sprawdzeni | Status |
|---|---|---|---|
| `eta_pickup_utc`/`_hhmm` | 8 site (dispatch_pipeline ×7, shadow_dispatcher ×2) | decyzja ×6 (extension hard-reject+score, paczka ×2, cross-repo time_arg×2) + display ×4 | CONFIRMED F1-A/B; skew F1-C PLAUSIBLE-materialność |
| `delivery_coords` | panel_watcher, state_machine ×3, gastro_edit | feasibility R1/R5/R7, plan_manager, courier_resolver F4-pozycja, obj_replay | CONFIRMED F2-A/B/C |
| `delivery_address` | panel_watcher, state_machine ×3, gastro_edit (flag) | drop_zone_from_address (grouper/pipeline/insertion_anchor) | CONFIRMED F2-B |
| `uwagi`+derywaty | panel_watcher:1311, state_machine:533 (happy) / 495 (fallback-DROP) | parse pickup (NEW_ORDER), czasowka_uwagi (shadow), czasowka_scheduler | CONFIRMED F3-A/B |

**COVERAGE GAPS (luka jawna, nie cisza):**
1. **Materialność F1-C skew** (czy pre_shift plan-based 4057 ≡ post-loop 5877 dla realnego kandydata) — NIE policzona runtime; wymaga Fazy C oracle (replay 1 pre_shift case + diff scoring-eta vs serialized-eta). Deklaruję strukturalnie, NIE liczbą.
2. **Display↔display (Telegram-floor vs konsola-surowy `eta_pickup_hhmm`→time_arg)** — PLAUSIBLE, nie potwierdzony runtime (czy floor-to-plan zmienia committed). Faza C.
3. **courier-app Kotlin** — czy apka lokalnie re-renderuje/edytuje `eta_pickup`/`uwagi` (poza serwerowym build_view) — NIE czytany kod Kotlin (granica; A6 LUKA #1 ta sama).
4. **Most paczki** — czy `parcel_lane` niesie własny `uwagi`/`notes`↔coords (natywny tor orders_state) — NIE prześwietlony (A6 LUKA #2).
5. **`czas_kuriera` jako pole** — analizowany TYLKO jako DOWNSTREAM-target `time_arg` (F1-B); pełna rodzina `czas_kuriera`/`pickup_at` closest-day = osobny temat (MEMORY [[czas-kuriera-closest-day-anchor]]), poza F.
6. **Pełna lista konsumentów `drop_zone_from_address`** — spot-checked 3 (grouper/pipeline/insertion_anchor); nie każdy z ~10 callsite districts re-grepowany 1:1.
7. **NIE-luki (świadomie):** Mailek/Papu (granica STOP). Flagi efektywne per-proces = A3. Floor pickup≥shift_start = A6 GRUPA 6 (inny root). Route-order = A6 GRUPA 2.

---

## DEDUP / ROLLUP (anty-double-count, do Fazy E)

| Instancja | Root A6 | Uwaga dedup |
|---|---|---|
| F1-A/B/C (`eta_pickup` display=decyzja) | **R5 (display≠decision)** | A6 GRUPA 7 = ten root; ja rozszerzam o cross-repo time_arg→committed + skew dwóch komputacji. NIE nowy root. |
| F2-A/B (`delivery_coords`↔`address` asym) | **NOWY pod-root klasy F** (A6 oddał do „Faza B-F") | „one coupled-address contract": para pisana razem; fix-flag w rejestrze. Sprzężony z D (leak `ENABLE_REGEOCODE_SYNC_TEXT`). |
| F2-C (`delivery_coords`→pozycja) | most **R5(F)+K5(sentinele)** | cross-field reuse; zatrucie coords→pozycja. Współ-raportowane z agentem sentineli (K5). |
| F3-A/B (`uwagi` boundary) | **NOWY pod-root klasy F** | „field-survives-every-recreation": twin persist (happy/fallback) + temporalny parse. Sprzężony z B (wzorzec #1 w polu). |

**Kontrakt docelowy (DRAFT, Faza F):** (1) `eta_pickup` — rozdziel `eta_pickup_decision` (jedyne wejście scoring/feasibility/time_arg) od `eta_pickup_display` (derywat, NIGDY z powrotem do decyzji); jedna komputacja, nie dwie. (2) `delivery_*` — JEDEN writer-kontrakt „pisz parę (coords,address,city) atomowo albo świadomie N-D"; `ENABLE_REGEOCODE_SYNC_TEXT`→ETAP4+fingerprint, potem retire (zawsze-sync). (3) `uwagi` — derywaty (`pickup_parsed`/`deadline`) liczone w JEDNYM miejscu które każda ścieżka persist wywołuje (happy∧fallback), + sweep utrwalonego stanu zamiast hooka-przy-tworzeniu (#18). **STOP — to PLAN, nie naprawa.**
