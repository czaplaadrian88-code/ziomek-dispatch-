# G5 — kanoniczny PRODUCENT snapshotu load-governora

**Status:** ZBUDOWANE, flaga `ENABLE_LOADGOV_SNAPSHOT_PUBLISH` **OFF**. Zero live, zero flipów.
**Podstawa:** plan v4 CZASY 492 (`/root/handover/CZASY_INCYDENT_492_DIAGNOZA_2026-07-27.md` §13.2 p.4)
+ specyfikacja Sola **RUN3-b sekcja 3, G5** + WB2 (`docs/WB2_CONDITIONAL_GUARDS.md`, czytnik
`core/loadgov_snapshot.py`) + raport adopcji GPS z 27.07 (`/root/handover/GPS_ADOPCJA_RAPORT_2026-07-27.md`).

**Poza zakresem:** Alarm certificate (OD-04), unifikacja mianownika serii legacy, monitor parytetu,
jakikolwiek flip. Wszystkie trzy = osobne bramki.

---

## 1. Problem, który to zamyka

WB2 zbudował KONSUMENTA kontraktu i zostawił go bez dostawcy. Cytat z RUN3-b:

> `plan_recheck` nie może dostać aktualnego `loadgov_ewma` z obecnego mechanizmu: EWMA jest stanem
> pamięci `dispatch_pipeline`, a ustawiana tolerancja jest module-global w tamtym procesie. Recompute
> w oneshocie stworzyłby drugi, niestabilny governor. Potrzebny jest jeden kanoniczny producer
> publikujący atomowy, wersjonowany snapshot.

Czyli: nie wolno policzyć EWMA drugi raz — wolno **opublikować tę, która już istnieje**, w formie,
której drugi proces może zaufać albo ją odrzucić.

## 2. Kto publikuje (i dlaczego tylko on)

`assess_order` — a więc i `_loadgov_compute` — biega w CZTERECH rodzajach procesów:

| Proces | Żywotność | EWMA po pierwszym ticku | Publikuje? |
|---|---|---|:--:|
| `dispatch-shadow` | pętla ciągła | wygładzana, ciągła | **TAK** |
| czasówka (`czasowka_scheduler`) | świeży proces CO MINUTĘ | = obciążenie chwilowe | nie |
| `plan_recheck` | świeży proces per tick | = obciążenie chwilowe | nie (to KONSUMENT) |
| panel-quote subprocess | świeży proces | = obciążenie chwilowe | nie |

Prawo do publikacji jest **jawnie zgłaszane**, nie wywnioskowane ze środowiska:
`shadow_dispatcher.run()` woła `loadgov_publisher.claim_producer_role(C.LOADGOV_SNAPSHOT_PRODUCER_ROLE)`.
Zgłoszenie jest związane z PID-em — fork nie dziedziczy prawa. Proces bez zgłoszenia zwraca
`not_producer` i nie dotyka dysku. Domyślnie więc **nikt nie publikuje**, dopóki nie ma i zgłoszenia,
i włączonego kill-switcha.

## 3. Mianownik EQUAL-TREATMENT — sedno zmiany

Seria LEGACY (`dispatch_pipeline._loadgov_compute`) dzieli przez `len(fleet_snapshot)`, czyli przez
flotę **dispatchowalną**. `courier_resolver.dispatchable_fleet` odrzuca z niej kurierów bez pozycji
(powód `no_position`) — a ich zamówienia ZOSTAJĄ w liczniku (`orders_state.json`). Kurier na zmianie,
który nie nadaje GPS, znika więc z mianownika, nie znikając z licznika.

Kierunek błędu jest **niebezpieczny**: obciążenie wychodzi ZAWYŻONE, czyli bliżej progu
`OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD = 4,5`, za którym leży poluzowanie tolerancji okna z 5 na 10 min.

Negatywny oracle bramki (`test_negatywny_oracle_mianownik_slepy_na_brak_gps_przerzuca_prog`):

| | licznik | mianownik | load | werdykt |
|---|---:|---:|---:|---|
| mianownik ślepy na brak GPS | 70 | 15 | **4,667** | ≥ 4,5 → przeciążenie |
| EQUAL-TREATMENT | 70 | **20** | **3,5** | < 4,5 → brak przeciążenia |

Ta sama flota, ten sam moment, dwa przeciwne werdykty. Raport adopcji GPS z 27.07 pokazuje, że to nie
jest defekt teoretyczny: **5 kurierów (CID 500, 515, 289, 540, 536) nie ma ani jednego fixa GPS w 7
dni**, a wykonali 88 dowozów.

Źródłem liczby jest `courier_resolver.last_fleet_filter_stats()` — ekspozycja liczby, którą
`dispatchable_fleet` **i tak już liczy** w `_rejected_for_log`, a którą dotąd wyrzucaliśmy zaraz po
zalogowaniu. To CZYTELNIK już policzonej prawdy, nie drugi writer: `courier_resolver` pozostaje
jedynym właścicielem decyzji „kto jest dostępny".

> ⚠ **Migawka jest „ostatnia w tym procesie", nie „ta z tego ticku".** `dispatchable_fleet()` bywa
> w shadow wołane także poza pętlą decyzji (detektor GPS-01, cache 60 s), więc `no_position` może
> pochodzić z przebiegu o kilkadziesiąt sekund starszego niż licznik. Stąd `LOADGOV_FLEET_STATS_MAX_AGE_S`
> (odrzucenie za starej migawki ⇒ brak publikacji) i pole `fleet_stats_age_s` w snapshocie — wiek jest
> **zapisany, nie przemilczany**.

> ⚠ **Precyzja zależy od `ENABLE_CID_AVAILABILITY_CONTRACT`.** Przy fladze ON (stan ŻYWY) `no_position`
> jest sprawdzane PO bramce dostępności, więc liczy dokładnie „na zmianie, bez pozycji". Przy fladze
> OFF (ścieżka legacy, m.in. domyślna w testach) sprawdzenie wyprzedza grafik, więc doliczani są też
> kurierzy spoza zmiany bez pozycji — mianownik wychodzi ZA DUŻY, czyli obciążenie ZANIŻONE, czyli
> degradacja w stronę STRICT. Kierunek bezpieczny; oba zachowania są przybite testami.

**Seria legacy zostaje NIETKNIĘTA.** Jej progi 2,7 / 3,5 / 3,0 (`ENABLE_FLEET_LOAD_GOVERNOR`, flaga
ŻYWA) były kalibrowane dokładnie na starym mianowniku — podmiana bez rekalibracji zmieniłaby
zachowanie kary worka i alertu trybu defensywnego bez ACK ownera. Dlatego snapshot niesie OBIE liczby
(`ewma` equal-treatment oraz `legacy_ewma`, `couriers_dispatchable`, `couriers_no_position`), żeby
rozjazd był **mierzalny w cieniu**, a nie domniemany. Unifikacja = osobna decyzja ownera (§7).

Wspólny dla obu serii jest wyłącznie RACHUNEK: `core/loadgov_ewma.ewma_step`. Dwie kopie alfy to dwie
polityki wygładzania, które rozjadą się przy pierwszej zmianie `tau` — ratchet
`test_ratchet_silnik_uzywa_wspolnego_jadra_ewma` tego pilnuje.

## 4. Kontrakt snapshotu

Ścieżka: `dispatch_state/loadgov_snapshot.json` (stała `core.loadgov_snapshot.SNAPSHOT_PATH` — JEDNO
źródło, producent ją importuje, nie powtarza).

Przykład NIE jest ręcznie napisany — to realny plik wyprodukowany scenariuszem negatywnego oracle
(70 zleceń, 15 kurierów dispatchowalnych, 5 na zmianie bez GPS):

```json
{
  "active_orders": 70,
  "code_fingerprint": "ae50fa8908b14018",
  "couriers_dispatchable": 15,
  "couriers_no_position": 5,
  "denominator_basis": "equal_treatment",
  "eligible_couriers": 20,
  "ewma": 3.5,
  "ewma_samples": 2,
  "ewma_tau_min": 15.0,
  "fingerprint": "1074bd0844909375",
  "flag_fingerprint_sha": "be4d126fe6a3ce23",
  "fleet_stats_age_s": 60.0,
  "generation": 1,
  "legacy_ewma": 4.667,
  "load_now": 3.5,
  "observed_at": "2026-07-27T18:01:00+00:00",
  "producer_pid": 2307731,
  "producer_role": "dispatch-shadow",
  "producer_started_at": "2026-07-27T17:02:00+00:00",
  "schema": "loadgov_snapshot.v1",
  "ttl_s": 180.0,
  "valid_until": "2026-07-27T18:04:00+00:00"
}
```

Klucze są sortowane (`sort_keys=True`), plik ma prawa `0644`. Czytnik na tym pliku zwraca
`source="snapshot"` — i mimo `legacy_ewma` ponad progiem 4,5 wciąż `window_tol_min → (5.0,
"strict_no_alarm_certificate")`.

Pola `ewma`, `observed_at`, `valid_until`, `generation`, `fingerprint` to `REQUIRED_FIELDS` czytnika —
kontrakt jest **all-or-none**, żeby częściowy plik nigdy nie uchodził za świeży pomiar. Reszta to
pola wymienione wprost w RUN3-b (`active_orders`, `eligible_couriers`, `producer_role`,
code/flag fingerprint) plus jakość pomiaru i materiał do decyzji o unifikacji.

`generation` = numer publikacji **w obrębie jednej instancji producenta** (start od 1). Razem
z `producer_pid` i `producer_started_at` odpowiada na pytanie „czy to ta sama, ciągła seria, co
poprzednio" — restart shadow widać jako powrót do 1 przy nowym `producer_started_at`.

## 5. Świeżość: okres publikacji vs `valid_until`

| parametr | wartość startowa | znaczenie |
|---|---:|---|
| `LOADGOV_SNAPSHOT_MIN_INTERVAL_S` | 30 s | dławienie **zapisu** (seria konsumuje KAŻDĄ próbkę) |
| `LOADGOV_SNAPSHOT_TTL_S` | 180 s | ważność; przycinana od dołu do 2× okresu |
| `LOADGOV_SNAPSHOT_MIN_SAMPLES` | 2 | rozgrzewka — po jednej próbce „EWMA" = obciążenie chwilowe |
| `LOADGOV_FLEET_STATS_MAX_AGE_S` | 120 s | maksymalny wiek migawki puli przyjmowanej jako mianownik |

Czytnik WB2 **nie ma własnego progu staleness** — bierze `now > valid_until` wprost z pliku. Całą
politykę świeżości definiuje więc producent i to on odpowiada za to, żeby `valid_until` przeżyło
przerwę między dwoma zapisami zdrowego procesu. Stąd twarde przycięcie TTL do 2× okresu publikacji:
konfiguracja z `flags.json`, która ustawiłaby TTL poniżej dławienia, dostaje przycięcie i ostrzeżenie
w logu, a nie cichą dziurę, w której konsument widzi `expired` w środku normalnej pracy.

Cisza dłuższa niż TTL (brak nowych zleceń ⇒ brak próbek ⇒ brak publikacji) wygasza snapshot
**świadomie**: brak zleceń to nie jest stan przeciążenia, a degradacja idzie w stronę STRICT.

## 6. Fail-safe — każda ścieżka błędu kończy się STRICT

`observe()` nigdy nie rzuca i zwraca powód decyzji. Publikacji NIE MA, gdy: `flag_off`,
`not_producer`, `no_orders`, `no_fleet_stats`, `stale_fleet_stats`, `no_eligible`, `warmup`,
`throttled`, `write_error`, `error`. W każdym z tych przypadków plik albo nie powstaje, albo zostaje
poprzedni, który sam wygaśnie — a konsument bez ważnego snapshotu bierze **STRICT 5**.

Zapis jest atomowy: `mkstemp` w katalogu docelowym → `json.dump` → `flush` → `fsync(plik)` →
`os.replace` → `fsync(katalog)`. Rename w obrębie katalogu jest na POSIX atomowy, więc czytelnik widzi
albo poprzedni snapshot, albo kompletny nowy — nigdy obcięty JSON. `fsync` domyka to na wypadek utraty
zasilania. Nieudany zapis NIE przesuwa `generation` (kolejny tick ponowi) i nie zostawia pliku
tymczasowego.

Sam zapis idzie przez bufor efektów K08 (`effects_buffer.divert`) — plik snapshotu to efekt uboczny
ticku, nie wejście tej decyzji, więc `fsync` nie stoi w ścieżce krytycznej werdyktu.

## 7. Zero wpływu na decyzje — i kiedy to przestanie być prawdą

Wg **OD-04** samo wysokie EWMA nie uprawnia do tolerancji 10 min; wymagany jest kanoniczny **Alarm
certificate**, którego nie produkuje dziś żadna warstwa. Dlatego `window_tol_min` zwraca STRICT nawet
przy snapshocie z EWMA daleko ponad progiem (ratchet
`test_snapshot_ponad_progiem_NADAL_daje_strict`). To dlatego kill-switch producenta jest DZIŚ
niedecyzyjny i stoi poza `ETAP4_DECISION_FLAGS` (precedens `ENABLE_LEX_WINDOW_LEDGER_V2` z WB1).

> ⚠ **Warunek przeniesienia:** w dniu, w którym powstanie producent Alarm certificate,
> `ENABLE_LOADGOV_SNAPSHOT_PUBLISH` staje się flagą decyzyjną i MUSI trafić do
> `ETAP4_DECISION_FLAGS` (fingerprint cross-proces + strip w conftest).

## 8. Otwarte decyzje ownera

1. **Unifikacja mianownika serii legacy.** Czy `ENABLE_FLEET_LOAD_GOVERNOR` ma przejść na mianownik
   equal-treatment? Wymaga rekalibracji progów 2,7 / 3,5 / 3,0 (dziś kalibrowanych na mianowniku
   zaniżonym) i ACK. Materiał pomiarowy = `legacy_ewma` vs `ewma` w snapshotach z cienia.
2. **Flip `ENABLE_LOADGOV_SNAPSHOT_PUBLISH` na shadow.** Deploy kodu jest no-opem (flaga OFF);
   włączenie w cieniu = zapis pliku co ≤30 s, bez wpływu na decyzje.
3. **Alarm certificate** — dopóki go nie ma, ścieżka loose jest martwa (świadomie).

## 9. Rollback

`flags.json` → `ENABLE_LOADGOV_SNAPSHOT_PUBLISH=false` (hot-reload, bez restartu). OFF = plik
snapshotu przestaje być odświeżany, wygasa po TTL, czytnik wraca do `absent`/`expired` ⇒ STRICT 5,
czyli dokładnie stan sprzed G5.
