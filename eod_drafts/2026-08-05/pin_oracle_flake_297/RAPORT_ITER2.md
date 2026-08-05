# RAPORT ITER2 (sesja 297) — oracle PIN KDF po blind CONFIRMED_DEFECT

- **Gałąź:** `wt/pin-oracle-flake-297-cto-20260805` (worktree `20260805-pin-oracle-flake-297-cto`)
- **Commit kodu (pełny SHA):** `45b4b0572c06b8997653b10d413c6aed7d6476a1`
- **Baza iteracji:** `767408ff4` (docs iter1) na `d09a783eb` (kandydat iter1) na `06e4d5c39` (master)
- **Werdykt wejściowy:** `/root/artifacts/blind-297/pinflake/verdict.json` — CONFIRMED_DEFECT (F1 CRITICAL, F2 HIGH, F3 MEDIUM)
- **Ledger:** NIETKNIĘTY (`process_debt_gate` = CTO, zgodnie z briefem).

---

## 1. Co było źle i dlaczego (przyczyna źródłowa, nie objaw)

Iteracja 1 usunęła flake, ale zrobiła to **wycinając skan dosłowny** i zostawiając same asercje
kształtu. Skutek: `salt` i `iter` stały się polami **SWOBODNYMI** — jedynym ograniczeniem treści
soli była długość + alfabet hex, a cyfry PIN-u SĄ znakami hex. Mutant produkcji „sól wyprowadzona
z sekretu" zapisywał PIN czytelnie do `kurier_piny_kdf.json`, zachowywał prawdziwy PBKDF2 (warunek
(F) zielony) i przechodził A–F na **zielono**, podczas gdy oracle mastera był tam **RED**.

**Przyczyną flake'a była DŁUGOŚĆ PIN-u, a nie idea skanu dosłownego.** 4-cyfrowy PIN produkcyjny
(`identity/schema.PIN_LENGTH`) trafia przypadkiem w 96 znaków losowego hexa na rekord z p ≈ 0,6 %
na bieg. Fix iteracji 1 zaatakował więc niewłaściwą warstwę (usunął asercję zamiast usunąć kolizję),
co jest dokładnie wzorcem zakazanym przez „naprawę u źródła".

## 2. Co zmienione (3 pliki kodu/dowodu, ZERO produkcji)

### `tests/pin_kdf_store_oracle.py` (kanoniczny właściciel oracle)
| Zmiana | Zamyka |
|---|---|
| **Kontrakt długości PIN-u** `MIN_ORACLE_PIN_LEN = 12`, egzekwowany **FAIL-CLOSED** (krótszy PIN = błąd bramki, nigdy cichy słabszy tryb) | warunek konieczny dla (G) bez flake'a |
| **(G) SKAN DOSŁOWNY całego blobu wraca** — w formie dosłownej, **odwróconej** i **hex-ASCII**, plus fragmenty PIN-u ≥ `BLOB_FRAGMENT_MIN_LEN` (10 zn.) | **F1** (`pin_prefix/middle/suffix/hexencoded/reversed_in_salt`) |
| **(A) klucze magazynu** skanowane dodatkowo od fragmentu ≥ `KEY_FRAGMENT_MIN_LEN` (4 zn.) — klucze to nazwy (dane deterministyczne), więc skan fragmentów tu nie flakuje | **F1** (`pin_split_salt_and_key`) |
| **(C) `iter` przypięty do kontraktowego `pin_auth.PBKDF2_ITERATIONS`** (zamiast samego floora) + osobna asercja, że sama stała produkcji nie zeszła poniżej `_MIN_ITER_FLOOR` | **F2** (`iter = 200000 + int(pin)` → RED) |
| **`LEAK_FORMS` 10 → 17 form**; formy zachowujące kształt **przeliczają hash** (`_rehash`), więc czerwienią się przez SWOJĄ asercję, a nie przypadkiem przez bijekcję (F) | **F3** (ratchet mutacyjny realnie dyskryminuje) |
| **Nowa sonda produkcyjna `assert_salt_not_derived_from_secret`** — ta sama entropia + różne PIN-y muszą dać tę samą sól (i ten sam `iter`) | **F1** u źródła: sól jest polem wolnym, więc z samego artefaktu nie da się wykluczyć każdego kodowania |
| **Docstring przepisany**: sekcja „CO ORACLE EGZEKWUJE (A)-(G)" **oraz** „CZEGO ORACLE NIE ZAMYKA" | **F3** (dokumentacja nie obiecuje więcej niż egzekwuje) |
| `_redact` redaguje teraz **wszystkie formy powierzchniowe i fragmenty ≥ 4 zn.** | komunikat asercji nie wynosi PIN-u do logów strażnika (potwierdzone: `'Marcin By #***'`) |

### `tests/test_a6_security_pin_kdf.py` (bramka)
- Testy oracle przeszły na **syntetyczne PIN-y 12-cyfrowe** (`PIN_A`/`PIN_B`); testy **ścieżki
  produkcyjnej** (`resolve_pin`, `gps_server`, rate-limit) **zostają na 4-cyfrowym PIN-ie
  produkcyjnym** — realizm zachowany.
- `test_oracle_stable_when_kdf_hex_contains_pin_digits` (anty-flake) przepisany: deterministycznie
  wymuszona sól zawiera **4-cyfrowy fragment** syntetycznego PIN-u — dokładnie kształt starej
  fałszywej czerwieni; oracle iter2 = PASS.
- **+12 nowych nodeidów** (7 nowych form `LEAK_FORMS` + 5 testów):
  `test_oracle_rejects_short_pin_fail_closed`, `test_salt_is_independent_of_pin`,
  `test_mutation_salt_derived_from_pin_fails_independence_probe`,
  `test_mutation_salt_derived_from_pin_fails_store_oracle` (mutacja produkcji **end-to-end** przez
  `resolve_pin` — 1:1 repro z werdyktu), `test_record_shape_is_pin_length_independent`.

### `eod_drafts/2026-08-05/pin_oracle_flake_297/`
- `sweep_oracle_flake.py` — sweep na PIN-ach 12-cyfrowych + **diagnostyka źródła flake'a** (ten sam
  losowy hex kontra PIN 4-cyfrowy); `probe_reviewer_forms.py` — sondy recenzenta odtworzone w repo
  (`--pin-len 12|4`); 4 pliki wyjść jako dowód.

## 3. Kluczowe rozumowanie: czy 12-cyfrowy PIN syntetyczny łapie formy F1 dla PIN-u produkcyjnego?

**Tak — i to jest dowodzone, nie założone.** Bramka nie testuje „PIN-u", tylko **kod produkcyjny**
(`pin_auth.make_record` / `register_pin` / `resolve_pin`) wywołany na PIN-ie z fixture. `pin_auth`
nie ma **żadnej gałęzi zależnej od długości PIN-u** (`_hash_pin` → `str(pin).encode()`, reszta
traktuje PIN jako nieprzezroczysty tekst) — więc defekt klasy „sól z sekretu", „cyfry PIN-u w
`iter`", „PIN w kluczu", „plaintext w polu" manifestuje się **identycznie** dla 12 i dla 4 cyfr.
Różni się wyłącznie prawdopodobieństwo **PRZYPADKOWEJ** kolizji z hexem — czyli dokładnie to, co
było flakiem. Trzyma to asercją nowy `test_record_shape_is_pin_length_independent` (identyczny
kontrakt rekordu dla PIN-u 4- i 12-cyfrowego + poprawna weryfikacja krzyżowa).

Dwie granice tego rozumowania, wypisane jawnie:
- hipotetyczny defekt **warunkowany długością PIN-u** (`if len(pin) == 4: ...`) nie zostałby złapany;
  dziś taka gałąź w `pin_auth` nie istnieje, a jej dodanie jest widoczne w diffie produkcji;
- kontraktu długości **nie da się obejść podaniem fałszywego długiego PIN-u**: warunek (F) wymaga,
  by każdy podany PIN weryfikował się PBKDF2 dokładnie z jednym rekordem — lista PIN-ów jest
  niepodrabialna.

## 4. Dowody (wszystkie przebiegi własne, w tym worktree)

| DoD | Wynik |
|---|---|
| **1. Sondy recenzenta = RED** | `probe_oracle_strength.py` (plik recenzenta, PIN „1234"): **10/10 RED**, 0 osłabień, 0 przepuszczonych wycieków. ⚠ Uczciwie: przy PIN-ie 4-cyfrowym RED pada już na kontrakcie długości, więc **nie dowodzi siły pojedynczych asercji** — dlatego odtworzyłem te same 10 form na PIN-ie 12-cyfrowym (`probe_reviewer_forms.py --pin-len 12`): **10/10 RED, każda przez SWOJĄ asercję** (kolumna msg: skan dosłowny / odwrócona / hex-ASCII / `iter != kontraktowy` / fragment w kluczu). `probe_fix_direction.py` recenzenta: mutant „sól z PIN-u" **RED** (był GREEN), 300 biegów uczciwej produkcji = 0 czerwieni. |
| **2. Sweep ≥1000 biegów** | 1000 biegów, 2 kurierów/bieg, seed 297, 315,5 s: **pełny oracle A–G = 0 czerwieni (0,00 %)**, **sam skan dosłowny na PIN-ach 12-cyfrowych = 0 czerwieni**. Diagnostyka w tym samym losowym hexie dla PIN-u 4-cyfrowego: **7 czerwieni (0,70 %)** — flake odtworzony i przypisany do długości PIN-u. |
| **3. Mutacje** | **17/17 RED** (katalog `LEAK_FORMS` po rozszerzeniu), test parametryzowany zaktualizowany — 7 nowych nodeidów. Formy zachowujące kształt mają przeliczony hash, więc RED pochodzi z docelowej asercji. |
| **4. Pełna regresja** | pkgroot + `ZIOMEK_SCRIPTS_ROOT`: **7552 passed, 24 skipped, 8 xfailed, 0 failed** (835 s). Baseline PRZED zmianami w tym samym drzewie: **7540 passed, 24 skipped, 8 xfailed, 0 failed** (789 s). **Delta = +12 nodeidów, 0 usuniętych**, wszystkie w `tests/test_a6_security_pin_kdf.py` (7 × `test_mutation_leak_forms_fail_oracle[pin_prefix_of_salt / pin_embedded_in_salt / pin_suffix_of_salt / pin_reversed_in_salt / pin_hexencoded_in_salt / pin_split_salt_and_key / pin_in_iter_value]` + `test_oracle_rejects_short_pin_fail_closed`, `test_salt_is_independent_of_pin`, `test_mutation_salt_derived_from_pin_fails_independence_probe`, `test_mutation_salt_derived_from_pin_fails_store_oracle`, `test_record_shape_is_pin_length_independent`). Sama bramka: 29 → **41 passed** w 8,75 s. |
| **5. Commit** | `45b4b0572c06b8997653b10d413c6aed7d6476a1`, jawny pathspec (8 plików), ta sama gałąź. Ledger nietknięty. |

Komenda bramki: `PK=/root/worktrees/dispatch_v2/pkgroot/20260805-pin-oracle-flake-297-cto; cd $PK/dispatch_v2 && ZIOMEK_SCRIPTS_ROOT=$PK /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_a6_security_pin_kdf.py -q`

## 5. Granice kontraktu

- **#1 ZERO PRODUKCJI** — spełniona: diff dotyka wyłącznie `tests/` i `eod_drafts/`; `identity/`,
  `core/` i pozostałe warstwy nietknięte (`git show --stat 45b4b0572`).
- **#2 ORACLE NIE SŁABSZY OD STAREGO** — spełniona i **ściśle mocniejsza**: każda asercja mastera ma
  swój odpowiednik (`pin not in blob` ⊂ (G); `kdf` ⊂ (C); `iter >= floor` ⊂ mocniejsze (C);
  `len(salt) >= 32` ⊂ (D); unikatowość soli ⊂ (F)), a doszły: formy odwrócona/hex-ASCII, fragmenty,
  równość `iter`, bijekcja PBKDF2, kontrakt pól, sonda niezależności soli. Pomiar: 0 form, na
  których stary jest RED a nowy GREEN (obie serie sond).
- **#3 ANTY-FLAKE** — spełniona: 0/1000 fałszywych czerwieni; bound teoretyczny ≈ 190 × 16⁻¹² ≈
  7 × 10⁻¹³ na bieg dla formy pełnej i ≈ 5 × 10⁻¹⁰ dla fragmentu 10-znakowego.

## 6. Czego oracle NADAL nie zamyka (jawnie, w docstringu modułu też)

1. PIN pocięty na fragmenty **krótsze niż 10 znaków** i rozrzucony po losowym hexie soli/hasha —
   takie fragmenty są statystycznie nieodróżnialne od hexa, a skan na nie oznaczałby powrót flake'a.
   (Forma „pół w kluczu, pół w soli" JEST łapana — przez skan kluczy od 4 znaków.)
2. **Niewymienione odwracalne kodowanie soli** (base64, permutacja, szyfr): sól jest parametrem
   publicznym i wolnym, więc żadna asercja nad samym plikiem tego nie wyklucza. Zamyka to **sonda
   produkcyjna** `assert_salt_not_derived_from_secret` (patrzy na kod, nie na artefakt).
3. Oracle mówi o magazynie KDF `dispatch_v2`; bliźniak `courier_api/auth.py` (apka Android) nadal
   czyta legacy plaintext `{pin: name}` — to **stan sprzed A-6**, udokumentowany w docstringu
   `identity/pin_auth`, poza zakresem tej bramki.

## 7. Do decyzji CTO (nie ruszam)

- **Re-seed manifestu nocnego strażnika** — +12 nodeidów wyzwoli `SUITE-CONTRACT-UNEXPECTED`
  (fail-closed). Zgodnie z historią repo re-seed jest osobnym krokiem PO merge.
- **Ledger** — bramka `tests.pin-kdf-oracle-substring-flake` czeka na przejście stanu przez CTO;
  `process_debt_gate.py` nietknięty.
- **Higiena briefów** — `BRIEF_ITER2_297.md` leży w korzeniu repo jako **niezacommitowany** (recenzent
  zgłosił zacommitowanie `BRIEF_297.md` do korzenia jako uwagę higieniczną; nie powtarzam tego kroku
  ani nie przenoszę cudzego pliku bez decyzji).
