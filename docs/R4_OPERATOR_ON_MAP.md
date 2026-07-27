# R4 — mapa kontraktu `availability_by_cid` i wygasanie `OPERATOR_ON`

Karta: `engine.operator-on-expiry-r4` (owner ACK „abc"). Protokół #0, ETAP 3 — mapa
kompletności PRZED implementacją. Stan repo: baza `master c1a32e082`, gałąź
`fix/r4-operator-on-expiry-20260728`.

Żywy przypadek: CID 284 (Mateusz Lach) — rekord `state=OPERATOR_ON`,
`provenance=assignment_event`, `updated_at=2026-07-26T16:13:16Z`, proponowany wieczorem
27.07 mimo braku w grafiku i braku jakiejkolwiek zmiany stanu w międzyczasie.

---

## 1. Writerzy `availability_by_cid`

| # | Writer | Plik:linia | Co zapisuje | Pod lockiem? |
|---|---|---|---|---|
| W1 | `set_operator_availability()` — **jedyny sankcjonowany writer** | `courier_availability.py:377` | `{state, provenance, updated_at}` per CID | ✅ `_store_lock` + atomic write |
| W2 | konsola koordynatora „X pracuje" / „wraca" | `manual_overrides.py:387` (`_do_include`) | `OPERATOR_ON` / `None` (neutral) via W1, `provenance=coordinator_console` | ✅ przez W1 |
| W3 | konsola koordynatora „nie pracuje" / `/stop` | `manual_overrides.py:439` (`_do_exclude`) | `OPERATOR_OFF` via W1, `provenance=coordinator_console` | ✅ przez W1 |
| W4 | handler `COURIER_ASSIGNED` | `state_machine.py:1290` | `OPERATOR_ON` via W1, `provenance=assignment_event`, `at=event.created_at` | ✅ przez W1 |
| W5 | `save_legacy_payload()` — zapis pól legacy | `courier_availability.py:355` | **zachowuje** `availability_by_cid` bieżącego store'u (RMW-guard); nie modyfikuje kluczy CID | ✅ `_store_lock` |
| W6 | **`manual_overrides_daily_reset.py`** — writer KONKURENCYJNY, poza repo | `/root/.openclaw/workspace/scripts/manual_overrides_daily_reset.py:48-54` | przepisuje CAŁY plik: zeruje `excluded`, `excluded_cids`, `working`; `availability_by_cid` przechodzi round-tripem **nietknięty** | ❌ **omija `_store_lock`** |

Precedencja przy W1: opóźniony `assignment_event` nie może nadpisać nowszej jawnej decyzji —
o zwycięstwie decyduje czas zdarzenia (`courier_availability.py:426-433`).

## 2. Konsumenci

| # | Konsument | Plik:linia | Rola |
|---|---|---|---|
| C1 | `courier_resolver.dispatchable_fleet()` | `courier_resolver.py:1802` (`load_context`), `:1827` (`resolve`) | **jedyny konsument decyzyjny** — buduje pulę kurierów; gated `ENABLE_CID_AVAILABILITY_CONTRACT` (ŻYWO = `True`) |
| C2 | `resolve()` | `courier_availability.py:201` | jedyna funkcja rozstrzygająca `dispatchable` |

Ścieżki, które **NIE** są konsumentami tego kontraktu (sprawdzone, żeby nie zgubić bliźniaka):
`identity/sources.py:196` — to nazwa **tabeli SQLite** w zewnętrznej bazie tożsamości, zbieżność nazw,
zero związku z modułem. Pozostali wołający `dispatchable_fleet()` (`shadow_dispatcher`,
`czasowka_scheduler`, `postpone_sweeper`, `replay_failed`, `tools/*`) konsumują **wynik** C1,
więc dziedziczą politykę automatycznie — nie są osobnym miejscem fixu.

## 3. Ścieżka bliźniacza — i źródło defektu

Kontrakt CID-keyed (2026-07-23) **zastąpił** legacy trójkę `excluded` / `excluded_cids` / `working`
jako źródło puli, ale przejął z niej wyłącznie *semantykę*, **nie cykl życia**:

| Własność | Legacy (`excluded`/`working`) | `availability_by_cid` (dziś) |
|---|---|---|
| Kasowanie dobowe | ✅ `manual_overrides_daily_reset.py` (timer `dispatch-overrides-reset.timer`, `OnCalendar=*-*-* 06:00:00 Europe/Warsaw`) | ❌ **brak** — reset nie zna tego klucza |
| Okno czasowe wpisu | ✅ `working[cid] = {start, end}` — jawne godziny | ❌ brak, rekord bezterminowy |
| Cap grafikiem | ✅ GRAFIK-CAP `effective_shift_end(...)` → po końcu zmiany odrzut `working_override_ended` (`courier_resolver.py:1954-1961`) | ❌ brak |
| Świadomość czasu w decyzji | — | ❌ `resolve()` zwraca `dispatchable = (state is OPERATOR_ON)` **bez odczytu `updated_at`** (`courier_availability.py:220-228`) |

**Root cause:** migracja na kontrakt CID-keyed zgubiła cykl życia „do końca dnia", który
bliźniak egzekwował od 2026-05-07 (Backlog #5 — ten sam defekt klasy „wpis persistuje przez dni",
wtedy dla 13 nazw przez 4 dni). Rekord `assignment_event` sprzed doby jest dziś traktowany jako
prawda o BIEŻĄCEJ dobie i bezwarunkowo wpuszcza kuriera do puli.

Dowód na żywym stanie (`dispatch_state/manual_overrides.json`, odczyt 27.07 ~21:10Z):
22 rekordy, z czego **10 `OPERATOR_ON` starszych niż bieżąca doba operacyjna** —
CID 538 i 471 z 24.07, CID 526 i 543 z 25.07, CID 284/179/289/409/520/545 z 26.07.
Wszystkie dziś bezwarunkowo dispatchowalne.

## 4. Warstwa fixu (ETAP 1 — u źródła)

Fix idzie do **`courier_availability.resolve()`** — jedynej funkcji rozstrzygającej dostępność.
Wygasanie jest **predykatem odczytu** (liczonym z `updated_at`), a nie nowym writerem ani
rozszerzeniem skryptu resetu. Uzasadnienie:

- **Jeden kanoniczny owner.** Polityka mieszka tam, gdzie zapada decyzja `dispatchable`;
  przyszły drugi konsument `resolve()` nie może jej pominąć.
- **Predykat odczytu nie może zdesynchronizować się ze store'em.** Job resetu może się spóźnić,
  paść albo (jak dziś) nie znać klucza — wtedy stan na dysku kłamie aż do następnego przebiegu.
- **Zero mutacji żywych danych runtime** → deploy nie jest destrukcyjny, rollback = flip flagi OFF.
- Rozszerzenie W6 byłoby dołożeniem polityki do **konkurencyjnego writera omijającego lock** —
  utrwalałoby dług zamiast go zdejmować.

## 5. Polityka wygasania (X)

**Rekord wygasa na pierwszej granicy doby operacyjnej — 06:00 Europe/Warsaw — ściśle po `updated_at`.**

Uzasadnienie X: to **dokładnie ta sama granica**, na której bliźniak kasuje `excluded`/`working`
(`OnCalendar=*-*-* 06:00:00 Europe/Warsaw`, DST-aware). Wybór stałej N godzin tworzyłby *drugą*
politykę życia tej samej klasy stanu, obok istniejącej — czyli dokładnie to, czego zakazuje
„jeden kanoniczny owner kontraktu". Granica doby jest polityką **odziedziczoną, nie wymyśloną**.

Symetria ON/OFF: wygasają oba stany, bo bliźniak też kasuje oba (`working` **i** `excluded`) o 06:00 —
„wykluczony do końca dnia" i „pracuje do końca dnia" mają ten sam horyzont.

Weryfikacja polityki na żywych danych:

| CID | `updated_at` | granica | werdykt przy fladze ON |
|---|---|---|---|
| 284 (incydent) | 26.07 16:13Z | 27.07 04:00Z | **WYGASŁY** → wypada z puli ✅ |
| 179/289/409/520/545 (26.07) | 26.07 | 27.07 04:00Z | **WYGASŁE** ✅ |
| 538/471 (24.07), 526/543 (25.07) | 24–25.07 | 25–26.07 04:00Z | **WYGASŁE** ✅ |
| 492 (aktywny dziś) | 27.07 20:30Z | 28.07 04:00Z | świeży → **bez zmiany** ✅ |
| 484/400/470/457 (dziś) | 27.07 | 28.07 04:00Z | świeże → **bez zmiany** ✅ |
| 7× `OPERATOR_OFF` konsoli | 27.07 19:55Z | 28.07 04:00Z | świeże → STOP **trzyma** ✅ |

Czyli: flaga ON zdejmuje z puli 10 rekordów-widm i **nie rusza żadnego dzisiejszego** —
w tym nie wskrzesza żadnego dzisiejszego STOP-u koordynatora.

Rekord wygasły jest traktowany **jak nieobecny** — decyzja spada na grafik, czyli dokładnie
ścieżka, którą już dziś realizuje `None` (neutral) z konsoli. Zero nowego stanu, zero nowej
gałęzi u konsumenta.

**Brak/niepoprawny `updated_at`** (rekord ręcznie edytowany — W1 zawsze zapisuje stempel):
świeżości nie da się dowieść, więc rekord `OPERATOR_ON` **wygasa** (nie wolno wpuszczać do puli
na niedowodliwej przesłance), a `OPERATOR_OFF` **zostaje** (zdjęcie ograniczenia wymaga DOWODU,
że granica minęła — nie jego braku). Jedna zasada: *niedowodliwa świeżość nigdy nie nadaje
dostępności*.

## 6. Flaga

`ENABLE_OPERATOR_AVAILABILITY_EXPIRY`, default **OFF** (`common.py` stała-fallback + wpis
w `ETAP4_DECISION_FLAGS` — flaga zmienia treść decyzji, więc trafia na listę i tym samym jest
wycinana przez `tests/conftest.py::_isolate_flags_json`, co daje hermetyczność suity względem
żywego `flags.json`). Kanon po aktywacji = `flags.json`, hot-reload między wywołaniami
`dispatchable_fleet()` (flaga czytana raz na wywołanie w `load_context()`, nie per kurier).
Rollback = klucz `false` / usunięcie klucza. Rejestr: `tools/flag_lifecycle_seed.py --merge`.

## 7. Poza zakresem R4 — otwarte, do decyzji ownera

1. **`OPERATOR_ON` nie dostaje `shift_end`.** U konsumenta (`courier_resolver.py:1844-1848`)
   gałąź `OPERATOR_ON` ustawia wyłącznie syntetyczną pozycję; `cs.shift_start`/`cs.shift_end`
   ustawia dopiero `else` (ścieżka grafikowa). Kurier z `OPERATOR_ON` wchodzi więc do puli
   z `shift_end=None` → **wewnątrzdobowy** cap końcem zmiany (GRAFIK-CAP, R-NO-WASTE) dla niego
   nie działa. To defekt tej samej rodziny co R4, ale osobny: dotyka rekordów **świeżych**
   (dzisiejszych), więc ma inny blast radius i wymaga własnego oracle + ACK. Nie ruszone.
2. **Krótsze okno dla `assignment_event`.** Granica doby zostawia rekord z 16:13 żywy do 04:00Z
   następnego dnia. Jeśli owner uzna, że pojedyncze przypisanie to zbyt słaba przesłanka na
   całą dobę, można zacieśnić TTL wyłącznie dla `provenance=assignment_event` (np. 6 h).
   Świadomie **nie zbudowane** — to byłaby druga polityka obok granicy doby; do decyzji.
3. **W6 omija `_store_lock`.** `manual_overrides_daily_reset.py` przepisuje cały plik bez locka,
   równolegle z W1 (przypisania biegną też o 04:00Z). Dziś ryzyko utraty zapisu jest małe, ale
   realne i niezależne od R4. Do osobnej bramki.
