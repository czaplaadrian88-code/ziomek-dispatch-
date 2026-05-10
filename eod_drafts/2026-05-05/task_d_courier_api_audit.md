# TASK D Pre-Condition — `courier-api.service` Audit (port 8767)

**Date:** 2026-05-05 (przed TASK D Cz 07.05)
**Mode:** READ-ONLY discovery, zero kodu/zero deploy
**Outcome:** **CRITICAL FINDING — courier-api NIE MA endpointu rejestracji**. D.4/D.5 spec wymaga re-design (patrz "Recommended TASK D D.4 design" niżej).

---

## Service overview

- **systemd unit:** `/etc/systemd/system/courier-api.service`
- **WorkingDirectory:** `/root/.openclaw/workspace/scripts/courier_api/`
- **ExecStart:** `/root/.openclaw/workspace/scripts/courier_api/.venv/bin/python main.py`
- **Type:** simple, `Restart=on-failure`, `User=root`
- **Logs:** `/root/.openclaw/workspace/scripts/logs/courier_api.log`
- **Runtime:** active since 2026-05-03 23:21 UTC (PID 1119321, 18.4 MB RSS)
- **Bind:** `0.0.0.0:8767` (config.py)
- **Code path:** `/root/.openclaw/workspace/scripts/courier_api/` (FastAPI 0.2.0)
- **DB:** SQLite `/root/.openclaw/workspace/dispatch_state/courier_api.db` — 3 tabele: `sessions`, `gps_history`, `pin_attempts`. **ZERO tabeli `couriers`** — kurierzy NIE są materializowani w courier-api DB.

---

## Endpoints discovery (full inventory)

Z `main.py` + `routes/admin.py` + `routes/fleet.py`:

| # | Method | Path | Auth | Purpose |
|---|--------|------|------|---------|
| 1 | GET | `/api/ping` | none | health check |
| 2 | GET | `/api/couriers` | none | list `[{id, name}]` z `kurier_ids.json` (READ) |
| 3 | POST | `/api/auth/select` | PIN | login Android: PIN→token, rate-limited 5/15min |
| 4 | POST | `/api/auth/logout` | Bearer token | revoke session |
| 5 | POST | `/api/gps/batch` | Bearer token | append GPS batch (Android app upload) |
| 6 | GET | `/panel/login` + POST | basic | admin panel login (cookie session) |
| 7 | GET | `/panel/api/positions/stream` | admin cookie | SSE live mapa |
| 8 | GET | `/panel/api/couriers/{cid}/trail` | admin cookie | last 200 GPS points |
| 9 | GET | `/api/fleet/daily-km` | admin cookie | km per day per courier |
| 10 | GET/POST | `/api/fleet/vehicles` (+ /{id} update) | admin cookie | vehicle CRUD |
| 11 | GET/POST | `/api/fleet/assignments` (+ /{id}/close) | admin cookie | courier↔vehicle assignment |
| 12 | GET | `/api/fleet/couriers` | admin cookie | list (read kurier_ids) |
| 13 | GET/POST | `/api/fleet/orders` (+ helpers) | admin cookie | revenue/cost reads |
| 14 | GET/POST/DELETE | `/api/fleet/fuel-entries` (+ summary) | admin cookie | fuel CRUD |

**Highlight register/add/create/new candidates dla D.4:** **NONE.** `grep -rn "register\|create_courier\|new_courier\|add_courier\|onboard"` w `courier_api/` → **zero matches.** courier-api jest READ-ONLY consumerem `kurier_ids.json` + `kurier_piny.json` — nie ma endpointu który by tworzył nowego kuriera.

---

## Authentication mechanism

Trzy odrębne mechanizmy:

1. **Android courier app** — Bearer token (`auth.py:create_session` zwraca `secrets.token_urlsafe(32)`, expiry 30 dni hard + 90 min idle auto-logout). Login via PIN matching `kurier_piny.json` → name → `kurier_ids.json` → cid. Rate limit 5 failed/15 min per cid (HTTP 429).
2. **Admin panel (dispatch UI)** — basic login `admin / nadajesz2026` (config.py hardcoded), session cookie `nadajesz_admin` 12h TTL, in-memory dict.
3. **GPS batch endpoint** — wymaga Bearer token z #1.

**Internal-only / service-to-service auth:** **BRAK.** courier-api nie ma API key ani internal-network shared secret. Wszystkie endpointy są albo PIN-based (Android), albo cookie-based (panel UI) — żadne nie nadają się pod call z dispatch_v2 onboarding workflow bez impersonacji.

---

## Required fields dla register (POST/PUT new courier)

**N/A — endpoint nie istnieje.** Jedyne POST schemas (Pydantic w main.py + fleet.py) dotyczą:

- `SelectRequest`: `courier_id`, `pin`, `device_id?`, `device_model?`, `app_version?` (login, NIE create).
- `VehicleCreate`, `AssignmentCreate`, `FuelEntryCreate` (fleet, NIE courier).

Dla nowego kuriera ground-truth = pliki JSON pisane przez DISPATCH_V2 (nie przez courier-api):
- `kurier_ids.json` — `{name: cid}` (int)
- `kurier_piny.json` — `{pin: name}` (4-digit string keys)
- `courier_tiers.json` — tier classification (zewnętrzny do courier-api)

---

## Rollback semantics

- **DELETE endpoint dla courier:** **NIE ISTNIEJE.**
- **Soft delete:** revocation sesji possible via `auth.revoke_courier_sessions(cid)` — ale to wewnętrzna funkcja, NIE expose'owana jako endpoint.
- **Implications dla TASK D atomic transaction:** klasyczny problem "step 4 = no-op write do JSON files" — courier-api konsumuje pliki on-the-fly (każdy `_load_json_safe` czyta świeży snapshot). Nie ma stanu w courier-api do rollback'u — DB courier-api NIE materializuje kurierów, więc rollback steps 1-3 (revert JSON files) JEST kompletny rollback.
- **Konsekwencja:** Adrian's spec D.4 "GPS app registration via courier-api.service port 8767 [NEW]" wymaga **albo** zbudowania nowego endpointu `POST /api/admin/couriers` w courier-api (TASK D scope expansion), **albo** redefiniowania step 4 jako side-effect step 1+3 (write `kurier_ids.json` + `kurier_piny.json` = courier "automatically registered" w GPS app perspektywie).

---

## Edge cases / unknowns

- **Crash mid-write:** courier-api jest read-only; same JSON files (kurier_ids, kurier_piny) writeowane przez dispatch_v2 z atomic temp→fsync→rename pattern. Crash = no-op dla courier-api (kolejny `_load_json_safe` cacheless re-read świeżego pliku).
- **Concurrent register:** `kurier_ids.json` writeowany różnymi codepathami w dispatch_v2 (kurier_ids tools, daily_accounting, ad-hoc skrypty) — brak globalnego lockfile. Race window jest realne ale outside courier-api scope.
- **Existing cid same name:** `kurier_ids.json` to `{name: cid}`, więc "Adrian C." key collision = overwrite cichy. **Pre-condition check w D.4 obligatory.**
- **GPS device pre-registration:** nie ma sensu — courier-api auth via PIN→name→cid, więc dopóki PIN istnieje w kurier_piny.json + name w kurier_ids.json, każde Android device'em może się zalogować. Nie ma per-device pre-registration.

---

## Recommended TASK D D.4 design (post-audit revision)

**KEY INSIGHT:** "GPS app registration" w Adrian's spec = **side-effect write do `kurier_ids.json` + `kurier_piny.json`**, NIE oddzielny POST do courier-api. courier-api to czysty consumer; nie ma własnego stanu kuriera. **Step 4 nie istnieje jako external service call.**

**Revised order operacji (3 steps NIE 4):**

1. `kurier_ids.json` write (atomic temp→fsync→rename, lockfile)
2. `courier_tiers.json` write
3. `kurier_piny.json` write (LAST — bo jeśli PIN jest, GPS app zaraz się zaloguje)

**Rollback:** revert 3 JSON files w odwrotnej kolejności (3→2→1). Brak external service call = brak external rollback.

**Idempotency:** pre-write check `if name in kurier_ids: reject ALREADY_EXISTS` — pełny "already exists" semantyka klient-side w D.4.

**Logowanie:** każdy step do `learning_log.jsonl` event=`ONBOARDING_KURIER_STEP_{1,2,3}` z `outcome={success,fail}` + atomic transaction id.

**Smoke-test po onboarding:** `curl -s http://localhost:8767/api/couriers | jq '.[]|select(.name=="<NEW_NAME>")'` — jeśli zwraca cid, GPS app gotowy. Brak osobnego registration call.

**Optional D.5 enhancement (future, NIE blocking 07.05):** dodać `POST /api/admin/couriers` endpoint w courier-api z basic auth (admin/nadajesz2026), dla scenariusza "GUI onboarding" — ale to wykracza poza atomic transaction wymagania.

---

## 5 unknowns dla Adriana

1. **Czy "GPS app registration" w spec D.4/D.5 oznacza external POST do courier-api, czy side-effect write do JSON files?**
   *Recommended default jeśli brak odpowiedzi:* side-effect (3-step transaction, NIE 4-step). Audit potwierdza brak endpointu register.

2. **Czy chcesz w TASK D DODAĆ nowy endpoint `POST /api/admin/couriers` do courier-api (scope expansion +1.5h)?**
   *Recommended default:* NIE — defer do dedicated TASK (np. D.5b), żeby D.4 zmieścić w 4-5h time-box.

3. **Czy `courier_tiers.json` to istniejący plik czy NEW (TASK D scope creates it)?**
   *Recommended default:* sprawdzić `ls /root/.openclaw/workspace/dispatch_state/courier_tiers.json` przed sprintem; jeśli istnieje — append-write, jeśli nie — TASK D D.2 inicjuje plik z atomic create.

4. **Czy chcesz pre-onboarding lockfile globalny dla `kurier_ids.json` (race protection przeciw concurrent writes z innych skryptów daily_accounting/maintenance)?**
   *Recommended default:* TAK, fcntl.LOCK_EX na sentinel `/tmp/kurier_ids.lock` 5s timeout — D.4 atomic_register_courier() wraps wszystkie 3 writes w jedno lock-acquire/release.

5. **Czy onboarding wymaga restart jakichkolwiek services (panel-watcher konsumuje kurier_ids.json snapshotem? courier-api?), czy oba serwisy są live-reload (`_load_json_safe` per request)?**
   *Recommended default:* live-reload — courier-api auth.py potwierdzony (`_load_json_safe` per `resolve_pin` call), panel-watcher TBD ale based on Lekcja #47 prawdopodobnie też. Zero restart wymagany.

---

## Pliki referencyjne (do TASK D sprintu 07.05)

- `/root/.openclaw/workspace/scripts/courier_api/main.py` (226 linii — endpointy)
- `/root/.openclaw/workspace/scripts/courier_api/auth.py` (172 linie — PIN resolver, sessions)
- `/root/.openclaw/workspace/scripts/courier_api/config.py` (29 linii — paths, secrets, ports)
- `/root/.openclaw/workspace/scripts/courier_api/db.py` (114 linii — schema v2, brak couriers table)
- `/root/.openclaw/workspace/scripts/courier_api/routes/fleet.py` (~580 linii — assignments, vehicles, fuel)
- `/root/.openclaw/workspace/scripts/courier_api/routes/admin.py` (242 linie — admin panel SSE/login)

**Audit time:** ~15 min (well under 45 min budget).
