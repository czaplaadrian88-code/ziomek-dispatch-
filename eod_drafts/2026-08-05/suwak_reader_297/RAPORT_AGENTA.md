# RAPORT AGENTA — sesja 297: SUWAK AUTONOMII jako STAŁY CZYTELNIK shadow-review

**Gałąź:** `wt/suwak-reader-297-cto-20260805` · **base master:** `06e4d5c39`
**Worktree:** `/root/worktrees/dispatch_v2/active/20260805-suwak-reader-297-cto`
**Data:** 2026-08-05 · **Tryb pracy:** read-only wobec źródeł danych, zero systemd, zero flag, zero Telegrama.

---

## 1. CO POWSTAŁO (jednym zdaniem)

Jednorazowy composer z 04.08 (`/root/artifacts/suwak-composer-20260804/suwak_2_liczby.py`) stał się stałym,
dobowym czytelnikiem `dispatch_v2/tools/suwak_autonomii_review.py`, zarejestrowanym w
`tools/shadow_review_daily.py` (job pod `shadow-review.timer`, 05:10 UTC), który **codziennie dopisuje
jedną linię do szeregu czasowego** `/root/artifacts/shadow-review/suwak_autonomii.jsonl` i zostawia czytelny
snapshot dnia. Metodologia obu liczb jest przeniesiona **1:1** — udowodnione biegiem porównawczym 0-diff
na tych samych żywych danych (sekcja 5).

---

## 2. JAK DOKŁADNIE JEST WPIĘTY (mechanizm rejestracji)

Mechanika istniejącego przeglądu, ustalona z kodu i unitów (nie z dokumentacji):

1. `shadow-review.timer` → `OnCalendar=*-*-* 05:10:00 UTC`, `Persistent=true`, `RandomizedDelaySec=120`.
   Następny tik: **2026-08-06 05:10 UTC** (ostatni bieg: 2026-08-05 05:11).
2. `shadow-review.service` → `ExecStart=/root/.openclaw/venvs/dispatch/bin/python -m
   dispatch_v2.tools.shadow_review_daily --out-dir /root/artifacts/shadow-review`,
   `WorkingDirectory=/root/.openclaw/workspace/scripts`, `ProtectSystem=strict`, `ProtectHome=read-only`,
   **`ReadWritePaths=/root/artifacts/shadow-review`** (jedyna ścieżka zapisu w całym biegu).
3. `shadow_review_daily.py` trzyma listę `READERS = [Reader(...)]`; każdy `Reader` to `argv` wołane
   `subprocess.run([VENV_PY] + argv, cwd=SCRIPTS)`, z twardym `--no-telegram`, bramką świeżości korpusu
   (`corpus_health`, fail-closed) i zbiorczym `summary.json` + `SUMMARY.md` w katalogu dnia.

**Rejestracja suwaka = jeden wpis w `READERS` + jeden nowy parametr `Reader.out_dir_flag`:**

```python
Reader(
    name="suwak_autonomii",
    argv=["-m", "dispatch_v2.tools.suwak_autonomii_review", "--no-telegram"],
    corpus=None,
    out_dir_flag="--out-dir",
    note="SUWAK AUTONOMII (2 liczby ownera z 19.07) — ...",
)
```

Dwie decyzje projektowe, obie świadome i obie przetestowane:

* **`out_dir_flag` (nowe pole `Reader`, obsłużone w `run_reader`).** Dotychczasowi czytelnicy oddają wynik
  przez stdout, który harness zrzuca do `<nazwa>.log`. Suwak produkuje **trwały artefakt** (szereg + snapshot),
  więc musi dostać katalog dnia z zewnątrz. Bez tego pisałby we własny default, czyli potencjalnie **poza**
  `ReadWritePaths` — job wywaliłby się dopiero na produkcji. `run_reader` dokłada `["--out-dir", out_dir]`
  do argv tylko wtedy, gdy czytelnik ma ustawioną flagę; pozostali czytelnicy mają argv **bit w bit** takie
  jak dotąd (test `test_harness_bez_flagi_nie_dokłada_out_dir`).
* **`corpus=None` — celowo bez bramki korpusu harnessu.** Bramka `corpus_health` przyjmuje JEDNĄ ścieżkę
  na czytelnika, a suwak ma dwa niezależne źródła (`scripts/logs/shadow_decisions.jsonl` + rotacje ORAZ
  `dispatch_state/outcomes_clean_shadow.jsonl`). Gdyby uschło jedno, bramka wycięłaby cały czytelnik i w
  szeregu czasowym powstałaby **cicha dziura**. Dlatego świeżość obu źródeł sprawdza sam czytelnik tym samym
  progiem 48 h i tą samą logiką (`corpus_state`), a martwy/pusty/brakujący korpus daje **`null` + `reason`
  w rekordzie dnia**, nigdy liczby z martwego strumienia. Fail-closed przeniesiony z poziomu czytelnika na
  poziom pojedynczej liczby — dokładnie to, czego wymaga brief (pkt 4).

Nie dotykałem: `objm_lexr6_smoke_verdict` (auto-rollback flagi — nie uruchamiany, zostaje na liście
`EXCLUDED`), `ziomek_time_route` (relikt), żadnego unitu, timera, flagi ani żywego stanu.

---

## 3. FORMAT WYJŚCIA

| Artefakt | Ścieżka po merge | Charakter |
|---|---|---|
| **Szereg czasowy** | `/root/artifacts/shadow-review/suwak_autonomii.jsonl` | **append-only**, 1 linia/bieg, schemat `suwak_autonomii.series.v1` |
| Snapshot dnia (maszynowy) | `/root/artifacts/shadow-review/<data>/suwak_autonomii.json` | pełny detal, schemat `suwak_autonomii.2_liczby.v1` (zgodny z composerem) |
| Snapshot dnia (czytelny) | `/root/artifacts/shadow-review/<data>/SUWAK_AUTONOMII.md` | raport dla ownera: 2 liczby + rozbicia + sekcja uczciwości + manifest wejść |
| Log biegu | `/root/artifacts/shadow-review/<data>/suwak_autonomii.log` | stdout czytelnika, zrzucany przez harness (jak dla pozostałych) |

Konwencja lokalizacji wynika z sandboxa: `ReadWritePaths` daje **wyłącznie** `/root/artifacts/shadow-review`,
a unit ma jawny komentarz „świadomie NIE piszemy do `scripts/logs`". Dlatego szereg **nie** trafia do
`scripts/logs/` (jak sugerował przykład w briefie), tylko do korzenia katalogu raportów shadow-review —
tam, gdzie piszą wszyscy pozostali czytelnicy. Katalog dnia dostaje snapshot, korzeń trzyma szereg, bo szereg
z definicji przecina dni.

Rekord szeregu (skrót, pełny przykład: `eod_drafts/2026-08-05/suwak_reader_297/suwak_autonomii.jsonl`):

```
day, generated_utc, read_started_utc, mode=READ_ONLY, status=OK|DEGRADED, degraded[], snapshot,
liczba1{metric, pct, true, n, baseline_pct, dprime_pct, auto_route_auto_pct, pool_ge3, pool_le2,
        window{day_first,day_last,days_distinct}, intake{...}, top_blockers{}, reason},
liczba2{metric, global, pool_ge3, pool_le2, pool_unknown, scarcity{...}, pool_coverage_pct,
        window, pool_known_window, overlap, reason},
join{n_joined, matrix, agree_given_auto_ready_pct, agree_given_not_auto_ready_pct, reason},
inputs[{path, size_bytes, mtime_utc, age_hours, records, bad_lines, sha256, day_first, day_last, skipped}]
```

Każda liczba niesie ze sobą **liczność, okno i tożsamość wejścia (SHA-256)** — bez tego szereg byłby
wykresem bez skali. Zapis: szereg wyłącznie `open(..., "a")` + `fsync` (nigdy `"w"`, zero `os.remove`/
`truncate` w całym module — ratchet testowy), snapshoty atomowo `temp → fsync → rename`.

---

## 4. METODOLOGIA — CO ZOSTAŁO 1:1, CO ZMIENIONE (i dlaczego)

**Bez zmian (definicje metryk):** mianownik Liczby 1 (decyzje dyspozytorskie po odrzuceniu
`CZASOWKA_RECLAIM_EVALUATION`/`lifecycle_observation`, dedupe po `event_id`, wymóg pól bramki),
cztery miary (bazowy / D / D' / `auto_route=AUTO`), definicja kubełków puli (`>=3` / `<=2` / `unknown`,
`bool` NIE jest liczbą kurierów), `agree` = TOP-1 == realny kurier, dekompozycja nadwyżki niezgód
(poziom bazowy = stopa niezgody przy `pool>=3`, nadwyżka = redystrybucja z niedoboru), okno wspólne,
trend tygodniowy, join po `order_id` biorący PIERWSZĄ decyzję zamówienia, raportowanie „puli nieznanej"
osobno zamiast doliczania do kubełków.

**Zmiany implementacyjne (nie dotykają wartości — dowód w sekcji 5):**

1. **Strumieniowy odczyt z projekcją pól.** Composer trzymał całe rekordy w RAM; pojedynczy rekord decyzji
   ma dziś do **0,7 MB**, a korpus z rotacjami to ~176 MB. Job dobowy chodzi pod `MemoryMax=1G` razem z
   pozostałymi czytelnikami — pełne rekordy w liście były realnym ryzykiem OOM na produkcji. Czytane są
   tylko pola z `DECISION_FIELDS`/`OUTCOME_FIELDS`, z zachowaniem semantyki obecności klucza (mianowniki
   liczone po `k in r`, nie po wartości).
2. **Rotacje wykrywane globem** (`shadow_decisions.jsonl` + `.N` + `.N.gz`, `.append.lock` odrzucany),
   zamiast trzech ścieżek na sztywno. Powód konkretny: composer 04.08 czytał `.2.gz` (1253 rekordy,
   22–27.07); **05.08 tego pliku już nie ma** (logrotate `daily/rotate 30/size 100M`). Hardcode po cichu
   zawężałby okno i nikt by tego nie zobaczył.
3. **Wyjście = append do szeregu + snapshot dnia**, zamiast nadpisywanego raportu jednorazowego.
4. **Fail-soft na poziomie biegu, fail-closed na poziomie liczby** (opisane w sekcji 2).

---

## 5. BIEG RĘCZNY NA ŻYWYCH DANYCH + PORÓWNANIE Z 04.08

Bieg ręczny (read-only, wyjście do worktree, **nie** do `/root/artifacts/shadow-review` ani do `logs/`):

```
python -m dispatch_v2.tools.suwak_autonomii_review --no-telegram \
  --out-dir .../eod_drafts/2026-08-05/suwak_reader_297/2026-08-05
➤ WERDYKT: SUWAK OK | LICZBA1 19.73% auto(D) n=1551
  | LICZBA2 zgodnosc pool<=2 28.64% (n=419) vs pool>=3 66.91% (n=1381)      [1,7 s]
```

### 5a. Parytet metodologii — 0 diff

Uruchomiłem **oryginalny composer** (z `OUT_DIR` przepiętym na scratch, artefakty z 04.08 nietknięte) na
tych samych żywych plikach i porównałem 49 grup metryk pole po polu:

```
PARITY_ALL_EQUAL = True
```

Zgodne co do bitu: wszystkie cztery miary Liczby 1 (`true`/`n`/`pct`), rozkład `auto_route`, rozbicia
`pool_ge3`/`pool_le2`/`pool_unknown`, okno, `coverage`, cały `intake` (w tym duplikaty, wykluczenia,
złe linie), rodziny blokerów (`families_any`/`families_first`), `orders`; dla Liczby 2 — `global`/`pool_*`
w oknie pełnym i wspólnym, `scarcity_decomposition`, `disagreement_split`, `pool_coverage_pct`,
`pool_known_window`, cały `weekly_trend`; join (`n_joined`, macierz 2×2, oba warunkowe %); manifest SHA-256.
Dowód: `eod_drafts/2026-08-05/suwak_reader_297/parity_composer_vs_reader.txt`.

### 5b. Różnice wobec liczb z 04.08 — skąd się biorą

| Miara | composer 04.08 | czytelnik 05.08 | wyjaśnienie |
|---|---|---|---|
| LICZBA 1 (wariant D) | 24.40 % (n=2754, 14 dni) | **19.73 %** (n=1551, 9 dni) | **inne okno**: rotacja `.2.gz` (22–27.07, 1253 rek.) zniknęła z dysku między 04.08 a 05.08; okno skurczyło się do 28.07–05.08 |
| bazowy / D' / AUTO | 0.69 % / 21.82 % / 7.95 % | 1.03 % / 17.60 % / 10.06 % | ta sama zmiana okna + dobowa zmienność |
| LICZBA 2 `pool<=2` | 28.64 % (n=412) | **28.64 %** (n=419) | +7 rekordów nowej doby, wartość praktycznie stała |
| LICZBA 2 `pool>=3` | 66.67 % (n=1362) | **66.91 %** (n=1381) | +19 rekordów nowej doby |
| LICZBA 2 globalnie | 57.01 % (n=20293) | 57.06 % (n=20462) | +169 rekordów doby 05.08 |
| redystrybucja (udział w zmierzonym korpusie) | 20.9 % | 21.2 % | jw. |

Różnica Liczby 1 to **wyłącznie** skład okna, nie zmiana metody — potwierdza to rozbicie dobowe policzone
z bieżących rotacji: 28.07 → 27.2 %, 29.07 → 22.8 %, 30.07 → 15.1 %, 31.07 → 23.0 %, 01.08 → 25.5 %,
02.08 → 7.1 %, 03.08 → 22.1 %, 04.08 → 20.2 %. Rozrzut dobowy 7–27 % jest większy niż różnica między
oboma pomiarami — i to jest właśnie argument za szeregiem czasowym zamiast pojedynczego strzału.
**Konsekwencja do zakomunikowania ownerowi:** okno Liczby 1 jest funkcją logrotate i będzie się wahać
(dziś 9 dni); dlatego każdy rekord szeregu niesie własne `window`.

### 5c. E2E przez harness

Uruchomiłem `shadow_review_daily --only suwak_autonomii --out-dir <worktree>` (z `SCRIPTS` przepiętym na
pkgroot kandydata, żeby wołał kod z gałęzi, nie z mastera):

```
READERS: ['b_route', 'bundle_calib', 'pending_resweep', 'reassignment', 'suwak_autonomii']
| suwak_autonomii | OK | — | ➤ WERDYKT: SUWAK OK / LICZBA1 19.72% auto(D) n=1552 / ... |
exit 0
```

Powstały: `harness_e2e/suwak_autonomii.jsonl` (szereg), `harness_e2e/2026-08-05/{suwak_autonomii.json,
SUWAK_AUTONOMII.md, suwak_autonomii.log, summary.json, SUMMARY.md}`.
`/root/artifacts/shadow-review/` **nietknięty** (zweryfikowane listingiem: nadal tylko biegi 04.08 i 05.08
z timera). Dowód: `eod_drafts/2026-08-05/suwak_reader_297/harness_e2e_run.txt`.

---

## 6. TESTY I REGRESJA

**Nowe testy: 25** (`tests/test_suwak_autonomii_review.py`), wszystkie hermetyczne (tmp_path, syntetyczny
korpus, zero systemd/sieci/żywych ścieżek):

* **poprawność 2 liczb na ręcznie policzonym korpusie** — mianownik Liczby 1 (9 linii → 2 nieparsowalne →
  −1 duplikat `event_id` → −1 lifecycle → −1 bez pól bramki = 4) i wszystkie cztery miary; Liczba 2 z
  rozpisanym rachunkiem zgodności per kubełek w docstringu testu;
* **segmentacja puli** — granica 2/3, `None` → `unknown`, `bool` NIE jest liczbą kurierów;
* **dekompozycja nadwyżki** (baseline 25 %, obserwowane 3, oczekiwane 1,0, nadwyżka 2,0 = 66,67 % segmentu,
  50 % zmierzonego korpusu) — liczby wyprowadzone ręcznie, nie z biegu;
* **okno wspólne** przycina korpus outcomes do okna decyzji; **okno znanej puli** raportowane osobno;
* **join** bierze PIERWSZĄ decyzję zamówienia (100 ma dwie: auto-gotową i nie);
* **rotacje**: glob wykrywa `.1`/`.2.gz`/`.10` i odrzuca `.append.lock`; rotacja `.gz` wchodzi do mianownika;
* **fail-closed per liczba** (negatywny oracle: korpus sprzed 40 dni NIE daje Liczby 1; brak/pusty korpus
  outcomes → `null` + powód);
* **fail-soft**: wyjątek w `compute_liczba1` → `main` kończy się **exit 0**, rekord `DEGRADED` z powodem,
  a **druga liczba nadal policzona** (degradacja częściowa); awaria zapisu snapshotu też nie wywala biegu;
* **append-only**: dwa biegi = dwie linie, pierwsza bajt w bajt nietknięta; `--no-series` nie tworzy szeregu;
* **brak zapisu poza własnym wyjściem**: źródła bit w bit i `mtime_ns` niezmienione, jedyne nowe pliki to
  trzy artefakty czytelnika;
* **ratchet append-only** (zakaz `os.remove`/`unlink`/`rmtree`/`truncate`, szereg tylko w trybie `"a"`);
* **rejestracja**: `READERS` zawiera suwaka z `--no-telegram` i `out_dir_flag`, harness przekazuje
  `--out-dir`, a czytelnicy bez flagi mają argv niezmienione.

Istniejąca bramka harnessu (`tests/test_shadow_review_daily.py`, 13 testów) przechodzi bez zmian — w tym
oba ratchety, które obejmują teraz także nowy czytelnik: „każdy czytelnik ma `--no-telegram`" i „żaden
czytelnik nie pisze do `dispatch_state/` ani `scripts/logs/`".

**Regresja** (`pkgroot/20260805-suwak-reader-297-cto/dispatch_v2`, venv dispatch):

| | wynik |
|---|---|
| baseline PRZED (master `06e4d5c39`) | **7529 passed, 24 skipped, 8 xfailed, 0 failed** (819 s) |
| po zmianie | **7554 passed, 24 skipped, 8 xfailed, 0 failed** (765 s) |
| delta | **+25 = dokładnie nowe testy**, zero zmian w istniejących |

Surowe wyniki: `eod_drafts/2026-08-05/suwak_reader_297/regresja.txt`.

---

## 7. CO SIĘ STANIE PRZY PIERWSZYM BIEGU TIMERA PO MERGE (dla CTO)

1. **Merge do mastera aktywuje czytelnik sam, bez żadnej akcji na systemd.** `shadow-review.service` woła
   `-m dispatch_v2.tools.shadow_review_daily` z `WorkingDirectory=/root/.openclaw/workspace/scripts`, czyli
   ładuje kod z **mastera w chwili tiku**. Nowy `Reader` jest wpisem w tym module — nie ma nowego unitu,
   nie trzeba `daemon-reload`, nie ma flagi do flipnięcia. **Najbliższy tik: 2026-08-06 05:10 UTC**
   (+`RandomizedDelaySec=120`). Jeśli merge ma nie zadziałać tej nocy, jedyną blokadą jest niemergowanie.
2. **Co powstanie:** nowy plik `/root/artifacts/shadow-review/suwak_autonomii.jsonl` (pierwsza linia szeregu)
   oraz w katalogu dnia `suwak_autonomii.json`, `SUWAK_AUTONOMII.md`, `suwak_autonomii.log`; wiersz
   `suwak_autonomii` dojdzie do `SUMMARY.md`/`summary.json`. Wszystko wewnątrz `ReadWritePaths` — sandbox
   systemd nie jest ruszany.
3. **Koszt biegu:** +~2 s do dobowego przeglądu (pomiar 05.08: 1,7 s przy 176 MB wejść), RSS mały dzięki
   projekcji pól. `timeout` czytelnika = domyślne 900 s.
4. **Ryzyko wywalenia joba: zerowe z konstrukcji.** Czytelnik zwraca **zawsze exit 0** — awaria daje rekord
   `DEGRADED` + linię `WARNING` w logu, więc `shadow-review.service` nie przejdzie w `failed` i nie odpali
   `OnFailure=dispatch-onfailure-alert@`. Cena tej decyzji: cicha degradacja jest widoczna **tylko** w
   `SUMMARY.md` (linia werdyktu ma wtedy `SUWAK DEGRADED`) i w polu `status` szeregu — jeśli CTO chce alertu
   na trwałą degradację, to osobna, świadoma decyzja (proponuję: dopiero po 2 dniach `DEGRADED` z rzędu).
5. **Czego oczekiwać w pierwszym rekordzie:** Liczba 1 z okna ograniczonego rotacją (05.08 = 9 dni; po
   nocnej rotacji zwykle o dobę mniej lub więcej), Liczba 2 z pełnego korpusu outcomes; kolektor outcomes
   chodzi 04:40, więc o 05:10 dane doby są już wliczone (`age_hours` < 1 h).
6. **Do zrobienia po merge (nie robiłem — poza zakresem worktree):**
   * **re-seed manifestu nocnego strażnika** (`night_guard --update-manifest`) — zbiór nodeidów rośnie
     o 25, fail-closed strażnik inaczej zaalarmuje;
   * **bramka w ledgerze** — w `OPEN_GATES.md` nie ma dziś żadnego wpisu dla suwaka; jeśli CTO chce
     śledzić „szereg czasowy suwaka żyje / owner dostał 2 liczby", to jest kandydat na `add` + `transition`
     po pierwszym biegu z timera (dowód: pierwsza linia `suwak_autonomii.jsonl` + SHA merge'a).

---

## 8. GRANICE PRACY (czego świadomie NIE zrobiłem)

* Nie uruchamiałem `objm_lexr6_smoke_verdict` (auto-rollback flagi) ani żadnego pisarza cienia.
* Nie dotknąłem `flags.json`, `dispatch_state/`, `scripts/logs/`, żywego `/root/artifacts/shadow-review/`,
  systemd, timerów, Telegrama; nie nadpisałem artefaktów composera z 04.08.
* Nie zmieniłem definicji żadnej metryki suwaka (parytet 0-diff w sekcji 5a).
* Nie zmieniłem zachowania pozostałych czterech czytelników — ich argv i bramka korpusu są nietknięte.
* Merge i ewentualne alertowanie na `DEGRADED` zostawiam CTO/ownerowi.

---

## 9. PLIKI W DOSTAWIE

| Plik | Rola |
|---|---|
| `tools/suwak_autonomii_review.py` | nowy czytelnik (moduł, ~700 linii z raportem MD) |
| `tools/shadow_review_daily.py` | rejestracja: `Reader.out_dir_flag` + wpis `suwak_autonomii` w `READERS` |
| `tests/test_suwak_autonomii_review.py` | 25 testów hermetycznych |
| `eod_drafts/2026-08-05/suwak_reader_297/` | dowody: bieg ręczny (snapshot MD/JSON), treść szeregu (`szereg_przyklad.md`), parytet z composerem, e2e przez harness, surowa regresja |
| `RAPORT_AGENTA.md` | ten raport |

⚠ Same pliki `.jsonl`/`.log` biegów zostały w worktree, ale **nie w commicie** — wyklucza je
`.gitignore:47-48` (`eod_drafts/**/*.jsonl`, `eod_drafts/**/*.log`). Ich treść jest w repo jako
`szereg_przyklad.md`, żeby format wyjścia był weryfikowalny bez uruchamiania czytelnika.

**Commity (gałąź `wt/suwak-reader-297-cto-20260805`, base `06e4d5c39`):**

* **`5bd5839f898e6f1ad35888ca35fb7616173c093a`** — DOSTAWA: czytelnik + rejestracja w harnessie
  + 25 testów + dowody. To jest commit do recenzji i merge'u.
* HEAD gałęzi — ten raport + `BRIEF_297.md` (dokumentacja, zero kodu; SHA celowo nie wpisany w
  treść, bo commit nie może zawierać własnego skrótu — `git log -1 --format=%H`).
