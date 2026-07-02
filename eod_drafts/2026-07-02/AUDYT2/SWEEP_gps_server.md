# SWEEP — PRODUCENT POZYCJI (`gps_server.py` + `dispatch-gps` + rodzina writerów)

**Lane:** AUDYT 2.0 — górny bieg rodziny K5 (sentinel-as-data), nigdy wcześniej nie sweepowany.
**Data:** 2026-07-01 ~22:10 UTC · **Tryb:** READ-ONLY (0 mutacji, 0 restartów, 0 zapisów do dispatch_state/logs).
**Oracle:** `fleet_position_history.jsonl` okno 48h (6055 rek. / 17 cid) + logi 14 dni + żywe procesy/porty.
**Kanon:** `ZIOMEK_ARCHITECTURE.md` (10 warstw, 6 filarów, 8 kontraktów, rejestr bliźniaków).

---

## 🎯 WERDYKT W JEDNYM ZDANIU
**Dzisiejszy fix L2.1 (walidator coords u ingest, `ENABLE_COORD_SENTINEL_INGEST_GUARD` ON) wylądował w MARTWYCH drzwiach.** Jedyny producent z guardem — `dispatch_v2/gps_server.py` (port 8766, PWA) — w 14 dni przyjął **0 pozycji** (54 511 linii logu, same `GET /`). Żywy producent to `courier_api/gps_writer.py` (port 8767, `source="android"`), który **nie ma żadnej walidacji coords** poza Pydantic range `±90/±180` — więc **(0,0) i każdy punkt „w zakresie, ale poza metropolią" przechodzi do store'a i do `courier_resolver` bez filtra**, trafia w haversine i wyrzuca zajętego kuriera z puli — **dokładnie mechanizm K5, który L2.1 miało zamknąć u źródła.** To podręcznikowy „fix trafił w jeden bliźniak" (kontrakt ③).

---

## MAPA PRODUCENTÓW POZYCJI (3 żywe procesy, nie 1)

| # | Producent | Port / proces | Store | Guard coords u wejścia | Żywy? (dowód) |
|---|---|---|---|---|---|
| A | `dispatch_v2/gps_server.py` (kanon, systemd `dispatch-gps`) | 8766 · pid 1062937 (restart 21:26 = po L2.1) | `gps_positions_pwa.json` (`source="pwa"`) | ✅ **L2.1** `_ingest_coords_ok` (bbox) + range | ❌ **0 POST /gps / 14 dni** |
| B | `courier_api/gps_writer.py` (FastAPI, apka natywna) | 8767 · pid 316833 | `gps_positions_pwa.json` (`source="android"`) + SQLite `gps_history` | ❌ **brak** (tylko Pydantic `ge=-90/le=90`) | ✅ **LIVE** (store=android; `POST /api/gps/batch 200`) |
| C | `/root/gps_server.py` (legacy Traccar, poza repo, poza systemd) | 8765 · pid 1010 (PPID=1, od 27.05) | `gps_positions.json` (klucz=IMIĘ) | ❌ **brak** (+ bare `except:`) | 🟡 store pusty `{}` od 11.06, ale proces + endpointy żyją |

Konsument scalający: `courier_resolver._load_gps_positions` (PWA primary, legacy fallback) — **bez guardu coords** (l.486–497).

---

## FINDINGI

### 🔥 F1 — [P0 · ŹRÓDŁO] L2.1 w martwym producencie; żywa ścieżka android bez guardu → K5 otwarte
- **Mechanizm:** guard sentinel-ingest istnieje TYLKO w `gps_server.py:105-114` (`_ingest_coords_ok`, flaga ON w `flags.json`). Ten producent (8766) nie przyjmuje pozycji. Żywy `courier_api/main.py:299-346` (`POST /api/gps/batch`) → `gps_writer.py:57-73` (`update_pwa_position`) pisze coords **bez bbox/sentinela**; jedyna bramka to `main.py:88-89` (`GpsPoint.lat ge=-90/le=90`, `lon ge=-180/le=180`) — **(0,0) mieści się w zakresie i przechodzi.** Dalej `courier_resolver.py:486-497` kopiuje rekord z PWA store **bez filtra** → sentinel wpada do fleet snapshot → downstream haversine raisuje → V328 wyrzuca ZAJĘTEGO kuriera z puli (8–28 ofiar/dzień wg opisu L2.1).
- **Źródło vs objaw:** czyste **źródło** — reguła „coords ∈ bbox lub odrzut u INGEST" (warstwa 2 kanonu) żyje w 1 z ≥2 producentów.
- **Dowód:** `gps_positions_pwa.json` → `"source":"android"`; grep walidacji coords w `courier_api/*.py` = brak (jedyny `if lat==0.0 and lon==0.0` jest w KONSUMENCIE `courier_orders.py:100`, nie u ingestu); `courier_api.log` → seria `POST /api/gps/batch 200`; `gps_server.log` (54 511 linii) → wyłącznie `GET /` + `favicon`.
- **Klasy:** bliźniaki ③ · sentinele/cicha-awaria ⑬ · warstwy ② · N-kopii ①.
- **Naprawa u źródła (kierunek, NIE wykonana):** jeden walidator coords (import `coords_in_bialystok_bbox`) w `courier_api` PRZED `update_pwa_position` i `insert_history_batch`, ta sama flaga; odrzut → kurier=no_gps (równe traktowanie, Adrian 29.06), NIE zatruta geometria. Docelowo: wspólny moduł ingestu importowany przez OBU producentów.

### 🔥 F2 — [P1 · ŹRÓDŁO] Dwaj pisarze tego samego JSON bez wzajemnego wykluczenia cross-proces + writer zduplikowany 1:1
- **Mechanizm:** `gps_positions_pwa.json` pisany przez DWA procesy: `gps_server.py:47,119-129` (`_write_lock` = `threading.Lock`) ORAZ `courier_api/gps_writer.py:20,63-73` (`_pwa_write_lock` = INNY `threading.Lock`). To dwa różne locki w dwóch procesach — nie chronią się wzajemnie. `flock(LOCK_EX)` (obie: `gps_server.py:74`, `gps_writer.py:43`) trzymany na UNIKALNYM pliku `tempfile.mkstemp(...)`, więc **cross-proces bezużyteczny**. `os.replace` jest atomowy per-zapis, ale cykl read-modify-write NIE jest atomowy między procesami → klasyczny lost-update (A czyta `{x}`, B czyta `{x}`, A pisze `{x,a}`, B pisze `{x,b}` → `a` przepada).
- **N-kopii:** `diff courier_api/gps_writer.py courier_api_panelsync/gps_writer.py` = **0 różnic** (bajt w bajt). Reguła atomic-write-pozycji żyje w ≥3 miejscach (gps_server, gps_writer, panelsync-gps_writer).
- **Źródło vs objaw:** źródło (własność zapisu store'a rozmyta na 2–3 moduły).
- **Dziś niski blast:** ścieżka PWA idle → realnie pisze tylko android, kolizji brak. Latentna mina strukturalna (kontrakt ① „jedno źródło").
- **Klasy:** współbieżność ⑮ · N-kopii ① · bliźniaki ③ · cross-repo ⑩.

### 🔥 F3 — [P1 · ŹRÓDŁO] Pole `timestamp` w jednym store = dwa różne zegary (server vs device), bez clampu
- **Mechanizm:** `gps_server.py:125` stempluje `datetime.now(timezone.utc)` (zegar SERWERA). `gps_writer.py:62` stempluje `datetime.fromtimestamp(ts_epoch)` gdzie `ts_epoch = latest["recorded_at"]` = zegar URZĄDZENIA. `main.py:157-171` `_parse_ts_to_epoch` **bez clampu** — `isinstance(ts,(int,float)) → return int(ts)` przyjmuje dowolny future/backward epoch verbatim. To samo pole JSON `timestamp` znaczy raz „kiedy serwer odebrał", raz „co twierdzi telefon".
- **Skutek:** downstream świeżość (`pos_age_min`, próg stale >25min) czyta pole jednakowo → **skew zegara telefonu cicho truje wiek pozycji** na ścieżce android (immunizowana tylko martwa ścieżka PWA). Przyszły ts → wieczna „świeżość"; wsteczny → fałszywy stale/duplikat w `gps_history` (UNIQUE `courier_id,recorded_at`).
- **Źródło vs objaw:** źródło (semantyka pola sprzężona z producentem).
- **Dowód:** kod obu writerów; `_parse_ts_to_epoch` bez sanity; oracle: dziś 0 wstecznych (okno 1-kurierowe, nie dowodzi odporności).
- **Klasy:** semantyka pól ⑥ · TZ/timestamp ⑫ · sentinele/cicha-awaria ⑬.

### 🔥 F4 — [P1] Legacy `/root/gps_server.py` — trzeci, NIEZARZĄDZANY producent z surowymi side-effectami
- **Mechanizm:** `/root/gps_server.py` (101 linii, POZA repo, POZA systemd — pid 1010 PPID=1 od 27.05). Brak atomic write (`save_json`: `json.dump(open(f,"w"))` = truncate-in-place, korupcja przy crashu w połowie zapisu). `except:` gołe (l.13) połyka wszystko. Pisze po **IMIENIU** (`data[kurier]`), nie po cid. Endpointy bez auth: `/stop` → tworzy `/tmp/gastro_stop` (**zatrzymuje dispatch!**, l.85-88), `/start` → kasuje flagę; `/status` renderuje HTML. Trzyma cert TLS `letsencrypt/178.104.104.138.nip.io`.
- **Skutek:** `gps_positions.json` = `{}` (pusty od 11.06) → nie karmi danych, ALE żywa powierzchnia kontroli dispatchu i błędu; `courier_resolver.py:516-526` wciąż go fallbackuje (name→cid; mapping-fail = HIGH „mass drop", udokumentowane `resolver:509-513`).
- **Źródło vs objaw:** źródło (nieznany, nieaudytowany byt produkcyjny na ścieżce).
- **Klasy:** martwy kod/wektor ⑪ · cross-repo (poza repo) ⑩ · konflikt ⑨ · cykl życia ⑦.

### 🧊 F5 — [P2] `dispatch-gps` (8766) = producent-wydmuszka; absorbent scarce-effort
- **Mechanizm:** systemd `dispatch-gps` żyje (limity zasobów, OnFailure alert, OOM-protect) ale w 14 dni **0 POST /gps**. Apka natywna idzie `/api/gps/batch`→8767; nginx `/gps`→8766 serwuje tylko stronę PWA (której nikt nie używa). Fix L2.1 + slot systemd utrzymywane dla martwego ingressu.
- **Skutek:** decoy pochłonął pracę audytową i deploy; dwa ingressy dla jednej roli.
- **Klasy:** martwy kod ⑪ · konflikt ⑨.

### 🧊 F6 — [P2 · KONTEKST, nie bug] Pokrycie realnym GPS ~zero — 1 kurier/24h, 8.4% rekordów
- **Dowód (oracle 48h):** `pos_source`: `last_picked_up_interp` 40.6% · `last_assigned_pickup` 26.0% · `pre_shift` 11.4% · `no_gps` 9.9% · **`gps` 8.4%** · `last_delivered` 2.5%. `source=gps` w 24h = **tylko cid 492** (z 11 widzianych). Świeżość realnego gps: median 1.0 min, p99 26.6, stale>25min = 8 (1.17% z-wiekiem), 0 wstecznych, 0×(0,0)/OOB/null w skonsumowanym snapshocie.
- **Interpretacja:** flota jedzie prawie w całości na proxy pozycji; realnych punktów mało → **każdy pojedynczy zły punkt ma nieproporcjonalny wpływ** (mały blast DZIŚ, duży przy wzroście adopcji). To argument ZA naprawą u źródła (F1), nie za wyciszeniem. Zero sentineli w oknie ≠ dowód że guard zbędny — to okno niskiego wolumenu.
- **Klasy:** kalibracja/pokrycie danych ⑦-kontekst.

### 🧊 F7 — [P2] Trzy schematy tożsamości u trzech producentów
- **Mechanizm:** A `gps_server`: PIN→imię→cid (`kurier_piny`+`kurier_ids`, 401 gdy brak; l.90-100). B `courier_api`: `_require_session`→`courier_id`. C legacy: PIN→imię, **klucz store = imię** (name→cid mapowany dopiero w `resolver:519-526`). Trzy reguły „kim jest kurier" dla tego samego pojęcia.
- **Klasy:** N-kopii (reguła tożsamości) ① · konflikt ⑨.

### 🧊 F8 — [P3] GC store istnieje, ale brak timera w `list-timers`
- **Mechanizm:** `tools/gps_positions_gc.py` istnieje (docstring: store'y „NIE miały żadnego GC/TTL — wpisy do ~55 dni wstecz"), ale `systemctl list-timers` **nie pokazuje** `gps_positions_gc` (widoczne tylko commitment-shadow / delivery-validation / fleet-position-snapshot). Do weryfikacji czy w ogóle zaplanowany; inaczej store puchnie (dziś mały bo 1 pisarz).
- **Klasy:** cykl życia ⑦.

### 🧊 F9 — [P3] Dryf dokumentacji + podwójny log + dublowany range-check
- `gps_server.py:18` docstring „Port: **8765**" vs `PORT=8766` (l.40) — dryf. · `gps_server.log`: **każda linia zdublowana** (duplikat handlera lub `StandardOutput`+`StandardError` → ten sam plik) → liczby w logu ×2 (drobny lying-instrument, kontrakt ⑤). · range-check `-90<=lat<=90` (l.340) dubluje Pydantic drugiego producenta (threshold-sprawl); bbox `52.6-53.7 / 22.3-24.1` (`common.py:518-519`) żyje tylko po stronie gps_server.
- **Klasy:** martwy kod/dryf ⑪ · kłamiące-przyrządy ⑤ · progi ⑭.

---

## POKRYCIE
Przesweepowane 15 klas anty-wzorców (kanon) na producentach pozycji A/B/C + ścieżce ingest→store→`courier_resolver`:

| # | Klasa | Status | Findingi |
|---|---|---|---|
| ① | N-kopii | ✅ | F1,F2 (gps_writer×2 + gps_server), F7 (3 tożsamości) |
| ② | warstwy (HARD u wejścia) | ✅ | F1 (walidacja u ingest brak na żywej ścieżce), F4 |
| ③ | bliźniaki / parytet | ✅ | F1 (guard w 1 z 2), F2 (gps_writer 1:1 panelsync) |
| ④ | flagi (rejestr/efektywny stan) | ✅ | `ENABLE_COORD_SENTINEL_INGEST_GUARD`=ON w `flags.json`, hot-read `decision_flag` (proc. restart 21:26) — ale bramkuje MARTWĄ ścieżkę (F1); brak drop-in env dla dispatch-gps |
| ⑤ | kłamiące przyrządy | ✅ | F5 (ingress=nic nie przyjmuje), F9 (log ×2) |
| ⑥ | semantyka pól | ✅ | F3 (`timestamp` server vs device) |
| ⑦ | kalibracja / cykl życia | ✅ | F6 (pokrycie 8.4%/1 kurier), F8 (GC bez timera) |
| ⑧ | koherencja/clampy | ✅ (styk) | F3 (brak clampu ts), F1 (brak chokepointu walidacji coords) |
| ⑨ | konflikt (ról) | ✅ | F4,F5 (2–3 ingressy jednej roli), F7 |
| ⑩ | cross-repo | ✅ | F2 (panelsync), F4 (legacy poza repo) |
| ⑪ | martwy kod | ✅ | F4 (legacy side-effects), F5 (8766 wydmuszka), F9 |
| ⑫ | TZ / timestamp | ✅ | F3 (device vs server; wszyscy stemplują UTC = strefowo spójne, ale różne zegary; brak clampu) |
| ⑬ | sentinele / cicha awaria | ✅ | F1 ((0,0)/OOB fail-open na androidzie), F4 (bare except) |
| ⑭ | progi | ✅ | F1/F9 (range ±90 dublowany, bbox tylko w gps_server) |
| ⑮ | współbieżność | ✅ | F2 (cross-proces lost-update, flock-na-tmp) |

**Runtime-oracle wykonany:** żywe porty (8765/8766/8767 → 3 procesy), `NRestarts=0` dispatch-gps (jedyny restart 21:26=L2.1), rozkład `pos_source`/geometrii/świeżości 48h, rozkład tożsamości, zawartość 3 store'ów (skopiowane do scratchpada), `journalctl` + logi 14 dni.

---

## JAWNE LUKI (świadomie poza tym sweepem)
- **SQLite `gps_history` (`courier_api/db.py`)** — audytowany tylko styk `insert_history_batch` (UNIQUE `courier_id,recorded_at`, INSERT OR IGNORE). NIE sprawdzone: schemat/migracje, indeksy, rozmiar/rotacja, retencja WAL, spójność z JSON store.
- **`courier_api/main.py` całościowo** — `_require_session`/auth, rate-limit, walidacja `battery/speed/heading`, endpointy statusu/deliver-guard — poza polem pozycji.
- **Apka Kotlin `pl.nadajesz.courier`** — realne źródło device-ts i ewentualnego (0,0)/słabej `accuracy`. Nie mam tu repo apki → NIE wiem, czy apka sama filtruje (0,0)/accuracy/skok >100km PRZED wysłaniem (część obrony mogłaby żyć klient-side; do potwierdzenia u źródła apki).
- **`courier_last_pos.json`** (producent `courier_resolver._save_last_known_pos`) — to konsument-producent w lane `courier_resolver` (pokryty Fazą 1); zmapowałem tylko styk (merge-by-ts, prune 25min).
- **nginx (routing `/gps`→8766, TLS, `/api`→8767)** — z opisu CLAUDE.md, NIE zweryfikowane na żywej konfiguracji.
- **Legacy port 8765 — realny ruch:** store pusty sugeruje brak GPSLoggerów, ale nie podsłuchałem portu; nie wiem czy jakikolwiek klient jeszcze bije.
- **Brak testu ON≠OFF flagi L2.1** i brak jakiejkolwiek mutacji/replayu — zgodnie z trybem READ-ONLY (to należy do ETAP-u naprawczego, nie audytu).

---
*Scratchpad z surowcami: `stores/{gps_positions_pwa,gps_positions,courier_last_pos}.json`, `stores/fleet_position_history.jsonl` (kopia 48h+), `oracle.py`.*
