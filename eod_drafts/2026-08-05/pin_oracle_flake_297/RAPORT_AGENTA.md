# RAPORT AGENTA — sesja 297 (fix flake oracle PIN KDF)

- **Bramka:** `tests.pin-kdf-oracle-substring-flake` (due 08.08)
- **Gałąź:** `wt/pin-oracle-flake-297-cto-20260805` (base master `06e4d5c39`)
- **Worktree:** `/root/worktrees/dispatch_v2/active/20260805-pin-oracle-flake-297-cto`
- **COMMIT (pełny SHA):** `d09a783eb3dce395f8cafa61c29892557ab15c61`
  (`test(A-6/297): oracle magazynu KDF strukturalny zamiast skanu substringiem…`)
- **Zakres:** WYŁĄCZNIE testy + artefakt dowodowy. **Zero zmian w produkcji**
  (`identity/pin_auth.py`, `gps_server.py`, `gps_rate_limit.py` nietknięte).

---

## 1. Diagnoza (dlaczego to był FALSE POSITIVE, nie defekt produkcji)

Stary `_no_plaintext_oracle` (w pliku bramki) sprawdzał wyciek tak:

```python
blob = json.dumps(store)
for pin in pins:
    assert pin not in blob, "PLAINTEXT PIN w magazynie KDF"
```

Rekord KDF = `{"kdf","salt","iter","hash"}`, gdzie `salt` = 32 znaki hexa, `hash` = 64 znaki
hexa — **losowy ciąg z alfabetu `0-9a-f`**. Prawdopodobieństwo, że 4-cyfrowy PIN wystąpi w nim
jako podciąg: ≈ 2 PIN-y × ~190 pozycji × 16⁻⁴ ≈ **0,58 %** na bieg. Do tego `"iter": 200000`
zawiera na stałe `0000`/`2000`, więc dla PIN-ów o tych cyfrach stary oracle czerwieniłby
**deterministycznie**. Zmierzone empirycznie: **6/1000 biegów = 0,60 %** (sekcja 4).

PBKDF2 jest jednokierunkowy, a sól z definicji jest publiczna — **cyfry PIN-u w hexie wyjścia
KDF nie są wyciekiem**. Fix należy do warstwy testu (oracle mierzył nie to, co deklarował),
a nie do produkcji.

## 2. Diff — co dokładnie zmieniłem

| Plik | Rodzaj | Opis |
|---|---|---|
| `tests/pin_kdf_store_oracle.py` | **NOWY** (181 l.) | Kanoniczny oracle magazynu KDF + katalog `LEAK_FORMS`. **JEDNO ŹRÓDŁO** — importuje go bramka i sweep; zero kopii. |
| `tests/test_a6_security_pin_kdf.py` | zmiana (+60/−19) | Usunięty lokalny oracle ze skanem substringiem → import z modułu wyżej; +1 test anty-flake; +1 test mutacyjny parametryzowany całym katalogiem `LEAK_FORMS`. |
| `eod_drafts/2026-08-05/pin_oracle_flake_297/sweep_oracle_flake.py` | **NOWY** | Dowód anty-flake (≥1000 biegów) + sekcja mutacji; importuje TEN SAM oracle. |
| `eod_drafts/2026-08-05/pin_oracle_flake_297/sweep_1000_output.txt` | **NOWY** | Zapisany wynik biegu 1000×. |
| `BRIEF_297.md` | **NOWY** | Brief CTO (był untracked w worktree — dołączony, żeby merge niósł kontekst). |

### Nowy oracle — asercje strukturalne (A–F)

| # | Warunek | Co domyka |
|---|---|---|
| A | klucz magazynu (tożsamość) nie zawiera PIN-u | PIN doklejony do nazwy kuriera |
| B | rekord ma **dokładnie** pola `{kdf,salt,iter,hash}` | pole-śmietnik z PIN-em; PIN jako **NAZWA** pola |
| C | `kdf == KDF_NAME`, `iter` to `int` ≥ `_MIN_ITER_FLOOR` | podmieniona etykieta KDF, osłabiony koszt |
| D | `salt`/`hash` = **dokładny kształt wyjścia KDF** (czysty lowercase-hex o dokładnej długości 32/64) | PIN zamiast hasha; PIN doklejony do soli/hasha (zmiana długości) |
| E | żadna **wartość** pola nie równa się PIN-owi | plaintext w dowolnym polu |
| F | sole unikatowe per-user **+ bijekcja PIN↔rekord przez `verify_record`** | „hash to jakieś odwracalne zakodowanie PIN-u" — hash musi zgadzać się z niezależnie policzonym PBKDF2(pin, salt, iter) |

Dodatkowo: komunikaty asercji **redagują PIN** (`***`) — także wtedy, gdy oracle czerwieni
właśnie dlatego, że PIN wyciekł do klucza/nazwy pola. Stary kod wypisywał w takiej sytuacji
sekret wprost do logów nocnego strażnika.

Hex soli/hasha **nie jest** skanowany substringiem (to było źródło flake'a); skan substringiem
został tam, gdzie jest deterministyczny — na kluczach magazynu i nazwach pól.

## 3. Mutacje — dowód, że oracle POZOSTAŁ oraclem

Katalog `LEAK_FORMS` (10 form realnego wycieku) parametryzuje
`test_mutation_leak_forms_fail_oracle`; ten sam katalog uruchamia sekcja `--mutations` sweepa.
Wynik (`sweep_1000_output.txt`): **10/10 RED**.

| forma wycieku | nowy oracle | stary skan substringiem | warunek, który czerwieni |
|---|---|---|---|
| `hash_is_pin` | **RED** | RED | D |
| `salt_is_pin` | **RED** | RED | D |
| `extra_field` (`{"pin_plain": PIN}`) | **RED** | RED | B |
| `pin_as_field_name` | **RED** | RED | B |
| `pin_in_store_key` | **RED** | RED | A |
| `pin_appended_to_hash` | **RED** | RED | D |
| `pin_appended_to_salt` | **RED** | RED | D |
| `pin_in_kdf_label` | **RED** | RED | C |
| `pin_hex_encoded_as_hash` | **RED** | **GREEN (przeoczenie)** | F |
| `iter_below_floor` | **RED** | **GREEN (przeoczenie)** | C |

**Nowy oracle jest ściśle silniejszy od starego:** łapie dwie formy, które skan substringiem
przepuszczał (PIN zakodowany hexem + osłabiony koszt KDF). Kontrola negatywna jest w tym samym
teście: przed każdą mutacją czysty magazyn KDF **przechodzi**.
Zachowany bez zmian: `test_mutation_plaintext_store_fails_oracle` (mutacja `make_record` → plaintext).

## 4. Dowód anty-flake

**(a) Deterministyczna repro w suicie** — `test_oracle_stable_when_kdf_hex_contains_pin_digits`:
wymusza sól `0123456789abcdef…`, której hex **zawiera** PIN `1234`, i asertuje jednocześnie
`pin in json.dumps(store)` (czyli stary skan **byłby RED**) oraz `verify_record(...) is True`
i PASS nowego oracle. Kształt flake'a jest odtąd przybity testem, nie tylko opisem.

**(b) Sweep 1000 biegów** (losowe PIN-y 4–6 cyfr, 2 kurierów/bieg, seed 297, prawdziwy PBKDF2 200k):

```
runs=1000 couriers/run=2 seed=297 czas=259.8s
STARY oracle (skan substringiem)  : 6 czerwieni (0.60% biegów)
NOWY oracle (strukturalny)        : 0 czerwieni (0.00% biegów)
MUTACJE: 10/10 RED → OK
WERDYKT: OK — 0 fałszywych czerwieni + oracle nadal łapie każdy wyciek
```

Zmierzone 0,60 % pokrywa się z obserwacją z memory (~0,56 %) i z rachunkiem teoretycznym —
to potwierdza, że trafiłem we właściwą przyczynę, a nie w objaw.

## 5. Regresja (pkgroot, `/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q`)

| bieg | stan | wynik |
|---|---|---|
| baseline PRZED | HEAD `06e4d5c39` (czysty eksport `git archive`, układ pkgroot 1:1) | **0 failed**, 7529 passed, 24 skipped, 8 xfailed (801 s) |
| po zmianie (final, dokładnie zacommitowane drzewo) | `d09a783eb` | **0 failed**, 7540 passed, 24 skipped, 8 xfailed (739 s) |

**Delta = +11 nodeidów i nic więcej** (1 test anty-flake + 10 parametrów mutacji);
18 istniejących nodeidów pliku bramki zachowanych bez zmiany nazw. Sam plik bramki:
`29 passed`.

Nowe nodeidy:
```
tests/test_a6_security_pin_kdf.py::test_oracle_stable_when_kdf_hex_contains_pin_digits
tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle[extra_field]
tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle[hash_is_pin]
tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle[iter_below_floor]
tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle[pin_appended_to_hash]
tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle[pin_appended_to_salt]
tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle[pin_as_field_name]
tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle[pin_hex_encoded_as_hash]
tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle[pin_in_kdf_label]
tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle[pin_in_store_key]
tests/test_a6_security_pin_kdf.py::test_mutation_leak_forms_fail_oracle[salt_is_pin]
```

## 6. ⚠ Dla CTO przed/po merge — trzy rzeczy

1. **Manifest nocnego strażnika wymaga re-seedu.** +11 nodeidów → `evaluate_suite_contract`
   zwróci `SUITE-CONTRACT-UNEXPECTED(11)` (fail-closed). Manifest v46 (7557 nodeidów, base
   `e81cb079…`) → po merge `night_guard.py --update-manifest`, zgodnie z konwencją
   `chore(night-guard): re-seed manifestu` z `git log`. Żadnego nodeida nie usunąłem ani nie
   przemianowałem, więc alertów `MISSING` nie będzie.

2. **Komenda regresji z briefu wymaga `ZIOMEK_SCRIPTS_ROOT`.** Uruchomiona bez niej
   (`cd pkgroot/<slug>/dispatch_v2 && pytest tests/ -q`) suita importuje pakiet `dispatch_v2`
   z **głównego repo** `/root/.openclaw/workspace/scripts` (`conftest.py:36`, default
   `_SCRIPTS_ROOT`), a nie z worktree — testuje więc nie ten kod, co trzeba, i daje 23 fałszywe
   `FAILED` (`SkipTest: moduł nie istnieje: …/active/dispatch_v2/tools/…`). Poprawnie:
   ```
   PK=/root/worktrees/dispatch_v2/pkgroot/20260805-pin-oracle-flake-297-cto
   cd $PK/dispatch_v2 && ZIOMEK_SCRIPTS_ROOT=$PK /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q
   ```
   Warto poprawić tę komendę w kolejnych briefach — to systemowa pułapka worktree, nie
   jednorazowa.

3. **Znaleziony DRUGI flake tej samej rodziny (poza zakresem bramki, nie ruszałem).**
   `tests/test_proposal_format_v2.py::test_v2_pickup_label_fallback_no_best_eta` czerwieni się
   zależnie od zegara: fixture ustawia `pickup_ready_at = now(UTC)+15 min`, a test asertuje, że
   render **nie** pokazuje literału `11:00`. W minucie 08:45 UTC (= 10:45 Europe/Warsaw)
   fallback renderuje dokładnie `11:00` → fałszywa czerwień. Trafiłem na to w jednym z biegów
   (08:45 UTC); ten sam plik po biegu: `30 passed`, a final regresja: 0 failed. Kandydat na
   osobną bramkę (fix: zamrożony zegar/`freezegun`-style monkeypatch zamiast `datetime.now`).

## 7. Zgodność z zasadami repo

- **Fix u źródła, nie łata:** przyczyną była metoda pomiaru w oracle (skan całego blobu), nie
  kosmetyka wyniku; nie dodano żadnego `if`, wyjątku od reguły ani tolerancji „ignoruj gdy
  losowo trafi". Oracle ma teraz **jednego kanonicznego ownera** (`tests/pin_kdf_store_oracle.py`),
  importowanego przez oba miejsca użycia — bez duplikatu w sweepie.
- **Zakaz osłabiania oracle:** udowodnione mutacjami 10/10 RED + dwie formy, których stara
  wersja nie łapała.
- **HERMETIC-GUARD:** testy wyłącznie w `tmp_path`. Sweep uruchamiany gołym pythonem **nie**
  importuje `gps_server` (ten przy imporcie otwiera żywy `logs/gps_server.log`) — importuje tylko
  `identity/pin_auth` + moduł oracle (stdlib), a katalogi robocze tworzy **obok siebie w
  worktree**, nigdy w `/tmp` ani w żywym `dispatch_state`. `resolve_pin` wołany z jawnym
  `use_kdf=True`, więc `flags.json` nie jest czytany.
- **PIN-y:** wyłącznie syntetyczne; oracle redaguje je w komunikatach; żywy `kurier_piny.json`
  nietknięty.
- **Commit:** jawny pathspec (5 ścieżek), bez `git add -A`, na gałęzi sesji; merge = CTO.
