# CLAUDE.md additions — drop-in sections

Trzy podsekcje do dorzucenia w `dispatch_v2/CLAUDE.md` (kolejność dowolna, sugerowana w sekcji "Roadmap" lub "Working Process").

---

## TASK D — Auto-Discovery + Onboarding Pipeline (czwartek 07.05, ~3h estimated)

**Revision 2026-05-05** post audit `courier-api.service` port 8767 (Agent #3 audit dziś):
courier-api jest **READ-ONLY consumer** plików `kurier_ids.json` + `kurier_piny.json` — NIE ma endpointu rejestracji. DB ma 3 tabele (sessions, gps_history, pin_attempts), brak `couriers` table. Stan kuriera consumed wyłącznie z 2 JSON plików.

**Implication:** atomic transaction redukuje się do **3 JSON file writes** (NIE 4 ze step "GPS app registration"). Step 4 (jeśli był) = **no-op**. Rollback prostszy — zero external service in chain (mniejsze ryzyko partial-failure).

**Components (revised):**

- **D.1 Detection unmapped in schedule** — worker integration; check schedule_today.json names vs kurier_ids.json (delta detection); flag `ENABLE_AUTO_DISCOVERY=false` default. **Reuse:** Sprawa #1 audit logic z `migrate_couriers_2026-05-05.py:_audit_unmapped()`.

- **D.2 Telegram UI + auto-PIN suggest + 2-button flow** — DM do Adriana z formatem (per Adrian's spec):
  ```
  📋 Wykryto nowego kuriera: {imie}
  Schedule: {start}-{end}
  Sugerowany cid: {auto-suggest next available}
  Sugerowany tier: Standard
  Sugerowany PIN: {auto-gen 4-digit collision-checked}
  
  [Tak, dodaj]   [Nie teraz]
  ```
  Auto-PIN: 4-digit random, collision-checked vs `kurier_piny.json`, exclude obvious patterns (0000/1111/1234/4321/repeating/ascending). **Reuse:** Sprawa #1 `generate_pin()` helper.

- **D.3 /add_kurier command parser + validation** — alternative manual entry:
  - `/add_kurier [cid] [tier] [pin]` — explicit PIN
  - `/add_kurier [cid] [tier]` — auto-generated PIN
  - Tier walidacja case-insensitive: gold | Std+ | Standard | Slow

- **D.4 Atomic transaction 3 JSON stores (revised, all-or-nothing rollback)** — kolejność:
  1. `kurier_ids.json` (panel_name → cid)
  2. `courier_tiers.json` (cid → tier)
  3. `kurier_piny.json` (cid → pin)
  
  **Pattern:** fcntl.LOCK_EX per-store + tempfile + fsync + os.rename. Rollback: jeśli step N fail → revert N-1, ..., 1. Per Lekcja #71 + Wytyczna #1 4-checkbox.
  
  **Reuse:** Sprawa #1 `migrate_one()` helper z `migrate_couriers_2026-05-05.py`.

- **D.5 Smoke verify post-onboarding (revised, NIE GPS app integration)** — read-only check że nowy kurier widoczny dla courier-api (consumer):
  - `curl -X GET http://localhost:8767/api/couriers/list` (lub equivalent)
  - Verify response contains new courier (panel_name + cid)
  - Eventually-consistent timeline: courier-api refresh consumer cache co N sekund (sprawdź interval w service config)
  - Integration test: post-onboarding wait, then verify
  - Jeśli timeout (>30s) → log warning ALE migration uznane za success (filesystem state correct)

- **D.6 NEW candidate — Telegram welcome message** (post-simplification headroom):
  - Po success migracji, bot wysyła DM do nowego kuriera z welcome:
  ```
  🚀 Witaj w NadajeSz, {imie}!
  
  Twój login do panelu kuriera (https://gps.nadajesz.pl):
  PIN: {pin}
  
  Pobierz aplikację: https://gps.nadajesz.pl/apk/courier.apk
  Po instalacji wpisz PIN i zaloguj się.
  
  Pytania → DM Adrian (Telegram).
  ```
  - **Wymaga:** kurier ma chat_id z botem (czy `/start` wysłany?). Bot NIE może DM do user który nigdy nie wysłał `/start` (Telegram restriction).
  - **Edge case:** jeśli kurier nie zrobił `/start`, fallback: Adrian dostaje "PIN do wysłania ręcznie {imie}: {pin}" → Adrian forwards via Telegram/SMS.
  - **Defer decision:** Adrian akceptuje D.6 scope czy zostać przy D.5 simple smoke verify?

**Pre-condition Sr 06.05 audit:** ❌ NIE WYMAGANE (audit już done dziś — Agent #3 task_d_courier_api_audit.md). 

**Sprint estimate revised:** ~**3h** (vs 4-5h pre-revision). Time saved przez D.4 simplification (~1.5h) + D.5 smoke verify simpler niż integration tests (~0.5h).

**5 unknowns dla Adriana** (czwartek przed sprint kickoff):
1. Auto-suggest cid: next available numeric? Adrian provides? Lookup last_max_cid + 1?
2. D.6 scope: implementuj welcome message lub defer V3.30?
3. Tier default Standard — czy override per courier obvious "gold candidate" (Adrian może chcieć designate)?
4. /add_kurier authorization: mirror KONIEC_AUTHORIZED_USER_IDS (Adrian + Bartek)?
5. Test fixture dla cross-store rollback: jak symulować step 2 fail? Mock fs? Mock json.dump exception?

---

## TASK E — Geocoding Outside-City Fix

Background diagnostic z 05.05 PROVED: `dispatch_pipeline.py:421` hardcoded `"Białystok"` + 16 unmapped sat cities w `events.db`. Impact 36.7% NEW_ORDER + 148 orders / 30d.

**Phase 1 (Sr 06.05, 4h, Components 1-3):**
- **C.1 zones_registry hierarchical** — primary city + sat-cities array; replace hardcoded literal.
- **C.2 geocoding upgrade** — multi-token Nominatim (Lekcja Nominatim single-token) + `verified=true|false` flag; fallback path zachowany.
- **C.3 drop-zone outside-city** — feasibility gate dla orders gdzie pickup OR drop poza primary city; SOFT penalty + Telegram TRASA section transparency.

**Phase 2 (Pt 08.05, 3-4h, Components 4-8):**
- **C.4 OSRM bbox + synthetic pos by city** — bounding box queries dla cross-zone routing; synthetic position fallback per-city (NIE always BIALYSTOK_CENTER).
- **C.5 Satellite adjacency rozszerzony** — 16 sat cities adjacency map (CC pre-build dziś ~12:00 UTC `geocoding_adjacency_draft_2026-05-06.md`; Adrian ACCEPT/REJECT per-edge Sr rano ~15 min).
- **C.6 Migration plan dla existing data** — backfill `events.db` 30d window + cache invalidation strategy (lazy on-read).
- **C.7 30+ test cases obowiązkowych** — per Adrian Z3 design.
- **C.8 Granularne flagi + sequencing deploy** — `ENABLE_OUTSIDE_CITY_GEO_*` default false; per-component gradual flip z 5s rollback gate.

**Pre-condition Adrian Sr rano (~15 min):** review adjacency draft (CC pre-built dziś), apply ACCEPT/REJECT per-edge → input dla Component 5 implementation.

---

## Wytyczna #1 — 4-checkbox Pre-Implementation Review (Z3 grade)

Cloud Claude obserwacja: **3 systemic gaps wykryte w 24h** (phantom bag V3.14, auto-discovery D.1-D.6, onboarding pipeline 3-store atomic post-audit) — wszystkie wzorzec Lekcji #71 (state mutation w 2+ miejscach bez atomic guarantee).

**Każda nowa feature MUSI przejść 4-checkbox przed implementacją:**

1. **Czy feature dotyka stanu w 2+ miejscach?**
   (e.g. JSON store + DB + cache + memory dict; jeśli TAK → wymaga atomic transaction design)
2. **Czy każda mutation ma atomic transaction guarantee?**
   (tempfile + rename + fsync, OR DB transaction + commit; brak partial-write race)
3. **Czy każda failure path ma rollback semantics?**
   (rollback per-step OR rollback final-state; brak "half-applied" outcomes)
4. **Czy są integration tests cross-store consistency?**
   (test scenario: store A applied, store B fails → assert store A reverted; happy path NIE wystarczy)

**Jeśli któryś = NIE → STOP, redesign przed implementacją.**

Apply to: TASK D (D.4 atomic 3-store mandatory post-audit revision 2026-05-05), TASK E (C.7 migration rollback path), Sprawa #1 (831 LoC migration — verify 3-store atomicity exists), wszystkie nowe sprintsy w tym tygodniu.

Cross-ref Lekcja #71 (test isolation = same root cause class), Lekcja #72 candidate (granular flag rollback enables safe deploys).
