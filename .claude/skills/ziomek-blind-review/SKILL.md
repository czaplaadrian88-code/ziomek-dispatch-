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
python3 .claude/skills/ziomek-blind-review/driver.py blind <katalog_kandydata> [--pin pin.json] [--out DIR]
```
Weryfikuje SHA-256 wejścia (fail-closed przy mismatch), buduje **ślepy bundle**
— kopiuje artefakty kandydata, a **wycina** raport autora, handoffy, git-log i
wszystko z nazwą niosącą werdykt (`report`, `audit`, `handoff`, `_plan`, …).
Wypisuje ścieżkę bundla + gotowy prompt recenzenta.

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
python3 driver.py blind fixtures/case-critical-policy-inversion --out /tmp/b   # bundle=[SKILL.md], AUTHOR_REPORT.md WYCIĘTY
python3 driver.py check /tmp/verdict.json                                       # OK / HOLD
```

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

1. **Blindowanie jest denylistą po NAZWIE, nie po treści.** Plik z werdyktem
   nazwany neutralnie (`x.md`) przejdzie do recenzenta. Trzymaj raporty autora
   pod nazwami z `report/audit/handoff/plan` albo poza katalogiem kandydata.
2. **Bundle NIE zawiera manifestu** — leci obok (`<out>.manifest.json`), żeby
   nawet nazwa wyciętego pliku nie sugerowała recenzentowi, czego szukać.
3. **Recenzent MUSI być świeżym subagentem.** Jeśli „recenzent" to ta sama sesja,
   która czytała raport autora — to nie blind review, to teatr. Driver nie
   wymusi tego za ciebie; to twoja odpowiedzialność orkiestracyjna.
4. **Driver nie jest recenzentem.** Nie ocenia treści — blinduje, pinuje i
   waliduje kształt werdyktu. Ocenę robi model bez twoich wniosków.
5. **`--pin` jest opcjonalny, ale przy promocji obowiązkowy** — bez niego
   recenzujesz bajty, których nikt nie przypiął (dokładnie luka HIGH-1 z audytu).

## Selftest (egzekwowany co noc)

```bash
.claude/skills/ziomek-blind-review/selftest.sh   # 8/8 PASS
```
Sprawdza część mechaniczną oracle: blindowanie wycina werdykty, pin jest
fail-closed, `check` odrzuca mętne werdykty, korpus spójny. **Wpięty w nocną
regresję** (`tests/test_skills_selftest.py`) — regresja zapali ALERT strażnika,
nie zostanie „zademonstrowana raz i zapomniana". Część modelowa oracle (czy
recenzent łapie wady) → `fixtures/EVAL_RESULT.md`, nie ten skrypt.

## Zakres

Read-only. Zero sieci, zero prod-state, zapisy tylko do `--out` (tmp domyślnie).
Nie promuje, nie aktywuje, nie nadaje authority. Orzeczenie CONFIRMED_DEFECT/CLEAN
to wejście dla właściciela/MAIN, nie zgoda na cokolwiek live.
