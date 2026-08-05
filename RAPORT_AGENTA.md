# RAPORT AGENTA — sesja 297: automat archiwizacji OD-7 (shadow-first)

**Gałąź:** `wt/od7-archiver-297-cto-20260805` (worktree `/root/worktrees/dispatch_v2/active/20260805-od7-archiver-297-cto`, base master `06e4d5c39`)
**Tryb pracy:** wyłącznie worktree, ZERO zapisu do żywego stanu (dowód mechaniczny w §7).
**Bieg apply:** NIE wykonany (należy do CTO/ownera). Merge, timer i 1. bieg apply — poza tą sesją.

---

## 0. Streszczenie dla ownera (4 zdania)

1. Powstał `tools/retention_archiver.py` + deklaratywna polityka `tools/retention_od7_policy.json`
   (jedno źródło liczb OD-7) + 47 testów hermetycznych; tryb domyślny to REPORT, a tryb apply jest
   zamknięty tokenem ACK ważnym jedną dobę i unieważnianym każdą zmianą polityki.
2. Bieg REPORT na żywych danych (READ-ONLY, 563 pliki / **6 309 MB**) mówi: **9 plików / 262,5 MB
   kwalifikuje się DZIŚ do archiwum** (po gzip ok. **35,1 MB**) plus 2 snapshoty baz (**617,5 MB → ok. 120,6 MB**);
   archiwizacja **nie zwalnia ani jednego MB** na żywym dysku i **nie ma tego zwalniać** — kasowanie żywych
   plików zostaje przy istniejących GC (zakaz drugiego writera).
3. Najważniejsze znalezisko nie jest o miejscu na dysku, tylko o **sprzeczności polityki z kodem**:
   w 4 miejscach istniejący GC kasuje dane ZANIM OD-7 pozwala je zdjąć z żywego dysku — `world_record`
   ginie po 14 dniach przy politykach 30 dni, `observability/*` tak samo, a `events.db` kasuje przetworzone
   zdarzenia po 48 h przy polityce 6 miesięcy. Automat jest tak zaprojektowany, żeby **wyprzedzać kasownik**
   (kopiuje od 11. dnia), ale luki 14→30 dni i 48 h→6 mies. **nie da się odzyskać samą archiwizacją** — to
   decyzja ownera (P-2, P-3).
4. Archiwum lokalnie **nie ma sensu** (dysk 91,5 %, 13 GB wolnego): stan ustalony to ok. **5,9 GB**;
   proponowany `--archive-root` = `/mnt/storagebox/archive/od7` (1 TB, 1,8 % zajęte) — **katalogu nie
   utworzyłem**, to świadoma decyzja właściciela.

---

## 1. Architektura — co dokładnie powstało

| Plik | Rola |
|---|---|
| `tools/retention_od7_policy.json` | **Jedyne źródło polityki archiwum**: klasy OD-7 (cytat liczb ownera), reguły ścieżka→klasa, wykluczenia, przepis PII, kandydaci na `--archive-root`. |
| `tools/retention_archiver.py` | Skaner + planner + raport + (bramkowany) executor. |
| `tests/test_retention_archiver.py` | 47 testów hermetycznych (tmp_path, sfabrykowane mtime). |
| `eod_drafts/2026-08-05/OD7_ARCHIVER_REPORT_20260805.{json,md}` | Raport 1. biegu REPORT na żywych danych = materiał do ACK ownera. |
| `eod_drafts/2026-08-05/OD7_ARCHIVER_RUN_AUDIT_20260805.txt` | Ślad audytu zapisów z tego biegu (§7). |

### 1.1 Jeden owner kontraktu (CLAUDE.md „NAPRAWA U ŹRÓDŁA")

* Owner **polityki archiwum** = plik polityki. Liczby (90/270, 30/365, 180/∞, mask 90) są cytatem OD-7;
  mapowanie plików na klasy jest moim doprecyzowaniem i każda reguła niesie pole `basis`.
* Owner **kasowania żywych plików** = mechanizm zadeklarowany w regule (`live_delete_owner`:
  `world_record.py:_gc`, `observability/log_rotation.py`, logrotate, `event_bus_cleanup`,
  `gps_positions_gc.py`). Archiwizator **nigdy** nie kasuje żywego pliku — akcja `SOURCE_DELETE`
  istnieje w kodzie, ale nie ma dziś ANI JEDNEJ reguły, która by ją włączała, a próba jej wykonania
  bez reguły to twardy wyjątek (test `test_source_delete_has_no_rule_and_is_fail_closed`).
  Dzięki temu nie powstaje drugi writer tej samej prawdy.
* Owner **prune'u artefaktów bez GC** = `tools/state_retention_prune.py` (Z0-4, gałąź `z04-retention`,
  bramka `state.retention-prune-z04`, wciąż przed ACK). Zakresy są rozłączne: tamto **kasuje**, to
  **kopiuje zanim ktokolwiek skasuje**. Uwaga do merge'u jest zapisana w polityce (`_relation_z04`).

### 1.2 Model działania (dlaczego kopiujemy wcześniej, niż polityka pozwala kasować)

```
próg kopiowania = min( OD-7 live_days , competing_gc.deletes_after_days − margines 3 dni )
```

Jeśli kasownik jest szybszy od polityki, automat zgłasza **KONFLIKT POLITYKI** i kopiuje z wyprzedzeniem
(dla `world_record`: 11. dzień przy GC 14 dni). Archiwum wygasa po `archive_days` z OD-7 i wtedy — i tylko
tam, wewnątrz `--archive-root`, który jest wyłączną własnością tego narzędzia — automat kasuje.

### 1.3 Bezpieczniki

| Bezpiecznik | Realizacja | Test |
|---|---|---|
| REPORT pisze wyłącznie do `--out`/`--text-out` | klasa `WriteGate` (whitelist ścieżek) + audyt CPython w biegu (§7) | `test_report_mode_writes_only_to_out` |
| Zapis do żywych korzeni niemożliwy w KAŻDYM trybie | `FORBIDDEN_WRITE_ROOTS` sprawdzane przed whitelistą — jawne `allow_root` nie pomaga | `test_write_gate_blocks_live_roots_in_every_mode` (4 ścieżki × 2 tryby) |
| APPLY tylko z ACK | `--apply` + `--ack-token OD7-<dzień UTC>-<sha polityki[:12]>`; brak tokenu = exit 2 **bez** degradacji do dry-runu | 4 testy bramki |
| ACK wygasa | token zależy od doby UTC i od hasha polityki | `test_apply_token_is_invalidated_by_policy_change` |
| Archiwum nie tworzy katalogu | apply bez istniejącego `--archive-root` = exit 2; report nigdy nie tworzy | 2 testy |
| Zapisy atomowe | temp `.od7_*` w katalogu docelowym → `fsync` → `os.replace` → `fsync` katalogu | 2 testy |
| Weryfikowalność i odwracalność | manifest jsonl: `source_sha256`, `content_sha256`, `archive_sha256`, rozmiary, ts, run_id, wersja+sha polityki; po zapisie gz jest **odczytywany i sprawdzany** przed publikacją | `test_apply_archives_with_verified_manifest`, `test_archive_verification_catches_corruption` (mutacja: gubienie bajtów → czerwone) |
| Pliki w użyciu | skan `/proc/*/fd` (bez zależności od `lsof`), otwarte do zapisu = SKIP z raportem | `test_open_for_write_file_is_skipped` |
| Fail-closed | błąd w APPLY przerywa bieg (exit 1); uszkodzony manifest = wyjątek, nie „lecimy dalej"; błędy w REPORT = exit 3 + raport | 3 testy |
| Idempotencja | manifest jako źródło „już zarchiwizowane" | `test_apply_is_idempotent` |

---

## 2. Mapowanie realnych plików na klasy OD-7 (bieg 2026-08-05 08:2xZ)

| Klasa | Pliki | Rozmiar | Co tam wpadło |
|---|---:|---:|---|
| `decision_logs` | 39 | 1 828,2 MB | `observability/candidate_decisions_*`, `observability/fleet_filter_*`, `decision_eta_log*`, `learning_log*`, `assignment_episode*`, `decision_outcomes*`, `plan_recheck_log*` |
| `world_record` | 15 | 1 354,2 MB | `world_record/world_record-YYYYMMDD.jsonl` |
| **`unknown`** | **250** | **997,7 MB** | wszystko poza literalnym OD-7 (korpusy shadow silnika, werdykty, snapshoty) — **nigdy nie ruszane** |
| `ops_logs` | 195 | 972,0 MB | `scripts/logs/*` — tylko klauzula maskowania po 90 dniach |
| `events_db` | 1 | 560,8 MB | `events.db` |
| `protected` | 17 | 327,6 MB | `shadow_decisions*` + 5 korpusów bramkowych (VETO z RETENCJA-RAPORT 5.1/5.2) |
| `gps` | 8 | 267,7 MB | `gps_quality_shadow*`, `fleet_position_history*`, `gps_delivery_truth*`, `courier_gps_commitment_shadow*`, `courier_api.db` (tabela `gps_history`), snapshoty pozycji |
| `live_state` | 38 | 1,0 MB | żywy stan operacyjny (kopie robi restic) |

Plan wynikowy: `ARCHIVE` 9 plików / 262,5 MB · `SQLITE_SNAPSHOT` 2 / 617,5 MB · `SKIP_TOO_YOUNG` 39 /
2 801,9 MB · `SKIP_LIVE_APPEND` 11 / 328,9 MB · `SKIP_NOT_ARCHIVABLE` 252 / 1 300,6 MB ·
`REPORT_UNKNOWN` 250 / 997,7 MB · błędów: **0**.

**UNKNOWN = świadomy wynik, nie luka.** OD-7 wymienia GPS, `world_record`, „logi decyzji" i `events.db`.
Korpusy shadow silnika (`r6_breach_shadow` 221,6 MB, `v319c_read_shadow_log` 145 MB,
`consumer_stuck_alert_evaluations` 107 MB, `drive_min_*` 146 MB, `lex_window_ledger_v2*` 108 MB, …) nie są
żadną z tych klas. Klasyfikuję je jako UNKNOWN i **nie ruszam** — przypisanie klasy to decyzja ownera (P-6).

---

## 3. Wynik biegu REPORT — liczby

| Pozycja | Wartość |
|---|---|
| Przeskanowano | 563 pliki / **6 309,2 MB** (READ-ONLY) |
| Do archiwum DZIŚ | 9 plików / **262,5 MB** → po gzip ok. **35,1 MB** (zmierzony współczynnik na próbkach, nie zgadywany) |
| Snapshoty baz | 2 (`events.db` 560,8 MB, `courier_api.db` 56,7 MB) → ok. **115,0 MB** |
| **Zwolnione z żywego dysku** | **0 MB** — i tak ma być (kasowanie ma innego ownera) |
| Przyrost archiwum (ten bieg) | ok. **150,1 MB** |
| Tempo w stanie ustalonym | ok. **15,5 MB/dobę** (world_record ~11,1 MB/d + decision_logs ~0,6 MB/d + amortyzacja snapshotów) |
| Stan ustalony archiwum | ok. **5 670 MB** (world_record 4 052 MB przy oknie 365 dni, decision_logs 219 MB, snapshoty reszta) |
| Dysk żywy | 150 GB, wolne 12,8 GB, **91,5 % zajęte** |
| Kandydat na archiwum | `/mnt/storagebox/archive` — 1 TB, **1,8 % zajęte**, 1 006 GB wolnego → mieści stan ustalony z zapasem |

**Bilans miejsca uczciwie:** ten automat **nic nie zwalnia**. Zwalnianie jest wyłącznie po stronie
istniejących GC (i bramki Z0-4). Wartość automatu to: dane, które dziś znikają bezpowrotnie, od jutra
istnieją w postaci sprawdzalnej sha256 i odwracalnej gunzipem przez 12 miesięcy (GPS 9, events.db bezterminowo).

---

## 4. ⛔ Konflikty polityki — najważniejsze znalezisko

| Reguła | OD-7 „żywe" | Kod kasuje po | Kto kasuje | Skutek |
|---|---:|---:|---|---|
| `events.db` | 180 dni | **2 dni** | `event_bus_cleanup` (timer 04:00: processed 48 h, audit_log 90 d) | między snapshotami przepada wszystko poza 48 h; snapshot co 30 dni **nie realizuje** „6 mies. żywe" |
| `wr.daily` | 30 dni | **14 dni** | `world_record.py:_gc` (`RETENTION_DAYS=14`) | dane z dni 15–30 nie istnieją; archiwum ratuje tylko to, co złapie do 14. dnia |
| `dec.observability_daily` | 30 dni | **14 dni** | `observability/log_rotation.py --retention-days 14` | jak wyżej |
| `gps.positions_live` | 90 dni | **1 dzień** | `tools/gps_positions_gc.py` (TTL 24 h) | to snapshot pozycji, nie historia — konflikt formalny, archiwizacja bez wartości (opisane w regule) |

To jest dokładnie mechanizm, który zjadł `pool_feasible` sprzed 23.07: **polityka mówi jedno, kod robi
drugie, a między nimi nie ma archiwum**. Automat zamyka trzecią dziurę (brak archiwum) i kopiuje z
wyprzedzeniem, ale zgodności „żywe 30 dni / 6 miesięcy" **nie da się osiągnąć bez zmiany kasowników** —
a to zmiana silnika (Przykazanie #0) i decyzja ownera: P-2, P-3.

---

## 5. Dług rotacji — 11 strumieni, których nie da się zarchiwizować w całości

Plik dopisywany na żywo nie ma bezpiecznego punktu odcięcia (kopia całości nie jest atomowa i łapie
ogon w trakcie zapisu). Automat takich plików **nie dotyka** i raportuje je jako dług:

| Strumień | Klasa | Rozmiar | Kto dziś kasuje |
|---|---|---:|---|
| `gps_quality_shadow.jsonl` | gps | 169,7 MB | **NIKT** |
| `learning_log.jsonl` | decision_logs | 85,5 MB | logrotate GRUPA B |
| `fleet_position_history.jsonl` | gps | 39,8 MB | **NIKT** |
| `plan_recheck_log.jsonl` | decision_logs | 13,0 MB | logrotate GRUPA B-2 |
| `assignment_episode.jsonl` | decision_logs | 13,0 MB | **NIKT** |
| `decision_outcomes.jsonl` | decision_logs | 4,8 MB | **NIKT** |
| `backfill_decisions_outcomes_v1.jsonl` | decision_logs | 1,5 MB | **NIKT** |
| `gps_delivery_truth.jsonl` | gps | 1,3 MB | **NIKT** |
| `decision_eta_log.jsonl` | decision_logs | 0,3 MB | jsonl_rotation |
| `courier_gps_commitment_shadow.jsonl` | gps | 0,2 MB | **NIKT** |
| `gps_positions_pwa.json.merge_shadow.jsonl` | gps | 0,0 MB | **NIKT** |

Wniosek: **wpięcie rotacji to warunek wstępny archiwizacji** tych strumieni (rotacja = punkt odcięcia).
Pokrywa się to z ustaleniem RETENCJA-RAPORT.md z 03.08 („15 strumieni bez rotacji"), ale tu jest liczone
mechanicznie i per klasa OD-7.

---

## 6. PII: detekcja, przepis, 3 defekty znalezione i naprawione u źródła

**Detekcja** (raport zawiera WYŁĄCZNIE liczniki, nigdy wartości — sprawdzone testem):
`world_record-2026072[234]` mają po 90–173 wystąpień `delivery_address`/`pickup_address` na próbkę,
`observability/candidate_decisions_*` po 2 100–3 100 `delivery_address`, w treści łapią się telefony,
kody pocztowe i e-maile. **Każdy plik idący do archiwum starszy niż 90 dni jest maskowany w locie** —
źródło pozostaje nietknięte, maskowana jest kopia archiwalna (`ARCHIVE_MASKED`, manifest odnotowuje
`content_sha256 != source_sha256` i statystyki maskowania).

**Przepis:** wykluczenia → pseudonimizacja (HMAC-SHA256 z solą `OD7_PII_SALT`, join zachowany) →
redakcja (`[PII-REDACTED]`), plus wzorce wartościowe (e-mail, telefon PL, kod pocztowy, „ul./al./os. … nr").

Bieg na żywych danych złapał **trzy defekty mojego własnego przepisu** — wszystkie naprawione u źródła
i przykryte testami regresji:

1. **Wzorzec `^[A-Z0-9_]{4,}$` kompilowany z `re.I`** pasował do KAŻDEGO klucza → maskowanie byłoby
   cichym no-opem (najgorszy tryb awarii przy RODO). Teraz wykluczenia są case-sensitive.
   Test: `test_real_policy_excludes_flag_keys`.
2. **Nazwy flag i klucze `*_id`** (`ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW`, `address_id`) wpadały w regułę
   „address" → archiwum straciłoby telemetrię silnika i joiny. Teraz: flagi wykluczone, `address_id`
   pseudonimizowany. Test: `test_masker_never_masks_flags_or_ids`.
3. **Gołe podciągi** trafiały w nazwy własne (`lokal` w `lokalka_zamowienia_*.csv`). Wzorce kluczy są
   teraz regexami z granicami. Test: `test_real_policy_key_patterns_have_word_boundaries`.

**Ograniczenia, których NIE ukrywam:**
* Słowniki **kluczowane** adresem (np. klucz `street mama thai`) — maskowanie wartości ich nie zakrywa.
  Detekcja zgłasza to osobno jako `KEYNAME_IS_PII:*` (P-5).
* `events.db` i `courier_api.db` są binarne — liczniki wzorców są poglądowe, prawdziwe maskowanie
  wymaga przejścia po schemacie tabel (P-4). Raport oznacza takie pliki „⚠ binarny".
* `MASK_LIVE` dla logów >90 dni jest **planowany, ale nie wykonywany**: te pliki leżą w żywym korzeniu,
  do którego to narzędzie nie pisze w żadnym trybie. W dzisiejszym biegu takich plików jest **0**
  (najstarszy log w `scripts/logs` ma 89 dni) — próg zostanie przekroczony lada dzień (P-4/P-5 przed tym).

---

## 7. Dowód: bieg REPORT nie tknął żywego stanu

Kod: jedyne prymitywy zapisu to `atomic_write_bytes`, `append_manifest`, `gzip_copy_verified`,
`sqlite_snapshot`, `mask_in_place` — **każdy zaczyna od `GATE.check(path)`**, a `WriteGate` odrzuca
ścieżki w `dispatch_state`/`scripts/logs`/`flags.json` zanim sprawdzi cokolwiek innego.

Dowód wykonawczy (nie deklaracja): bieg puszczony pod audytem CPython (`sys.addaudithook`), który loguje
każde `open` z intencją zapisu, `rename`, `remove`, `mkdir`. Pełny ślad w
`eod_drafts/2026-08-05/OD7_ARCHIVER_RUN_AUDIT_20260805.txt` — **5 zdarzeń, wszystkie w worktree**:
2 pliki tymczasowe `.od7_*.tmp` i 2 `rename` na pliki raportu. Zero dotknięć żywych korzeni.
(To istotne, bo HERMETIC-GUARD suity **nie działa** poza pytestem — narzędzie musi mieć własną bramkę.)

---

## 8. Testy i regresja

* **Nowe testy:** `tests/test_retention_archiver.py` — **47 testów, 47 zielonych**, ~2,2 s,
  w 100 % hermetyczne (własne drzewa w `tmp_path`, sfabrykowane `mtime`, własna polityka).
  Pokrycie wg briefu: klasyfikacja (6) · progi wieku i konflikt z GC (6) · report vs apply gating (7) ·
  manifest + sha + odwracalność + idempotencja + wygasanie (7) · maskowanie i detekcja (9) ·
  bramka zapisu i fail-closed (7) · polityka (3) · sqlite (2).
* **Test mutacyjny:** `test_archive_verification_catches_corruption` — podmieniony `GzipFile` gubi bajt;
  weryfikacja sha po dekompresji czerwienieje i archiwum **nie jest publikowane**.
* **Lint/typy:** `ruff` (konfiguracja `tools/devlint/ruff.toml`) i `mypy` na obu nowych plikach: czysto (0 naruszeń). Repo-wide ratchet `tools/devlint/ratchet_check.py` jest czerwony od dryfu mastera po baseline z 06.07 — moje pliki dokładają **0**.
* **Regresja pełna** (`/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q` w pkgroocie):

| Bieg | Wynik |
|---|---|
| baseline PRZED (master `06e4d5c39`) | `23 failed, 7506 passed, 24 skipped, 8 xfailed` (822 s) |
| PO zmianie (bieg 1) | `23 failed, 7553 passed, 24 skipped, 8 xfailed` (803 s) |
| PO zmianie (bieg 2, kod finalny = commit `f9d4d824f`) | `23 failed, 7553 passed, 24 skipped, 8 xfailed` (780 s) |

Delta = wyłącznie moje 47 testów (7506 → 7553 passed). Listy faili baseline i po zmianie są **identyczne** (`diff` w pliku dowodowym). 23 faile baseline'u są zastane i niezwiązane
(`tests/test_a2_selection_shadow.py` 15, `tests/test_courier_reliability.py` 8) — lista w
`eod_drafts/2026-08-05/OD7_ARCHIVER_REGRESSION_20260805.txt`.

---

## 9. Granice tej sesji (czego świadomie NIE zrobiłem)

* **Ani jednego biegu APPLY** — nie wydałem sobie tokenu ACK. 1. bieg = CTO/owner.
* **Nie utworzyłem** katalogu archiwum (dysk 91,5 %; wybór nośnika to decyzja).
* **Nie ruszyłem** `world_record.py`, `log_rotation.py`, logrotate, `event_bus_cleanup`, timerów —
  konflikty polityki są zaraportowane, nie „naprawione po cichu" (to zmiana silnika = Przykazanie #0).
* **Nie użyłem** `--include-sqlite` w biegu na żywo (otwarcie żywej bazy read-only to i tak dotknięcie
  żywego stanu; flaga istnieje dla biegu z ACK).
* **Nie dopisałem** bramki do ledgera procesowego — proponowany wpis: `state.retention-archiver-od7`,
  stan `BUILT_OFF`, blocker „ACK ownera na 1. bieg apply + wybór `--archive-root`", dowód: SHA commitu
  + raport z §3. Zakładam, że robi to CTO razem z merge'em (baza ledgera jest współdzielona, a brief
  ograniczył mnie do worktree).

---

## 10. Otwarte pytania do ownera

| # | Pytanie | Dlaczego to Twoja decyzja |
|---|---|---|
| **P-1** | `shadow_decisions.jsonl` (168 MB): OD-7 klasyfikuje go jako „log decyzji" (archiwum 12 mies.), ale VETO z 03.08 mówi „nie ruszać" (5 otwartych bramek). Dziś dałem mu klasę `protected` = **żadnej kopii**. Archiwizować (kopia niczego nie psuje), czy dalej nie dotykać? | archiwizacja = eksport danych z PII poza serwer |
| **P-2** | `world_record`: OD-7 mówi 30 dni żywe, kod kasuje po 14. Podnieść `RETENTION_DAYS` do 30 (zmiana silnika, protokół #0, +~1,3 GB na dysku 91,5 %), czy zaakceptować 14 dni jako realną politykę i zapisać to w OD-7? | sprzeczność polityki z kodem, obie drogi mają koszt |
| **P-3** | `events.db`: `event_bus_cleanup` kasuje przetworzone po 48 h. Snapshot co 30 dni ratuje tylko obraz z dnia snapshotu. Chcesz (a) częstsze snapshoty, (b) eksport wierszy przed kasacją (zmiana w cleanupie), czy (c) uznać 48 h za faktyczną politykę? | „6 mies. żywe" jest dziś niewykonalne bez zmiany kasownika |
| **P-4** | Maskowanie PII w bazach (`events.db`, `courier_api.db`) wymaga przejścia po schemacie tabel — budować? | zakres większy niż klauzula „logi" z OD-7 |
| **P-5** | Korpusy, w których **klucz** jest adresem — maskowanie wartości ich nie zakrywa. Przepisywać klucze (zmiana schematu korpusu, psuje istniejące joiny) czy zostawić i odnotować ryzyko? | kompromis RODO ↔ użyteczność danych uczenia |
| **P-6** | 250 plików / 997,7 MB w `UNKNOWN` (korpusy shadow silnika). Przypisać im klasę OD-7, czy zostawić poza polityką (jak dziś)? | rozszerzenie zakresu decyzji z 03.08 |
| **P-7** | Sól `OD7_PII_SALT` do pseudonimizacji: kto ją generuje i gdzie leży? Utrata = brak joinów w archiwum, wyciek = deanonimizacja. | sekret o cyklu życia dłuższym niż archiwum |
| **P-8** | `--archive-root` = `/mnt/storagebox/archive/od7` (sshfs, 1 TB, 1,8 %)? Zgoda na archiwum na mouncie sieciowym (dostępność, czas gunzipa przy odtwarzaniu)? | stan ustalony ~5,9 GB, dysk lokalny odpada |

---

## 11. Wykonanie 1. biegu APPLY (dla CTO/ownera, gdy zapadną P-2/P-8)

```
# 1. owner tworzy katalog archiwum (automat go NIE tworzy)
mkdir -p /mnt/storagebox/archive/od7
# 2. sól do pseudonimizacji (P-7), inaczej pseudonimizacja jest fail-closed
export OD7_PII_SALT='<sekret>'
# 3. token ACK na dziś (ważny do końca doby UTC, unieważniany zmianą polityki)
/root/.openclaw/venvs/dispatch/bin/python tools/retention_archiver.py --print-ack-token
# 4. bieg apply (zalecane najpierw --limit-actions 1 na jednym pliku)
/root/.openclaw/venvs/dispatch/bin/python tools/retention_archiver.py \
    --apply --ack-token OD7-YYYYMMDD-xxxxxxxxxxxx \
    --archive-root /mnt/storagebox/archive/od7 \
    --out eod_drafts/<data>/OD7_APPLY.json --text-out eod_drafts/<data>/OD7_APPLY.md --limit-actions 1
# 5. weryfikacja: gunzip -t + porównanie sha z MANIFEST.jsonl
```

Rollback: skasować pliki spod `--archive-root` (źródła nie były ruszane — archiwizacja jest addytywna).
Timer: dobowy, **przed** 03:00 UTC (`dispatch-log-rotation.timer`) i przed pierwszym zapisem dnia do
`world_record` — inaczej automat przegra wyścig z kasownikiem.

---

**Commit:** `f9d4d824f21b6f6989dc181e27202f759323a114`
**Pliki:** `tools/retention_archiver.py`, `tools/retention_od7_policy.json`,
`tests/test_retention_archiver.py`, `eod_drafts/2026-08-05/OD7_ARCHIVER_REPORT_20260805.{json,md}`,
`eod_drafts/2026-08-05/OD7_ARCHIVER_RUN_AUDIT_20260805.txt`, `RAPORT_AGENTA.md`.
