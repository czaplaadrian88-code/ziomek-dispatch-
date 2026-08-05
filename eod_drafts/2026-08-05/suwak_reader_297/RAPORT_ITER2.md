# RAPORT ITER2 (sesja 297) — suwak-czytelnik: 5 findingów blind zamkniętych

**Gałąź:** `wt/suwak-reader-297-cto-20260805` (worktree `active/20260805-suwak-reader-297-cto`)
**Baza iteracji:** `5bd5839f8` (kandydat iter1) + `61ce4462d` (RAPORT_AGENTA)
**Werdykt wejściowy:** `/root/artifacts/blind-297/suwak/verdict.json` — `CONFIRMED_DEFECT`, 5 findingów
(F1/F2 HIGH, F3/F4 MED, F5 LOW); granice read-only / append-only / harness / fail-soft POTWIERDZONE.
**Commit iter2:** `daab5c91907bd0c33bf62e2d5d6e50e248e824cf`
**Pliki:** `tools/suwak_autonomii_review.py`, `tests/test_suwak_autonomii_review.py` (⛔ ledger nietknięty — CTO)

---

## 0. Zmiana kontraktu wewnętrznego (jedno źródło prawdy o bazie)

`compute_liczba1(paths, max_age_h)` → **`compute_liczba1(base, max_age_h)`**.

Rotacje dobierane są TERAZ wewnątrz funkcji (`discover_decision_files(base)`), więc wołający nie
może już sparować ścieżki bazowej z cudzą listą plików, a „plik żywy” przestał być pozycją w liście
(F2 brał się dokładnie z tego rozjazdu). To jedyny publiczny sygnał modułu, który zmienił kształt;
`run()` i bramka testowa są jedynymi konsumentami (`grep` po repo: brak innych wywołań).

---

## 1. F1 (HIGH) — glob rotacji wpuszczał `<baza>.<cokolwiek>.<N>` do mianownika

**Fix (u źródła, whitelist):** `_ROTATION_RE` (blacklista `\.(\d+)(\.gz)?$` przez `.search()` na
CAŁEJ ścieżce) zastąpiony funkcją `rotation_index(base, path)` z `re.fullmatch` na **samej nazwie
pliku**: `re.escape(basename(base)) + r"\.(\d+)(\.gz)?"`. Korpusem jest wyłącznie `<baza>.<N>`
i `<baza>.<N>.gz`.

**Dowód (oracle PRZED/PO, reprodukcja recenzenta 1:1):** korpus 7 decyzji + `.1` +
`shadow_decisions.jsonl.append.lock.1` z 1 rekordem:

| | pliki wchodzące do korpusu | LICZBA 1 (D) | `oLOCK` w indeksie joinu |
|---|---|---|---|
| PRZED `5bd5839f8` | baza, **`.append.lock.1`**, `.1` | **62,50 % (5/8)** | **True** |
| PO iter2 | baza, `.1` | **57,14 % (4/7)** | False |

Wartości identyczne z tymi, które zmierzył recenzent (62,50 % vs 57,14 %, rozjazd 5,36 pp).

**Ratchet:** `test_rotacje_wykrywane_globem_bez_smieci` — 12 klas sufiksów
(`.append.lock`, `.append.lock.1`, `.bak`, `.bak.1`, `.old.7`, `.save.12.gz`, `.tmp`, `.tmp.3`,
`.1.zst`, `.1.bak`, `-20260804`, `.gz`) MUSI dać pusty wkład, a `.7`/`.7.gz` MUSZĄ zostać rozpoznane.
`test_smieciowy_plik_konczacy_sie_liczba_nie_wchodzi_do_mianownika` — trzy pliki-śmieci z realnymi
rekordami nie ruszają `intake`, `global` ani indeksu (porównanie z wzorcem policzonym bez nich).

---

## 2. F2 (HIGH) — fail-open: brak bazy + świeża rotacja produkowały liczbę

**Fix (u źródła):** `is_base` liczone przez porównanie z **rzeczywistą ścieżką bazową**
(`os.path.realpath(path) == os.path.realpath(base)`), nie przez `path == paths[0]`. `live_seen`
wymaga OBECNEJ i świeżej bazy. Kontrola `live_seen` przeniesiona PRZED kontrolę „brak rekordów”,
bo brak żywej bazy jest przyczyną, a nie skutkiem; komunikat wskazuje ścieżkę bazową.

**Dowód (oracle PRZED/PO):** katalog, w którym istnieje TYLKO `shadow_decisions.jsonl.1`
(mtime = teraz), bazy nie ma:

| | wynik |
|---|---|
| PRZED `5bd5839f8` | `available=True`, LICZBA 1 (D) = **100,00 % (1/1)** — z zatrzymanego strumienia |
| PO iter2 | `available=False`, `reason="brak żywego korpusu bazowego: …/shadow_decisions.jsonl"` |

Komentarz w kodzie (l. 249-253 iter1) deklarował dokładnie to, czego kod NIE robił — teraz robi.

**Ratchety:** `test_brak_zywej_bazy_przy_swiezej_rotacji_nie_daje_liczby` (negatywny oracle +
kontrola pozytywna: ta sama rotacja z OBECNĄ bazą → liczba powstaje) oraz
`test_martwa_baza_nie_ozywa_przez_swieza_rotacje` (baza -40 dni + świeża `.1` → nadal `MARTWY`).

---

## 3. F3 (MED) — linia append-only szeregu twierdziła „OK” o snapshocie, który nie powstał

**Fix (u źródła — kolejność zapisów):** w `run()` najpierw WSZYSTKIE zapisy snapshotu
(`suwak_autonomii.json`, `SUWAK_AUTONOMII.md`), a linia szeregu dopisywana na SAMYM KOŃCU, ze
statusem ustalonym po tych zapisach. Przy awarii: `status=DEGRADED`, wpis w `degraded` z typem
wyjątku i — gdy JSON snapshotu nie powstał — `snapshot=None` (żadnych wskaźników na nieistniejący
plik). Gdy padł wyłącznie raport MD, a JSON istnieje, wskaźnik zostaje, bo jest prawdziwy.

**Dowód (oracle PRZED/PO, reprodukcja recenzenta — PLIK w miejscu katalogu dnia):**

| | linia szeregu |
|---|---|
| PRZED `5bd5839f8` | `status=OK`, `degraded=[]`, `snapshot=…/suwak_autonomii.json`, plik **nie istnieje** |
| PO iter2 | `status=DEGRADED`, `degraded=["snapshot: FileExistsError: …"]`, `snapshot=None` |

**Ratchety:** `test_awaria_snapshotu_nie_wywala_biegu_a_szereg_mowi_prawde` (sprawdza TREŚĆ linii,
nie tylko exit i istnienie pliku — na tym potknęła się poprzednia bramka) oraz
`test_awaria_samego_raportu_md_zostawia_wskaznik_na_istniejacy_snapshot`. Oba czerwienieją po
odwróceniu kolejności zapisów (mutacja = powrót do `append → snapshot`).

**Granica, którą zostawiam jawnie:** pole `status` wewnątrz samego pliku snapshotu JSON powstaje
przed jego zapisem, więc w scenariuszu „JSON zapisany, MD padł” snapshot mówi `OK`, a szereg
`DEGRADED`. Szereg jest źródłem prawdy o kompletności biegu (i tylko on jest append-only) —
snapshot opisuje liczby, które faktycznie policzono. Zmiana tego wymagałaby dwufazowego zapisu
snapshotu i nie należy do zakresu iteracji.

---

## 4. F4 (MED) — join brał OSTATNIĄ, nie pierwszą decyzję zamówienia ⚠ ŚWIADOMA RÓŻNICA METODOLOGII

**Fix (decyzja CTO z briefu — kierunek: poprawność):** indeks joinu budowany po posortowaniu
rekordów po `ts` (`sorted(decisions, key=_ts_order_key)`), więc „pierwsza” znaczy **najwcześniejsza
w czasie**, także gdy decyzje zamówienia leżą po obu stronach granicy rotacji. Rekordy bez `ts`
sortują się na koniec — nie mogą wygrać z rekordem o znanym czasie. Zdanie w raporcie MD dla ownera
poprawione na „**najwcześniejsza w czasie** (najmniejszy `ts`, także gdy leży w innej rotacji)”,
a różnica opisana w docstringu modułu.

**Dowód (oracle PRZED/PO):** `o1` z decyzją pierwotną w `.1` (`ts` 2026-08-04T09:00, D=False,
MANUAL, pool=1) i przekierowaniem w bazie (`ts` 2026-08-05T10:00, D=True, AUTO, pool=9):

| | `idx['o1']` |
|---|---|
| PRZED `5bd5839f8` | `{D: True, auto_route: AUTO, pool: 9}` — decyzja PÓŹNIEJSZA |
| PO iter2 | `{D: False, auto_route: MANUAL, pool: 1}` — decyzja PIERWOTNA |

### ⚠ DO DECYZJI OWNERA (przez CTO): to jest zmiana metodologii wobec composera 04.08

Composer `/root/artifacts/suwak-composer-20260804/suwak_2_liczby.py` ma tę samą wadę (identyczna
kolejność ścieżek i reguła `if oid not in idx`) — recenzent sprawdził to bezpośrednio. Do 04.08
liczby ownera były więc liczone z decyzją PÓŹNIEJSZĄ, przy zdaniu w raporcie, że brana jest
pierwsza. Wybrałem poprawność deklaracji (zdanie mówione ownerowi codziennie ma być prawdziwe),
zgodnie z decyzją CTO w briefie iter2. Skutki:

* **LICZBA 1 i LICZBA 2 się NIE zmieniają** — F4 dotyka wyłącznie indeksu joinu.
* Zmieniają się komórki macierzy „auto-gotowe × zgodność” i pochodne
  (`agree_given_auto_ready_pct`, `auto_ready_d`), czyli bezpośredni test tezy „łatwe = auto”.
  Kierunek zmiany: przekierowania (zwykle łatwiejsze, bo pula już odblokowana) przestają udawać
  decyzję pierwotną, więc odsetek „auto-gotowych” w joinie zwykle SPADNIE.
* Szereg czasowy będzie miał nieciągłość metodologiczną względem jednorazowego raportu z 04.08 —
  tylko w sekcji JOIN, i tylko na zamówieniach z decyzjami po obu stronach rotacji.

**Ratchet:** `test_indeks_bierze_najwczesniejsza_decyzje_takze_miedzy_rotacjami` (dwa pliki rotacji
+ przypadek rekordu bez `ts`).

---

## 5. F5 (LOW) — coverage puli liczyło `bool` jako liczbę kurierów

**Fix:** `coverage["pool_feasible_count"]` liczone przez `pool_bucket(...) != "unknown"` — jedna,
ta sama definicja pokrycia, której używa segmentacja (`isinstance(True, int)` jest prawdą, więc test
na typ liczbowy zaliczał do pokrycia rekordy siedzące w kubełku `unknown`).

**Dowód (oracle PRZED/PO):** korpus z jednym rekordem `pool_feasible_count=True`:
PRZED `coverage=8` vs suma kubełków `7` (niespójne); PO `coverage=7` vs `7` (spójne).

**Ratchet:** `test_pokrycie_puli_nie_liczy_boola_jako_liczby_kurierow` (pokrycie == suma kubełków
`>=3` i `<=2`, wartość policzona ręcznie).

---

## 6. Poza zakresem (świadoma granica fail-soft, bez zmiany)

Obserwacja projektowa recenzenta: **jedna nieczytelna rotacja `.gz` kasuje LICZBĘ 1 całego dnia,
mimo zdrowego pliku bazowego** (wyjątek gzip → `available=False` + `reason`, exit 0, LICZBA 2 nadal
liczona). Zgodnie z briefem NIE zmieniam tego w tej iteracji. Zachowanie jest bezpieczne w kierunku
„brak liczby zamiast liczby fałszywej”, ale kosztowne: uszkodzony plik archiwalny wygasza pomiar
z żywego korpusu. Kandydat na osobną bramkę: liczyć z plików czytelnych + jawnie raportować
w manifeście plik pominięty i zawężone okno.

---

## 7. Parytet vs composer 04.08 PO fixach (dowód, nie deklaracja)

Sonda `parity_probe.py` liczy TRZY implementacje na tym samym korpusie syntetycznym: composer 04.08
(prawda odniesienia), czytelnik PRZED (`5bd5839f8`) i czytelnik PO (iter2). Porównanie rekurencyjne
obejmuje `l1` (global, kubełki, okno, coverage, blockery, orders, intake), indeks joinu, `l2` (full,
overlap, trend tygodniowy, okna) i cały `join`. Composer nigdy nie widzi żywych ścieżek
(`DECISIONS`/`OUTCOMES` nadpisane, asercja przed pierwszym wywołaniem), a `sys.addaudithook`
przerywa bieg przy jakimkolwiek zapisie/mutacji poza katalogiem tymczasowym sondy.

| Korpus | PRZED (różnic) | PO (różnic) | co zostało |
|---|---|---|---|
| **A — czysty** (baza + `.1` + `.2.gz`, bez śmieci) | 2 | **2** | wyłącznie kosmetyka: polskie znaki w polu tekstowym `label` (`pełne` vs `pelne`) |
| **B — ze śmieciami** (`.append.lock.1`, `.bak.1`) | **45** | **2** | te same 2 kosmetyczne; F1 przywraca parytet tam, gdzie composer był ŚLEPY (nie czytał śmieci) |
| **C — zamówienie na granicy rotacji** | 2 | **13** | 3 pola `idx['700']` + 10 pochodnych komórek `join` — **świadoma różnica F4**; LICZBA 1 (62,5 %) i LICZBA 2 identyczne |

Czyli: na korpusie bez plików-śmieci i z obecną bazą wynik czytelnika **== composer** (poza dwoma
polami tekstowymi). F1 zmienia wynik tylko tam, gdzie composer był ślepy; F4 tylko tam, gdzie
composer był błędny — dokładnie tak, jak wymaga DoD.

---

## 8. RED-first — dowód, że bramka czerwieni na `5bd5839f8`

Naprawa F2 zmienia sygnaturę `compute_liczba1` (baza jest argumentem, nie pozycją w liście), więc
sam pytest nie daje czystego RED dla F2/F4/F5 — na starym module te testy wywalają się na
sygnaturze (`IsADirectoryError`, bo stary kod iteruje po znakach ścieżki), a nie na defekcie.
Dlatego dowód jest dwuwarstwowy:

1. **Poziom defektu — `oracle_5_findings.py`** (adapter sygnatury, obie wersje modułu w jednym
   biegu): wszystkie 5 reprodukcji z `verdict.json` odtworzone na PRZED i zamknięte na PO — tabele
   w sekcjach 1-5 wyżej. To jest właściwy dowód RED→GREEN dla wszystkich pięciu findingów.
2. **Poziom bramki — bieg nowego pliku testów przeciwko modułowi `5bd5839f8`**: `15 failed,
   16 passed`, w tym **RED z asercji (nie sygnatury)** dla:
   * `test_rotacje_wykrywane_globem_bez_smieci` — `AssertionError: [... '.tmp.3', ...] == [... '.10']` (F1),
   * `test_awaria_snapshotu_nie_wywala_biegu_a_szereg_mowi_prawde` — `AssertionError: 'OK' == 'DEGRADED'` (F3),
   * `test_awaria_samego_raportu_md_zostawia_wskaznik_na_istniejacy_snapshot` — `AssertionError: 'OK' == 'DEGRADED'` (F3).

   Pozostałe czerwienieją na sygnaturze — to skutek naprawy F2 u źródła, a nie ukryty zielony.
   Po fiksach: **44 passed** (31 suwak + 13 harness), 0 failed.

**Artefakty biegów (poza repo, trwale): `/root/artifacts/suwak-iter2-297/`** — `oracle_5_findings.py`
+ `oracle_out.txt` (RED→GREEN 5 findingów), `parity_probe.py` + `parity_out.txt` (parytet 3
implementacji), `test_RED_na_5bd5839f8.py` + `suwak_PRZED.py` (bramka odpalona przeciw modułowi
sprzed fixu), `baseline_iter2.txt` / `regresja_iter2.txt` (pełne biegi), `nodeids_PRZED.txt` /
`nodeids_PO.txt` (listy do delty). Obie sondy są odtwarzalne bez repo:
`/root/.openclaw/venvs/dispatch/bin/python /root/artifacts/suwak-iter2-297/oracle_5_findings.py`.

---

## 9. Bramka i regresja

| | baseline (`61ce4462d`, PRZED edycją) | po iter2 |
|---|---|---|
| pełna regresja (pkgroot + `ZIOMEK_SCRIPTS_ROOT`) | 7554 passed, 24 skipped, 8 xfailed, **0 failed** (788 s) | **7560 passed**, 24 skipped, 8 xfailed, **0 failed** (899 s) |
| nodeidy zebrane | 7582 | **7588** (+6 netto) |
| `test_suwak_autonomii_review.py` + `test_shadow_review_daily.py` | 38 passed | **44 passed** (31 + 13) |

Komenda (obie strony identyczna):

```
PK=/root/worktrees/dispatch_v2/pkgroot/20260805-suwak-reader-297-cto
cd $PK/dispatch_v2 && ZIOMEK_SCRIPTS_ROOT=$PK /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q
```

### Delta nodeidów — WYŁĄCZNIE moje (7582 → 7588, netto +6: 7 nowych, 1 przemianowany)

```
- tests/test_suwak_autonomii_review.py::test_awaria_snapshotu_nie_wywala_biegu          (przemianowany)
+ tests/test_suwak_autonomii_review.py::test_awaria_snapshotu_nie_wywala_biegu_a_szereg_mowi_prawde
+ tests/test_suwak_autonomii_review.py::test_awaria_samego_raportu_md_zostawia_wskaznik_na_istniejacy_snapshot
+ tests/test_suwak_autonomii_review.py::test_brak_zywej_bazy_przy_swiezej_rotacji_nie_daje_liczby
+ tests/test_suwak_autonomii_review.py::test_martwa_baza_nie_ozywa_przez_swieza_rotacje
+ tests/test_suwak_autonomii_review.py::test_indeks_bierze_najwczesniejsza_decyzje_takze_miedzy_rotacjami
+ tests/test_suwak_autonomii_review.py::test_pokrycie_puli_nie_liczy_boola_jako_liczby_kurierow
+ tests/test_suwak_autonomii_review.py::test_smieciowy_plik_konczacy_sie_liczba_nie_wchodzi_do_mianownika
```

Zero zmian w nodeidach poza `tests/test_suwak_autonomii_review.py` (`diff` list zebranych
nodeidów PRZED/PO w całości powyżej). Żaden istniejący test nie został usunięty ani osłabiony —
jedyne modyfikacje istniejących: dopasowanie wywołań do nowej sygnatury `compute_liczba1(base, …)`
oraz wzmocnienie czterech testów wskazanych w `gate_blind_spots`.

Manifest nocnego strażnika (7557 nodeidów na `06e4d5c39`) NIE był ruszany — re-seed po merge należy
do CTO, tak jak przy bramkach G5/G6.

---

## 10. Czego iteracja NIE zmienia

* Granice potwierdzone przez recenzenta jako czyste (read-only, zero sieci, append-only szeregu,
  argv czterech istniejących czytelników w harnessie, fail-soft biegu zbiorczego) — bez zmian;
  ratchety `test_ratchet_zrodlo_nie_ma_operacji_kasujacych_i_nadpisujacych_szereg`,
  `test_bieg_nie_dotyka_zrodel_ani_nie_pisze_poza_wyjsciem`,
  `test_czytelnik_jest_zarejestrowany_w_shadow_review_daily` przechodzą bez modyfikacji.
* Ledger procesowy (`process_debt_gate.py`) — nietknięty, zgodnie z briefem.
* Flagi, systemd, żywy stan — zero zmian. Czytelnik nadal aktywuje się sam przy pierwszym tiku
  `shadow-review.timer` po merge (05:10 UTC), bez flagi i bez restartu.
