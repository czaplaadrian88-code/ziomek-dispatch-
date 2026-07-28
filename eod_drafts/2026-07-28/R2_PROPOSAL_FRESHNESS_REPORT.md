# R2 — proposal freshness — raport wykonawczy

## Zakres i stan

- Worktree: `/root/worktrees/dispatch_v2/active/20260728-r2-proposal-freshness-sol`
- Branch oczekiwany: `feat/r2-proposal-freshness-20260728`
- Baza przekazana przez ownera: `c18c7a0a8`
- Model / effort: `sol` / `high` — przekrojowy root cause, lifecycle i CAS.
- Produkcja: zero zapisu, zero flipu, zero restartu, zero deployu.
- Git/push: brak; sandbox nie pozwala odczytać metadanych worktree `.git`.

## Root cause i wynik

Propozycję tworzono tylko na `NEW_ORDER`. Kanoniczny assignment przechodzi przez
`panel_watcher._emit_and_apply_state`, ale nie miał writer-a prawdy
assignment-time. R2 dodaje:

1. pre-assignment snapshot aktualnej dispatchowalnej floty i pełny
   `core.decide(..., _bypass_early_bird=True)`;
2. post-assignment CAS dokładnego `assignment_event_id` i lifecycle markera pod
   wspólnym `state_machine.lifecycle_apply_lock`;
3. append-at-most-once `assignment_episode.v1`;
4. niezależny shadow refresh na istniejącym minutowym resweep, bez drugiego
   solve i bez mutacji actionable proposal.

Trwały retry już zatwierdzonego assignmentu jest odrzucany przed solve, więc
nie może stworzyć fałszywego późnego „assignment-time” backfillu.

## Hooki

| Hook | Rola |
|---|---|
| `panel_watcher.py:149` | wspólny chokepoint wszystkich `COURIER_ASSIGNED` |
| `panel_watcher.py:163` | prepare przed durable state apply; fail-safe |
| `durable_event_apply.py:1548` | kanoniczny event/outbox/state |
| `state_machine.py:332` | wspólna cross-process lifecycle lock |
| `state_machine.py:746` | strict CAS read |
| `proposal_freshness.py:195` | świeży assignment-time solve |
| `proposal_freshness.py:235` | exact generation predicate |
| `proposal_freshness.py:252` | locked CAS + append once |
| `tools/pending_global_resweep.py:273` | fail-safe refresh sink |
| `tools/pending_global_resweep.py:836` | reuse `_ga_results`, bez drugiego solve |
| `proposal_refresh.py:73` | nie-actionable `SHADOW_ONLY` serializer |
| `proposal_refresh.py:153` | fleet/winner/cooldown state machine |
| `telegram_approver.py:2045` | istniejący consumer wymaga top-level `PROPOSE` |

Pełna mapa writerów/konsumentów: `docs/R2_PROPOSAL_FRESHNESS_MAP.md`.

## Format i anty-spam

Format `assignment_episode.v1` zawiera wyłącznie order ID, czasy, CID,
PII-free skrót floty, SHA-256 generacji, świeży winner/runner-up/margines,
verdict/routing, realny CID, zgodność po CID, CAS, code SHA i flag fingerprint.
Nie zapisuje nazw, adresów, restauracji, uwag, telefonów ani surowego GPS.

Refresh zapisuje `proposal_refresh.v1` tylko po:

- zmianie SHA-256 posortowanego zbioru dispatchowalnych CID;
- realnej zmianie zwycięskiego CID;
- odstępie co najmniej 120 s od poprzedniego wpisu orderu.

Pierwszy tick tylko seeduje baseline. Stan jest atomowy
`temp→fsync→rename→fsync(dir)` pod `flock`; log ma deterministyczne event ID i
`append_jsonl_once` z kontrolą rotacji.

## Flagi

- `ENABLE_ASSIGNMENT_EPISODE_LOG = False`
- `ENABLE_PROPOSAL_REFRESH = False`

Obie są w `ETAP4_DECISION_FLAGS`, odcisku, conftest-strip, dokumentacji i
rejestrze lifecycle. Seeder uruchomiono z `--merge` na izolowanych źródłach do
`/tmp`, ponieważ sandbox blokował żywy `flags.json`; do repo przeniesiono tylko
dwa nowe, jawnie skuraturowane wpisy. `flag_lifecycle_check --skip-external`
zwraca `ok=true`, 0 błędów.

## Testy

| Bramka | Wynik |
|---|---|
| `py_compile` 5 zmienionych modułów + test | PASS |
| R2 + pending resweep + assignment lag | `26 passed` |
| szwy panel-watcher | `113 passed` |
| durable event/state generation | `143 passed` |
| flag effect coverage | `3 passed`; brak nowej luki |
| conftest strip — dwa mechaniczne oracles | `2 passed` |
| lifecycle registry repo-hermetic | PASS, 0 errors |
| R2 flag-doc na izolowanym flags.json | oba udokumentowane, `new_drift=[]` |
| night-guard manifest | v32, 5832 nodeidów, hash poprawny |
| entropy dashboard | wykonał się; brak dostępu do żywych plików, AUTO #4 N/D |

Mutation-sensitive dowody:

- usunięcie fresh `_solve_fresh` / użycie starego pola nie daje CID-B i czerwieni
  oracle „stara A → aktualna B”;
- usunięcie exact generation CAS zapisuje stale `assign-old` i czerwieni test;
- usunięcie lifecycle lock lub rozdziału `SHADOW_ONLY` czerwieni source ratchet;
- brak cooldown/winner/fleet gate daje dodatkowy rekord i czerwieni anty-spam.

Pełny bieg systemowym Pythonem zatrzymał się podczas collection: 10 błędów
środowiskowych (`schedule_utils`, dwa skrypty replay oraz PermissionError przy
sprawdzeniu istnienia chronionego `panel.env`). Kanoniczny venv oraz pełny
pkgroot/flags są w sandboxie niedostępne. Dlatego wymagane:

```text
/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q
```

oraz porównanie z baseline master pozostają do wykonania przez CTO. Delta R2 w
wykonanych klastrach jest pusta; delta pełnej suity nie jest atestowana.

## Commity do wykonania przez CTO

Sandbox blokuje `.git`, więc commitów nie utworzono. Jawne pathspecy:

```bash
git add tests/test_r2_proposal_freshness.py tools/night_guard_suite_manifest.json
git commit -m "test(R2): lock proposal freshness oracles"

git add common.py panel_watcher.py proposal_freshness.py proposal_refresh.py \
  tools/pending_global_resweep.py tools/flag_lifecycle_registry.json
git commit -m "feat(R2): add assignment truth and shadow refresh"

git add docs/R2_PROPOSAL_FRESHNESS_MAP.md ZIOMEK_LOGIC_REFERENCE.md \
  ZIOMEK_BACKLOG.md eod_drafts/2026-07-28/R2_PROPOSAL_FRESHNESS_REPORT.md
git commit -m "docs(R2): map proposal freshness contracts"
```

Bez `git add -A`, bez push i bez merge do mastera.

## Otwarte bramki

1. CTO: kanoniczna pełna suita + baseline master; oczekiwana delta pusta.
2. CTO: commit trzy warstwy powyżej i merge wyłącznie do
   `integration/noc-20260728`.
3. FLIPMASTER/owner: dodanie kluczy false/true do żywego `flags.json` jest osobną
   operacją. Ten sprint jej nie wykonał.
4. Wykonanie/autonomia pozostaje HOLD do co najmniej 24 h i `n >= 200` czystych
   `assignment_episode.v1`, potem niezależny review i owner gate.
5. Ledger `engine.proposal-freshness-r2` oraz zewnętrzne memory/todo wymagają
   transition/handoff przez CTO; sandbox worktree nie ma prawa zapisu poza repo.

## Rollback

Kod jest inert przy braku kluczy. Po przyszłej aktywacji natychmiastowy rollback
to ustawienie odpowiedniej flagi na `false`/usunięcie klucza (hot reload).
Pełny rollback kodu: revert trzech jawnych commitów; append-only logi pozostają
historią, a sidecar refresh nie jest wejściem żadnej decyzji.
