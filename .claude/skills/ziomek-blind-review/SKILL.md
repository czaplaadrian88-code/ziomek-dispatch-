---
name: ziomek-blind-review
description: Niezależna, ślepa recenzja kandydata (skill, patch, brama) przed promocją. Użyj, gdy trzeba wystawić status INDEPENDENT, zweryfikować cudzy artefakt bez confirmation bias, sprawdzić kandydata na skill, uruchomić blind review, zablindować bundle recenzenta albo potwierdzić, że autowalidacja to nie niezależny przegląd.
---

# ziomek-blind-review

Autor **strukturalnie nie może** wystawić sobie statusu INDEPENDENT — niezależność
to nie wiedza, to świeży kontekst bez jego wniosków. To jedyna zdolność, której
`/root/.codex/AGENTS.md` z definicji nie zapewni: instrukcja globalna jest w
kontekście autora, więc nie jest od niego niezależna.

Ten skill mechanizuje to, co w audycie 2026-07-17 znalazło CRITICAL w 3185-liniowej
bramie, która sama o sobie meldowała „264/264, zero przeżyło": **oddaj artefakt
świeżemu recenzentowi, który nie widział twojego raportu.**

Ścieżki względne wobec `dispatch_v2/`. Driver:
`.claude/skills/ziomek-blind-review/driver.py`.

## Kiedy używać

- przed promocją / merge / aktywacją dowolnego kandydata (skill, patch bramy,
  zmiana kanonu), gdy kontrakt wymaga statusu `INDEPENDENT`;
- gdy jedyny „dowód" to autowalidacja autora (`AUTHOR_STATIC_ORACLE`);
- gdy podejrzewasz confirmation bias — autor polerował własny artefakt N cykli.

## Kiedy NIE używać

- do zatwierdzenia CZEGOŚ, co sam napisałeś, jako „niezależne" — to sprzeczność;
- jako zamiennik pełnej regresji / hermetycznych testów (to osobne bramy);
- do nadania authority — review nie promuje, tylko orzeka.

## Proces (3 kroki, driver robi 1 i 3)

```
python3 .claude/skills/ziomek-blind-review/driver.py blind <katalog_kandydata> [--pin pin.json] [--out DIR] [--allow-sensitive ŚCIEŻKA]
```
Skanuje CAŁY zakres bramką PII/sekretów (fail-closed — patrz niżej), weryfikuje
SHA-256 wejścia (fail-closed przy mismatch i przy częściowym pinie), buduje
**ślepy bundle** — kopiuje
artefakty kandydata, a **wycina** raport autora, handoffy, git-log i wszystko
z nazwą niosącą werdykt (`report`, `audit`, `handoff`, `_plan`, …).
Filtr ocenia osobno nazwę pliku i każdy katalog. Dokładna kanoniczna ścieżka
`.claude/skills/ziomek-blind-review/` jest recenzowalna, żeby skill nie wycinał
własnego kodu; wyjątek nie obejmuje nazwy pliku ani dalszych katalogów, więc
`AUTHOR_REPORT.md` i `author-review/x.py` nadal są bezwarunkowo wycinane.
Źródła shell (`.sh`) są kopiowalne, ponieważ ta sama kanoniczna polityka skanuje
ich pełną treść przed kopiowaniem; błąd UTF-8, NUL, limit lub błąd odczytu
zatrzymuje cały bundle jako `unscannable`, zamiast cicho pominąć oracle.
Obok zapisuje schema-v2 manifest z SHA-256 każdego pliku i agregatem całego
bundla. Wypisuje ścieżkę bundla + gotowy prompt recenzenta.

```
python3 .claude/skills/ziomek-blind-review/driver.py verify <bundle.manifest.json>
```
To obowiązkowy pierwszy krok recenzenta. Weryfikuje dokładny zestaw ścieżek,
każdy digest i agregat; dodatkowy/usunięty/zmieniony bajt daje `HOLD`.
Driver ustawia `dont_write_bytecode` przed importem lokalnej polityki, a gotowy
prompt używa dodatkowo `python3 -B`: verifier uruchomiony z wnętrza bundla nie
może sam dopisać `__pycache__` i unieważnić obiektu, który właśnie sprawdza.

**Krok 2 — człowiek/orkiestrator:** oddaj bundle i prompt **ŚWIEŻEMU subagentowi**
(`Agent`, osobny kontekst), który NIE ma dostępu do twoich wniosków, raportu
autora ani pamięci projektu. Recenzent zwraca JSON:
`{"disposition": "CONFIRMED_DEFECT|CLEAN", "findings": [{"file","line","reproduction"}]}`.
Dla artefaktu wysokiego ryzyka użyj ≥2 recenzentów o różnym kącie (polityka /
fałszywy dowód / kontrola fałszywie-pozytywna).

```
python3 .claude/skills/ziomek-blind-review/driver.py check <verdict.json>
```
Odrzuca werdykt bez `file`+`line`+`reproduction` i dyspozycję spoza zbioru —
„wygląda ok" nie przechodzi.

## Uruchomienie (zweryfikowane w tej sesji)

```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2/.claude/skills/ziomek-blind-review
python3 driver.py eval          # spójność korpusu: 3 fixtures OK
python3 driver.py blind fixtures/case-critical-policy-inversion --out /root/worktrees/blind-demo
python3 driver.py verify /root/worktrees/blind-demo.manifest.json
python3 driver.py check /root/worktrees/blind-demo-verdict.json
```

## 🔒 Bramka PII/sekretów — FAIL-CLOSED (od 2026-08-02)

Powód: **near-miss 2026-08-01** — `blind .` na korzeniu repo objął chroniony plik
klasy PII (pełne nazwiska kurierów) i skopiował go do bundla recenzenta. Recenzent
go nie otworzył, bundle skasowano, ale bramka nie zadziałała, bo denylista znała
tylko nazwy niosące **werdykt**, nie **dane osobowe**.

Kanoniczna polityka: **`pii_denylist.py`** — jedyne miejsce, gdzie definiuje się
wrażliwość oraz zbiór kopiowalnych rozszerzeń (`BUNDLE_COPYABLE_SUFFIXES`: `.md`,
`.json`, `.yaml`, `.yml`, `.py`, `.sh`, `.schema.json`, `.txt`). Driver woła
`screen_tree()` **raz, przed jakimkolwiek zapisem**, a potem używa predykatu
`is_bundle_copyable()` z tego samego modułu; nie ma własnej listy rozszerzeń ani
drugiej warstwy filtrów w manifeście.

| warstwa | co łapie | czego NIE łapie |
|---|---|---|
| `path` — globy/katalogi/tokeny nazw | `*.env`, `*.pem`, `*.key`, `secrets/`, `credential*`, `*full_names*`, `courier_names`, `pesel`, `telefon`; katalogi danych (`identity/`, `daily_accounting/`, `grafik/`) **tylko dla plików danych** — kod o PII zostaje recenzowalny | pliku nazwanego neutralnie (`dane.json`) |
| `scope` — ucieczka zakresu | dowiązanie (pliku lub katalogu) wskazujące POZA katalog kandydata — klasyczne przemycenie pliku, którego skan kandydata by nie objął. **Nieallowlistowalne**: zmaterializuj plik w katalogu kandydata albo zawęź zakres | — |
| `content` — heurystyki treści | materiał w kształcie sekretu w skanowalnym tekście; w plikach strukturalnych (`json/jsonl/csv/tsv/yaml`) także pola osobowe i klienta oraz ≥3 różne wartości w kształcie nazwiska/numeru telefonu | osobowych heurystyk w kopiowalnym kodzie `.py` i prozie `.md`/`.txt`; nazwisk użytych jako klucze struktury; gołego `adres`/`address` |
| `unscannable` — pełność skanu | każdy **kopiowalny** plik, którego pełnej treści nie przeskanowano: `>2 MiB`, NUL, nie-UTF-8, błąd odczytu albo limit parsera wartości/wierszy → odmowa | niekopiowalne binaria/archiwa nie trafiają do bundla; ich treść pozostaje niepotwierdzona |

Zachowanie: każde trafienie (także `unscannable`) = **ODMOWA budowy bundla
(exit 3)**, nie cichy skip pliku —
i przy odmowie na dysku nie powstaje ani jeden bajt bundla. Komunikat odmowy
**nigdy nie cytuje dopasowanej treści** (klasa + reguła + licznik), więc wolno go
wkleić do raportu. Zdjęcie klasyfikacji: `--allow-sensitive <ścieżka_względna>`,
osobno dla KAŻDEGO pliku. Dopasowanie jest dokładne: literówka, katalog i wzorzec
glob = odmowa, żeby allowlista nie udawała przejrzanego pliku.

```
python3 .claude/skills/ziomek-blind-review/driver.py screen <katalog>   # sam skan, nic nie tworzy
```
Właściwą reakcją na odmowę jest **ZAWĘŻENIE zakresu** (`blind .` na korzeniu repo
to prawie zawsze błąd), a nie hurtowe allowlistowanie.

### Jawny dług po review 2026-08-02

- **N1 — ratchet pojedynczych rodzin reguł:** obecny oracle czerwieni pakietowe
  osłabienia, ale nie ma osobnego mutanta/wabika dla każdej z rodzin
  `SECRET_DIRS`, `PII_NAME_TOKENS_ANY`, `PII_NAME_TOKENS_DATA`, `PHONE_VALUE_RE`
  i pełnego strażnika NUL. Do domknięcia osobnym zakresem; nie blokuje W6.
- **N3 — nazwa pola manifestu:** `excluded_carrying_verdict` obejmuje zarówno
  pliki wycięte jako werdykt, jak i niekopiowalne po typie. Zachowanie jest
  poprawne, ale nazwa myląca; zmiana wymaga wersjonowania/kompatybilności
  konsumentów manifestu i pozostaje jawnym długiem.

## Oracle — korpus `fixtures/` (nie autorski, potwierdzony)

Eval nie jest autorski jak w bramie, którą zastępuje. Trzy fixtures to **realne,
potwierdzone wady** z audytu 2026-07-17, plus kontrola fałszywie-pozytywna:

| fixture | oczekiwane | mapuje na |
|---|---|---|
| `case-critical-policy-inversion` | CONFIRMED_DEFECT | CRITICAL-1: „ETAP 8 — deploy bez ACK" przechodzi bramę |
| `case-high-false-green` | CONFIRMED_DEFECT | CRITICAL-2: „264/264 zabitych = bezpieczne" (metryka ≠ bezpieczeństwo) |
| `case-clean-baseline` | CLEAN | reviewer NIE MOŻE halucynować wady na czystym |

**READY skilla:** ≥2 niezależnych recenzentów łapie oba defekty (2/2) **i** zwraca
CLEAN na baseline (0 fałszywych). **HOLD:** cokolwiek poniżej — bo wtedy proces nie
wykryłby tego, co człowiek wykrył w godzinę. Wynik żywej ewaluacji tej sesji:
patrz `fixtures/EVAL_RESULT.md`.

## Gotchas

1. **Blindowanie WERDYKTÓW jest denylistą po NAZWIE, nie po treści.** Plik z
   werdyktem nazwany neutralnie (`x.md`) przejdzie do recenzenta. Trzymaj raporty
   autora pod nazwami z `report/audit/handoff/plan` albo poza katalogiem kandydata.
   (Bramka PII to osobna, mocniejsza warstwa: nazwa **oraz** treść — patrz wyżej.)
2. **Bundle NIE zawiera manifestu** — leci obok (`<out>.manifest.json`). Manifest
   zawiera exact path→SHA-256 i agregat, ale nie treść ani wnioski autora.
3. **Recenzent MUSI być świeżym subagentem.** Jeśli „recenzent" to ta sama sesja,
   która czytała raport autora — to nie blind review, to teatr. Driver nie
   wymusi tego za ciebie; to twoja odpowiedzialność orkiestracyjna.
4. **Driver nie jest recenzentem.** Nie ocenia treści — blinduje, pinuje i
   waliduje kształt werdyktu. Ocenę robi model bez twoich wniosków.
5. **`--pin` jest opcjonalny, ale przy promocji obowiązkowy** — bez niego
   recenzujesz bajty, których nikt nie przypiął. Jeśli pin nie obejmuje każdego
   kopiowanego pliku, driver odmawia i nie może wystawić `pin_verified=true`.

## Selftest (egzekwowany co noc)

```bash
.claude/skills/ziomek-blind-review/selftest.sh
python3 .claude/skills/ziomek-blind-review/pii_oracle.py   # sam negatywny oracle + mutanty
```
Sprawdza część mechaniczną oracle: blindowanie wycina werdykty, pin jest
fail-closed, `check` odrzuca mętne werdykty, korpus spójny, a bramka PII odmawia
na syntetycznych wabikach (`pii_oracle.py`: 17 przypadków odmowy + 3 jawne
kontrole granic + sonda limitu JSONL + **mutation ratchet 11/11**). Uruchamia też
realny driver z wnętrza jego własnego bundla i wymaga PASS bez `__pycache__`.
Ratchet osobno czerwieni duplikat
ownera rozszerzeń w driverze, usunięcie reguł path/content/scope, fail-closed,
odmowy `unscannable` (w tym cichą trunkację), dokładności allowlisty per plik,
klasy `client_data` i parserów `csv/tsv/yaml`. **Wpięty w nocną
regresję** (`tests/test_skills_selftest.py`) — regresja zapali ALERT strażnika,
nie zostanie „zademonstrowana raz i zapomniana". Część modelowa oracle (czy
recenzent łapie wady) → `fixtures/EVAL_RESULT.md`, nie ten skrypt.

## Zakres

Read-only wobec projektu. Zero sieci, zero prod-state, zapisy tylko do `--out`;
dla artefaktów pracy ustaw trwały katalog pod `/root/worktrees/`.
Nie promuje, nie aktywuje, nie nadaje authority. Orzeczenie CONFIRMED_DEFECT/CLEAN
to wejście dla właściciela/MAIN, nie zgoda na cokolwiek live.
