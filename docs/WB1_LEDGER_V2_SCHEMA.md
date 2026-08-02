# WB1 faza 1 — ZAMROŻONY schemat `lex_window_ledger.v2`

**Status:** FROZEN (2026-07-27). Zmiana pól = nowa wersja schematu (`v3`), nigdy cicha
mutacja `v2`. Podstawa: incydent CZASY 492, `/root/handover/CZASY_INCYDENT_492_DIAGNOZA_2026-07-27.md`
sekcja 13.2 p.1 (scalone werdykty 3 recenzentów) + owner ACK D1/D2/D3 z 27.07
(`memory/owner-decision-czasy-d1d2d3-2026-07-27`).

**Zakres tego dokumentu:** kontrakt danych + kanoniczny writer + odcięcie obserwatorów
u źródła. **Poza zakresem:** guardy G1–G5 (WB2), zmiany decyzji silnika, instalacja
unitów, monitor parytetu.

---

## 1. Problem, który ten schemat likwiduje

Dzisiejszy ledger v1 (`dispatch_state/lex_committed_window_shadow.jsonl`) ma pole
`applied`, które jest **wartością flagi `ENABLE_LEX_COMMITTED_WINDOW`, a nie faktem
zapisu planu**:

```python
"applied": ENABLE_LEX_COMMITTED_WINDOW,   # plan_recheck.py:1993 (v1)
```

Konsekwencje zmierzone na żywym pliku 2026-07-27 (18 211 wierszy, ostatnie 24 h):

| Miara | Wartość | Znaczenie |
|---|---|---|
| wierszy / 24 h | 612 | — |
| unikalnych treści decyzyjnych | 228 | reszta to powtórki |
| wierszy będących powtórką | 384 (**62,7 %**) | zawyżają każdą statystykę |
| mediana odstępu powtórki | **0,36 min** | nie 5-min tick silnika |
| `applied == true` | **612 / 612 (100 %)** | także dla procesów, które NIC nie zapisują |

Powtórki pochodzą od **trzech timerów obserwatorów**, które wołają tę samą ścieżkę
kanonu co silnik, ale ich zapisy planu są przekierowane na temp:

| Obserwator | Wejście w kanon | Częstotliwość |
|---|---|---|
| `dispatch-carried-first-guard` | `tools/carried_first_guard.py:121` → `_apply_canon_order_invariants` | ~3 min |
| `dispatch-b-route-shadow` | `tools/b_route_shadow.py:262/321` → `_gen_one_bag_plan` / `_apply_canon_order_invariants` | ~3,5 min |
| `dispatch-bundle-calib-shadow` | `tools/bundle_calib_shadow.py:383/434` → `_gen_one_bag_plan` / `_apply_canon_order_invariants` | ~5 min |

Wniosek: **na ledgerze v1 nie da się skalibrować progów WB2** — 63 % masy to obserwatorzy,
a `applied` nie odróżnia „silnik wybrał" od „silnik zapisał" od „kurier to zobaczył".

Dodatkowy dowód, że env obserwatora **kłamie** o stanie efektywnym (13.2 p.8):
`dispatch-b-route-shadow.service.d/route-flag-parity.conf` deklaruje
`Environment=ENABLE_LEX_COMMITTED_WINDOW=1`, choć proces nie jest writerem planu.
Dlatego rola **nie może** być wyprowadzana z env unitu ani z nazwy procesu.

---

## 2. Semantyka ZAMROŻONA: koniec `applied`

`applied` **znika**. Zastępują je trzy **rozłączne fakty**, każdy z własnym momentem
powstania i własnym rekordem:

| Fakt | Pole | Kto ustala | Kiedy |
|---|---|---|---|
| **decided** | `decision.decided` | warstwa reorderu `_lex_committed_window_reorder` | kandydat wybrany ORAZ flaga APPLY ON ORAZ sekwencja ≠ baseline |
| **written** | rekord `write_receipt`, `write.outcome` | ścieżka zapisu `_gen_one_bag_plan` / `_retime_one_bag_plan` po CAS | `written` / `skipped_cas` / `failed` / `not_attempted` |
| **served** | rekord `served_receipt`, `served.outcome` | warstwa API/panel/apka | gdy sekwencja realnie wyszła do konsumenta |

**Reguła nadrzędna:** `decided == true` **nie implikuje** `written`, a `written` **nie
implikuje** `served`. Każdy poziom to osobny rekord dowiązany przez `attempt_id`.
Analiza, która myli te poziomy, jest błędna z definicji.

`decided` obserwatora jest **z definicji `false`** — obserwator nie ma czym zdecydować
planu; ale nawet gdyby policzył kandydata, jego rekord nie trafia do pliku kanonicznego.

---

## 3. Rola wywołania — kontrakt odcięcia obserwatorów

### 3.1 Kanoniczny mechanizm: jawny kontekst wywołania (capability)

Rola jest **przekazywana jawnie przez callera** jako obiekt `LedgerContext`,
tworzony wyłącznie przez fabryki w `dispatch_v2/core/lex_window_ledger.py`:

```python
writer_context(source, trigger)    # rola WRITER — woła TYLKO plan_recheck
observer_context(source, trigger)  # rola OBSERVER
```

Ścieżka przekazania (parametr `ledger_ctx`, keyword-only, domyślnie `None`):

```
run_recheck()        ─┐
recanon_courier()    ─┼─→ _gap_fill_plans ─→ _gen_one_bag_plan   ─┐
redecide_courier()   ─┘                      _retime_one_bag_plan ─┴─→ _apply_canon_order_invariants
                                                                        └─→ _lex_committed_window_reorder
                                                                              └─→ record_decision(ctx, …)
```

**Brak kontekstu (`None`) = OBSERVER.** To jest kierunek fail-safe: pominięta ścieżka
writera powoduje **utratę wiersza kanonicznego** (wykrywalną przez `coverage`/heartbeat),
a nigdy **zanieczyszczenie** pliku kanonicznego. Obserwator nie ma jak zdobyć roli
WRITER przypadkiem, bo nigdy nie woła fabryki `writer_context`.

### 3.2 Dlaczego NIE env z unitu i NIE nazwa procesu (decyzja projektowa)

| Odrzucony wariant | Powód odrzucenia |
|---|---|
| `Environment=ZIOMEK_LEDGER_ROLE=…` w unicie | env obserwatorów **udowodnienie kłamie** (b-route-shadow deklaruje `ENABLE_LEX_COMMITTED_WINDOW=1`); dodatkowo instalacja unitów jest poza zakresem WB1 (osobny ACK), więc WB1 nie mógłby dostarczyć czystego baseline'u bez zmian w systemd |
| heurystyka po `argv`/nazwie procesu | wprost zakazana w briefie; łamie się przy `python -m`, testach, replayach |
| rola per-proces (globalny stan modułu) | panel-watcher jest writerem **i** hostem innych ścieżek; globalny stan wprowadza kolejność inicjalizacji jako ukrytą zależność |

Rola jest **własnością ścieżki kodu**, nie procesu — dlatego jedynym poprawnym nośnikiem
jest parametr wywołania.

### 3.3 Gdzie trafiają wpisy obserwatorów

**Decyzja: osobny plik obserwacyjny** (nie kasujemy danych — preferencja z briefu).

| Rola | Plik |
|---|---|
| WRITER | `dispatch_state/lex_window_ledger_v2.jsonl` (**KANON** — jedyne wejście kalibracji progów WB2) |
| OBSERVER | `dispatch_state/lex_window_ledger_v2_observations.jsonl` (diagnostyka, parytet obserwator↔silnik; **NIGDY** wejście kalibracji) |

Uzasadnienie: wpisy obserwatorów zachowują wartość diagnostyczną (kontrfaktyk „co
policzyłby shadow"), a rozdział plików sprawia, że pomyłka analityka wymaga świadomego
sięgnięcia po inny plik zamiast filtra po kolumnie, o którym można zapomnieć.

Routing jest zaimplementowany **w jednym miejscu** (`_target_path`) i jest jedynym
miejscem w kodzie, które odwzorowuje rolę na plik.

---

## 4. Rekord — schemat v2

Cztery rodzaje rekordów w jednym append-only strumieniu, rozróżniane przez `record_kind`:
`heartbeat` · `decision` · `write_receipt` · `served_receipt`. Wspólny nagłówek:

```jsonc
{
  "schema": "lex_window_ledger.v2",
  "schema_version": 2,
  "record_kind": "decision",
  "emitted_at": "2026-07-27T19:01:26.310596+00:00",   // ISO-8601 UTC
  "decision_id": "…",   // tożsamość DECYZJI: run_id + cid + bag_signature + route_generation
  "attempt_id": "…",    // tożsamość POJEDYNCZEJ ewaluacji (uuid4); łączy 3 rodzaje rekordów
  "run_id": "…"         // jedno przebiegnięcie entrypointu (tick / zdarzenie / uruchomienie obserwatora)
}
```

`decision_id` grupuje **ponowne podejścia do tej samej decyzji** (retry, CAS-skip →
kolejna próba). `attempt_id` jest kluczem obcym dla `write_receipt` i `served_receipt`.

### 4.1 `record_kind: "decision"`

| Sekcja | Pole | Typ | Faza | Znaczenie |
|---|---|---|---|---|
| `caller` | `role` | `"writer"\|"observer"` | WB1 | rola z jawnego kontekstu |
| | `source` | str | WB1 | np. `plan_recheck.run_recheck` |
| | `trigger` | str | WB1 | `tick` / `recanon:pickup` / `redecide:override` / `observe` |
| | `can_persist_plan` | bool | WB1 | czy ta ścieżka może utrwalić plan (writer ⇒ true) |
| | `pid` | int | WB1 | diagnostyka |
| `courier_id` | | str | WB1 | CID (13.2: „cid" był brakiem v1) |
| `bag` | `signature` | str | WB1 | `_bag_signature` |
| | `active_order_ids` | [str] | WB1 | **exact active-order signature** — dokładny zbiór aktywnych zleceń |
| | `active_order_signature` | str | WB1 | sha1-16 z posortowanych `active_order_ids` |
| | `size` | int | WB1 | |
| `route` | `generation` | int\|null | WB1 | `plan_version` planu bazowego (route/bag generation) |
| | `sequence_lock` | bool | WB1 | stan `ENABLE_PLAN_SEQUENCE_LOCK` |
| `candidates` | `pool_size` | int | WB1 | liczba permutacji rozważonych |
| | `feasible` | int | WB1 | liczba przeszłych przez filtry |
| | `rejected` | obj | WB1 | licznik odrzuceń wg przyczyny: `precedence`, `no_return`, `metrics`, `carry_cap`, `breaches`, `delay_tol`, `r6_per_order` |
| | `summary` | [obj]\|null | WB1 | do `CANDIDATE_SUMMARY_MAX` najlepszych: `{perm, window_viol, drive_min, max_carry}` |
| `baseline` / `chosen` | `seq` | [[oid,`P`\|`D`]] | WB1 | sekwencja |
| | `window_viol` | int | WB1 | naruszenia okna odbioru |
| | `breaches` | int | WB1 | naruszenia R6 |
| | `drive_min` | float | WB1 | jazda |
| | `max_carry_min` | float | WB1 | maks. wiek niesionego |
| `items[]` | `order_id`, `kind` | str | WB1 | per sztuka |
| | `arrival_min` | float\|null | WB1 | przyjazd względem `now` |
| | `handoff_min` | float\|null | WB1 | **przekazanie** = przyjazd + dwell (OD-01: arrival ≠ handoff) |
| | `dwell_min` | float | WB1 | postój użyty w symulacji |
| | `possession_source` | str\|null | WB1 | źródło `picked_up_at` (`panel_ts` / `sim` / `none`) |
| | `parcel_mode` | str\|null | WB1 | tryb paczkowy zlecenia |
| | `raw_W_min` | float\|null | WB1 | surowe W (okno) — **bez** zaokrągleń prezentacyjnych |
| | `raw_drive_min` | float\|null | WB1 | surowa jazda dojazdu do stopu |
| | `raw_carry_min` | float\|null | WB1 | surowy wiek niesionego przy dostawie |
| `raw` | `W_*`, `drive_*`, `carry_*` | float | WB1 | surowe agregaty baseline/chosen (wejście progów WB2) |
| `guards` | `G1`…`G5` | obj\|null | **WB2** | `{verdict, threshold_effective, margin, reason}` — pola gotowe, wypełniane od WB2 |
| `loadgov` | `source`, `age_s`, `fingerprint`, `ewma`, `observed_at`, `valid_until`, `generation` | | **WB2 (G5)** | snapshot governora; `null` ⇒ WB2 stosuje strict 5 |
| `write` | `cas_expected`, `cas_current`, `outcome`, `receipt` | | WB1 (część) | patrz 4.2 |
| `served` | `outcome`, `receipt` | | **WB1 faza 2** | patrz 4.3 |
| `validator` | `final` | obj\|null | **WB2 (G4)** | wynik finalnego walidatora post-floor/post-pin |
| `decision` | `decided` | bool | WB1 | **fakt wyboru** (patrz §2); definicja zawiera rolę — obserwator ma tu ZAWSZE `false` |
| | `candidate_chosen` | bool | WB1 | czy warstwa wskazała kandydata (bez względu na rolę) — rozdziela „policzył" od „zdecydował" |
| | `identity` | bool | WB1 | kandydat == baseline (lex już optymalny) |
| `flags` | `apply`, `shadow`, `ledger_v2` | bool | WB1 | efektywne wartości w momencie decyzji |
| | `fingerprint_sha` | str | WB1 | sha1-16 z `common.flag_fingerprint()`; PEŁNY odcisk raz na przebieg w `heartbeat` (powtarzany w każdym wierszu rozdymał rekord ~3×) |
| | `code_fingerprint` | str | WB1 | sha1-16 źródła warstwy reorderu (wykrywa zmianę kodu bez zmiany flag) |
| `thresholds` | `window_tol_min`, `delay_tol_min`, `max_stops` | float | WB1 | **progi efektywne** użyte w tym wywołaniu |
| `coverage` | `run_seq` | int | WB1 | numer rekordu w tym `run_id` (dziura ⇒ utrata) |
| | `dropped` | int | WB1 | ile rekordów odrzucił backpressure od startu procesu |
| | `degraded` | bool | WB1 | true gdy budżet/limit aktywny |
| | `build_us` | int | WB1 | koszt zbudowania rekordu (µs) |
| | `emit_us_prev` | int\|null | WB1 | pełny koszt emisji POPRZEDNIEGO rekordu (µs) — heartbeat kosztu |

### 4.1b `record_kind: "heartbeat"` — dowód pokrycia

Emitowany **raz na `run_id`**, przed pierwszym rekordem decyzji. Pola: `caller`,
`flags.fingerprint` (pełny), `flags.fingerprint_sha`, `flags.code_fingerprint`.

Pełni dwie role: (a) rozwija skrót `fingerprint_sha` z rekordów decyzji, (b) jest
**dowodem pokrycia** — przebieg writera bez heartbeatu w pliku oznacza, że proces nie
doszedł do warstwy okna. Bez tego cisza w ledgerze jest nieodróżnialna od „nie było
rozjazdów", co było jedną ze współ-awarii incydentu 492.

### 4.2 `record_kind: "write_receipt"` — fakt zapisu (osobny od `decided`)

Emitowany przez ścieżkę zapisu po próbie CAS. Pola: `attempt_id`, `decision_id`,
`courier_id`, `outcome` ∈ `written` | `skipped_cas` | `failed` | `not_attempted`,
`cas_expected`, `cas_current`, `plan_version_after`, `writer` (`regen`/`retime`),
`error` (klasa wyjątku lub `null`).

**Reguła czytania (część kontraktu):** *brak* rekordu `write_receipt` dla danego
`attempt_id` oznacza **plan NIE został utrwalony**. Ścieżka `_gen_one_bag_plan` ma
kilka wyjść przed CAS (odrzucenie bramki L3, niekompletny plan) — nie emitują one
receiptu z rozmysłem: cisza jest jednoznaczna, a dopisywanie „nie zapisano" w każdym
z tych wyjść tworzyłoby drugi punkt prawdy o zapisie. Para
`decided=true` + brak receiptu = **decyzja, która nigdy nie dojechała do pliku** —
jedna z klas zdarzeń, których v1 nie potrafił pokazać.

### 4.3 `record_kind: "served_receipt"` — fakt podania (WB1 faza 2)

Osobny od `write_receipt` z rozmysłem: plan zapisany do `courier_plans.json` nie jest
tożsamy z planem, który zobaczył kurier (`live_eta`, DTO panelu, APK). Pola:
`attempt_id`, `decision_id`, `courier_id`, `surface` (`api` / `panel` / `apk` /
`live_eta`), `outcome`, `served_at`, `stop_sequence_hash`. **API modułu istnieje i jest
przetestowane; wpięcie emiterów po stronie API = WB1 faza 2** (osobne zadanie, poza
tą fazą).

---

## 5. Kompatybilność wsteczna i wygaszenie v1

**Jeden przełącznik, nigdy dwóch writerów naraz.** Kanoniczny moduł jest właścicielem
obu formatów i wybiera dokładnie jeden:

| `ENABLE_LEX_WINDOW_LEDGER_V2` | Zachowanie |
|---|---|
| **OFF (default)** | zapis **v1, bajt-w-bajt jak dziś** do `lex_committed_window_shadow.jsonl` (z polem `applied`). Pełny no-op względem produkcji — to jest rollback. |
| **ON** | zapis **wyłącznie v2** z rozdziałem ról; plik v1 **zamrożony** (przestaje rosnąć, zostaje na dysku jako historia). |

Dzięki temu:
- deploy kodu **nic nie zmienia** (dowodliwe: flaga OFF = ścieżka v1),
- start zbierania czystego baseline'u = **flip jednego klucza w `flags.json`, hot-reload,
  bez restartu**,
- rollback = flip OFF; żadnej migracji danych, żadnej utraty obserwowalności w oknie
  między deployem a flipem,
- **nie ma momentu, w którym dwa writery piszą tę samą prawdę.**

**Czytniki v1 nie pękają:** grep po repo wykazał, że jedynymi konsumentami
`lex_committed_window_shadow.jsonl` są `plan_recheck.py` (writer) i
`tests/test_lex_committed_window.py`. Zewnętrznych czytników brak — plik po flipie
pozostaje czytelny jako zamrożona historia (m.in. korpus 561 wierszy użyty w symulacji
Sol RUN3-b).

---

## 6. Bezpieczeństwo hot-path (warunek Sol S1-cond7)

| Wymóg | Realizacja |
|---|---|
| **budżet narzutu** | **zmierzony 2026-07-27** (500 iteracji, OSRM zamockowany): mediana **+46,3 µs** na wpis ponad ścieżkę v1 (114,5 → 160,7 µs), z czego budowa rekordu ~16 µs, append ~74 µs. Rekord decyzji **3,2 kB** (v1: 282 B). Realny `osrm_client.table()` to 20–50 ms, więc udział ledgera w wywołaniu warstwy okna wynosi **0,09–0,23 %**. Skala 3000 wpisów/dobę = 0,14 s CPU i 9,5 MB/dobę. Bieżące wartości w `coverage.build_us` / `coverage.emit_us_prev`. |
| **błąd zapisu nie wywraca decyzji** | całość `record_*` w `try/except Exception` → `_log.warning` + licznik; funkcje **nigdy nie rzucają** i zwracają `None`. Wywołanie z warstwy decyzji ignoruje wynik. |
| **kill-switch** | `ENABLE_LEX_WINDOW_LEDGER_V2` czytany **przez `decision_flag()` w momencie wywołania** ⇒ hot-reload z natury, bez cache'a module-level |
| **backpressure** | `MAX_RECORDS_PER_RUN` (default 2000) na `run_id`; po przekroczeniu rekordy odrzucane, `coverage.dropped` rośnie, `degraded=true` |
| **rotacja bez utraty** | przed appendem: jeśli plik > `MAX_BYTES` (default 64 MiB) → `os.rename` na `<plik>.<YYYYmmddTHHMMSSZ>` **pod tym samym namespace-lockiem** co append. Rename zachowuje wszystkie dane; `jsonl_appender._open_current` sam wykrywa przeniesiony inode i ponawia. |
| **ograniczenie rozmiaru rekordu** | `candidates.summary` przycięte do `CANDIDATE_SUMMARY_MAX` (12); `items[]` ograniczone rozmiarem worka (≤ `LEX_WINDOW_MAX_STOPS` = 8) |

### 6.1 Dlaczego kill-switch NIE trafia do `_D3_FALA_A_FLAGS`

`_D3_FALA_A_FLAGS` istnieje wyłącznie po to, by odświeżać **globale modułu
`plan_recheck` cache'owane przy imporcie** (flagi decyzyjne czytane w gorącej pętli).
Kill-switch ledgera jest czytany `decision_flag()` **przy każdym wywołaniu**, więc jest
hot-reloadowalny z konstrukcji. Dopisanie go do `_D3_FALA_A_FLAGS` utworzyłoby **drugi
nośnik tej samej prawdy** (globalny w `plan_recheck` obok odczytu w module ledgera) —
dokładnie ten wzorzec, który zakazuje `CLAUDE.md` („konkurencyjni writerzy tej samej
prawdy"). Precedens w kodzie: `ENABLE_STAGE_TIMING_OBSERVATION` (`common.py`) — również
niedecyzyjny kill-switch poza `ETAP4_DECISION_FLAGS`, bo nie zmienia treści decyzji.

Konsekwencja rejestrowa: flaga jest w `common.py` + `tools/flag_lifecycle_registry.json`,
**poza** `ETAP4_DECISION_FLAGS` (nie wchodzi do `flag_fingerprint`, nie jest strippowana
przez conftest — jej wartość w testach jest deterministyczna).

---

## 7. Projekt unitu obserwatora (opis, BEZ instalacji)

Instalacja = osobny ACK, poza WB1 fazą 1. Po stronie kodu **nic nie trzeba zmieniać** —
obserwatorzy są odcięci brakiem kontekstu writera. Zalecenia do przyszłego wdrożenia:

1. `dispatch-b-route-shadow.service.d/route-flag-parity.conf` — usunąć
   `Environment=ENABLE_LEX_COMMITTED_WINDOW=1` **albo** opatrzyć komentarzem, że dotyczy
   wyłącznie geometrii trasy B i **nie** oznacza roli writera (dziś ta linia wprowadza
   w błąd operatora czytającego `systemctl show`).
2. Żaden unit obserwatora **nie** dostaje zmiennej roli — brak zmiennej jest kontraktem
   („rola pochodzi z kodu, nie z env").
3. Rotacja: dopisać oba pliki v2 do `core/jsonl_rotation.JSONL_PATHS` przy okazji
   deployu (wbudowana rotacja rozmiarowa działa niezależnie i jest bezpiecznikiem).

---

## 8. Bramka jakości tego schematu

| Test | Plik |
|---|---|
| obserwator **nigdy** nie tworzy wpisu kanonicznego (negatywny oracle) | `tests/test_wb1_lex_window_ledger_v2.py` |
| semantyka `decided` / `written` / `served` rozłączna | j.w. |
| błąd appendu **nie** wywraca decyzji | j.w. |
| kompatybilność: flaga OFF ⇒ v1 bajt-w-bajt, v2 nie powstaje | j.w. |
| mutation: usunięcie rozróżnienia ról ⇒ **czerwone** | j.w. |
| ratchet: `applied` nie może wrócić do formatu v2 | j.w. |

---

**Owner:** Adrian Czapla <ac@nadajesz.pl> · **Autor spec:** WB1 faza 1 (Opus pod nadzorem CTO/Fable)
