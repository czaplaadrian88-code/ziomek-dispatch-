# Choice-set + join hardening — dowód wykonawczy

Data: 2026-07-21
Zakres: wyłącznie kod i testy w klonie `/root/cx-choiceset`; bez merge, deployu,
restartu, zapisu stanu runtime i flipu flag. Bazowy HEAD: `00be30ba`.

## ETAP 0 — baseline i ograniczenia

- `ziomek-cto brief`: repo czyste, HEAD `00be30ba`; zewnętrzny health zwrócił
  `rc=2`, bo sandbox nie widzi `night_guard_history.jsonl`. To stan poza klonem.
- Kanoniczny interpreter `/root/.openclaw/venvs/dispatch/bin/python` jest dla tej
  sesji niedostępny (`Permission denied`). Pełna suita należy do CTO zgodnie ze
  zleceniem ownera; lokalne testy celowane zostaną oznaczone jako niekanoniczny
  smoke, jeśli jedynym dostępnym runnerem pozostanie `/usr/bin/python3`.
- `../flags.json` nie należy do świeżego klonu i nie będzie modyfikowany. Obie
  nowe flagi mają fallback OFF; wpisanie kluczy `false` do nośnika live jest
  krokiem wydania CTO, nie częścią tego commitu.
- Zewnętrzny checkout panelu pod
  `/root/.openclaw/workspace/nadajesz_clone/panel` jest nieczytelny w sandboxie.
  Lokalny kontrakt `pending_proposals`/`panel_watcher` i czytniki ledgera są
  sprawdzane testem addytywności; odczyt checkoutu panelu pozostaje bramką CTO.

## ETAP 1–3 — root cause i mapa kompletności

Root cause A: `core.selection` zachowuje pełną listę `candidates`, ale przy
budowie `PipelineResult` ucina ją do `top[:16]` / `with_plan[:16]`; serializer
nie ma więc danych do pełnego choice-setu. Root cause B: rekord shadow idzie do
`pending_proposals.decision_record` z `event_id`, lecz writer learning_log go
nie przepisuje. Świeży E1 zapisuje pod nazwą `lifecycle_event_id` inne ID —
późniejszego `COURIER_ASSIGNED` — używane do idempotencji outboxa.

| miejsce / pole | rola | writer / consumer | dotknięte | powód / test |
|---|---|---|---|---|
| `core/candidates.py` | tworzy pełną pulę | writer obiektów `Candidate` | N-D | pula już kompletna; test >16 na granicy selection→serializer |
| `core/gates.py` | early return przed budową puli | writer pustego `PipelineResult` | N-D | geocode/early-bird nie mają jeszcze kandydatów; addytywny default daje pustą pulę |
| `core/selection.py` wszystkie EMIT-y | traci pełną pulę przy top-N | writer `PipelineResult.full_pool_candidates` | TAK | każdy konstruktor wyniku dostaje tę samą pełną listę; test ścieżki >16 |
| `dispatch_pipeline.PipelineResult` | granica warstw | schema writer/reader | TAK | addytywne pole z defaultem; grep wszystkich konstruktorów/fake'ów |
| `shadow_dispatcher._serialize_candidate` (A) | pełny serializer alternatives | consumer kandydata | TAK | wspólny sześciopolowy projection helper; test parytetu A |
| inline `best` w `_serialize_result` (B) | pełny serializer best | consumer kandydata | TAK | ten sam helper co A; test parytetu B |
| `full_pool_compact` | nowy ledger field | writer `_serialize_result` | TAK | tylko ON; dokładnie 6 kluczy, best + pełna pula bez duplikatu CID |
| `common.py` | kontrakt flag | ETAP4, const fallback, fingerprint | TAK | OFF/ON różne w testach |
| lifecycle registry + logic reference | dokumentacja/rollback | checkery | TAK | lifecycle `shadow`, rollback hot OFF |
| `pending_proposals_store` | przenosi cały decision dict | writer/reader | N-D | schema otwarta, bez filtrowania; round-trip z obcym polem |
| `panel_watcher` pending consumer | czyta `best`/`event_id` przez `.get` | consumer | TAK | nowe pole top-level ignorowane; join bierze `decision_record.event_id` |
| checkout panelu koordynatora | możliwy zewnętrzny consumer | reader | N-D | sandbox `Permission denied`; jawna bramka CTO przed merge |
| `daily_briefing`, replay/analyzery | czytają JSON dict | consumers learning_log | N-D | brak strict schema i brak użycia starego znaczenia pola; backcompat test starego rekordu |
| `_write_panel_agree` | writer PANEL_AGREE | source learning_log | TAK | ON przepisuje shadow `event_id` do `lifecycle_event_id` |
| `_check_panel_override` | writer PANEL_OVERRIDE | source learning_log | TAK | ten sam helper i kontrakt co AGREE |
| `_append_learning_record` + SQLite projection | dedupe retry | consumer ID przypisania | TAK | osobne `assignment_lifecycle_event_id`; fallback czyta stare projekcje E1 |
| `PANEL_LEARNING_NONE` | wewnętrzny receipt bez decyzji | writer/consumer outbox | N-D | zachowuje stare ID przypisania; nie jest lekcją AGREE/OVERRIDE |

HARD/SOFT: zmiana wyłącznie obserwacyjna, nie dotyka feasibility, score,
selekcji ani execution authority. Part B jest za osobną flagą, ponieważ na tym
HEAD zmiana znaczenia istniejącego klucza nie jest czysto addytywna.

N-D: `auto_assign_gate.py` — nie serializuje kandydatów; klasyfikacja auto-route
pozostaje na istniejącym top-N i nie konsumuje nowego pola granicy.
N-D: `drive_min_calibration.py` — nie jest writerem/readerem decision ledger;
projekcja kopiuje już policzone metryki bez zmiany kalibracji.
N-D: `tools/reassignment_forward_shadow.py` — osobny obserwator replay, nie
konstruuje `PipelineResult` ani nie parsuje nowego pola strict.
N-D: `objm_lexr6.py` — selekcja/tie-break pozostają bez zmian; serializer tylko
zachowuje wejściową pulę i finalny wariant best.
N-D: `claim_ledger.py` — choice-set to addytywne pole decision ledger, bez zmiany
claimów i bez konsumpcji przez ten moduł.
N-D: `tools/pending_global_resweep.py` — czyta wybrane klucze przez `.get`; nie
waliduje zamkniętego schematu decision record.
N-D: `scoring.py` — wynik i termy score nie zmieniają się; tylko kompaktowa
projekcja istniejącej wartości.
N-D: `plan_recheck.py` — writer learning_log nie modyfikuje planu ani recanon.
N-D: `route_order.py` — j.w.; brak konsumpcji obu nowych pól.
N-D: `route_podjazdy.py` — j.w.; brak konsumpcji obu nowych pól.

## ETAP 4–7 — evidence

regresja: 231 passed, 1 skipped, 0 failed w celowanym smoke; po mutacji 9 passed,
0 failed; lifecycle 16 passed, 0 failed. Runner niekanoniczny, pełna suita = CTO.

e2e: ON serializuje 20/20 mimo top-16, pending przenosi addytywne pole, a writerzy
PANEL_AGREE i PANEL_OVERRIDE zapisują dokładny shadow event_id.

pozytywny-wplyw: ON≠OFF; A OFF bez klucza, ON pełne 6 pól; B OFF zachowuje E1,
ON rozdziela shadow-ID od assignment-ID bez zmiany decyzji.

rollback: obie flagi false; kodowo git revert commitu; bez migracji danych.

- Runner testów był niekanoniczny `/usr/bin/python3`, bo sandbox blokuje venv.
- Mutation-probe A: zamiana źródła na legacy `result.candidates` została złapana
  (`17 != 20`, 1 failed). Mutation-probe B: podstawienie assignment-ID zamiast
  shadow-ID zostało złapane (1 failed). Obie mutacje cofnięto; re-test 9 passed.
- Checkery: `flag_lifecycle_check --repo-hermetic --skip-external` = ok,
  516/516 curated, 0 errors; syntetyczny doc-check = 0 new drift / 0 stale;
  `git diff --check` czysty, compile 6/6, registry JSON poprawny. Grep wykazał
  30 call-site'ów/fake'ów panel-learning i 45 konstruktorów `PipelineResult`;
  żaden konstruktor nie używa argumentów pozycyjnych.
- live/deploy/restart/fingerprint: N-D — nie wykonywano, CTO robi merge i suitę.
