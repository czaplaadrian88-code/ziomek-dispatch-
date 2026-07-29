# FIX-PACK 1 — fundament eskalacji po blind review (F1–F9)

## Werdykt

F1–F9 są domknięte source/test w nocnym worktree. Wszystkie cztery flagi
pozostają `default=False`; nie wykonano flipu, zapisu runtime, deployu,
restartu, Telegrama ani innej operacji live.

Model/effort: `sol` / `xhigh` — zmiana przecina kontrakt feasibility,
S1→S2→S3, selection i oba miejsca serializacji, z wysokim kosztem błędu
termicznego.

Deklarowana przez ownera baza worktree: `82b50c5db`. Mechaniczne `git
status/log/diff` są niedostępne, ponieważ `.git` wskazuje na hostowy gitdir
zablokowany w sandboxie. Nie naprawiano ani nie odtwarzano metadanych Git.

## Root cause F9 i naprawa końcowa

Zastany F9 był za słaby: tworzył `PipelineResult` bez nowych artefaktów.
Przechodził więc nawet wtedy, gdy serializer ufał wyłącznie gejtom producentów.

RED-first po włożeniu stale/podrobionych artefaktów do realnego
`PipelineResult` wykazał pięć przecieków top-level przy wszystkich flagach OFF:

- `carry_eval`;
- `alarm_certificate`;
- `strategy2_probe`;
- `order_created_at`;
- `hard35_enforcement`.

Ten sam kontrakt miał bliźniaczy kanał LOCATION A+B: centralny helper
candidate metrics przepuszczał `carry_eval` i `hard35_enforcement`.

Hash RED rekordu: `c3b5b80f1c7f1ba150a49707d981e877edbd758b60f964c86c84fb5d9dc9348f`.

Naprawa u źródła jest w finalnej granicy serializera:

- top-level emituje artefakt tylko przy aktywnej fladze będącej jego ownerem;
- wspólny helper LOCATION A+B ma mapę feature-owned metrics→owner flag;
- test ON jawnie włącza flagi i nadal dowodzi propagacji A+B;
- test OFF podaje wszystkie artefakty mimo OFF, wymaga braku pól i porównuje
  pełne kanoniczne bajty z zamrożonym baseline.

Hash GREEN/OFF po naprawie:
`b6cc2e629ed7d8c09cc0cadd2065d376020508411979bb8684eb8714b6203c89`.
Hashu nie aktualizowano.

## Macierz F1–F9

| Finding | Fix u źródła | Oracle / mutation | Status |
|---|---|---|---|
| F1 | `core/alarm_certificate.is_alarm()` sam sprawdza `ENABLE_ALARM_CERTIFICATE_SHADOW`; producer nie jest jedyną ochroną | ważny certyfikat + flaga OFF ⇒ `False` | PASS |
| F2 | wszystkie trzy wyjścia `PROPOSE` (`feasible`, `best_effort`, `solo`) przechodzą przez `_hard35_proposal_boundary` przed emisją | `MAYBE` z raw carry `35.01` ⇒ `KOORD` + least-damage | PASS |
| F3 | pipeline wykonuje S2 przed budową certyfikatu; cert wiąże fingerprint i `strategy2_found` | zero S1≤35 + S2≤35 ⇒ `NORMAL_STRATEGY2`, `alarm=False` | PASS |
| F4 | `validate` przelicza kontrfakt i pool fingerprint z realnej puli; ścieżka bez puli wymaga evidence utworzonego dopiero po tej walidacji | sfałszowane `between_35_40_count/cids` ⇒ `False` | PASS |
| F5 | `shadow_probe` omija saved-plan consumption/writery; `_soon_free_probe(..., pure_read=True)` czyta świeży store bez cache i bez invalidacji mismatch | bajty planu i `_perf_plans_cache` przed==po | PASS |
| F6 | `BOUND_POSSESSION_SOURCES` jest allowlistą fizycznych GPS/handoff; każdy inny opis źródła pozostaje `proxy` | `panel.picked_up_at` ⇒ `proxy`, nie `bound` | PASS |
| F7 | `carry_min` jest zaokrąglane tylko prezentacyjnie; `le_35/le_40`, `max` i agregaty używają raw float | `35.004` wyświetla `35.0`, ale `le_35=False` | PASS |
| F8 | mutation podmienia realny egzekutor HARD35 na no-op, nie reason-string | real ⇒ `KOORD`; mutant ⇒ `PROPOSE`, więc test zabija wyłączenie haka | PASS |
| F9 | finalny serializer gejtuje top-level i LOCATION A+B według owner flag | stale pola przy wszystkich flagach OFF ⇒ identyczny hash baseline | PASS |

## Mapa kompletności

| Miejsce | Rola | Writer/consumer | Dotknięte | Dowód |
|---|---|---|---|---|
| `core/carry_freshness.py` | kanoniczny carry/provenance | owner | TAK | F6/F7 + literal timestamps |
| `feasibility_v2.py` | producer `carry_eval` | writer | TAK | tylko `ENABLE_CARRY_CANON_V2` |
| `core/candidates.py` | wspólny evaluator S1/S2 | consumer | TAK | `shadow_probe`, pure saved-plan read |
| `core/strategy2_probe.py` | horyzont i pełna flota | producer | TAK | slot×fleet oracle |
| `dispatch_pipeline.py` | kolejność S1→S2→S3 | orchestrator | TAK | F3 i source ratchet |
| `core/alarm_certificate.py` | kontrfakt, validate, HARD35 | owner/consumer | TAK | F1/F3/F4/F8 |
| `core/selection.py` | trzy wyjścia PROPOSE | consumer | TAK | F2 + F8 + ratchet 3 call-sites |
| `core/loadgov_snapshot.py` | okno G5 | consumer | TAK | forged cert zostaje strict |
| `core/lex_window_guards.py` | cap 35/40 | consumer | TAK | Alarm tylko z certyfikatem |
| `plan_recheck.py` | G2/G4 recheck | consumer | TAK | wspólny cert/carry |
| `shadow_dispatcher.py` top-level | rekord decyzji | consumer/writer | TAK | F9 pełny hash |
| `shadow_dispatcher.py` LOCATION A+B | candidate metrics | bliźniaczy consumer | TAK | ON propagation + OFF stale-field oracle |
| panel/restauracja/apka/Telegram | przyszłe pełne S2 | consumer/writer | N-D | poza zakresem, zero live |
| fizyczny producer possession | ground truth | writer | N-D | wymaga potwierdzonego event contract |

## Dowody testowe

- RED F9: pięć top-level pól oraz dwa candidate metrics przeciekały przy OFF;
  hash `c3b5b8…dc9348f`.
- Wymagany klaster:
  `test_escalation_ladder_night_2026_07_28.py +
  test_verdict_gate_guards.py + test_wb2_conditional_guards.py`:
  `76 passed`.
- Klaster poszerzony o kompletność serializera, funkcjonalny parytet
  LOCATION A↔B i hard-metrics: `91 passed`.
- Ten sam poszerzony klaster z `HERMETIC_STRICT=1`: `91 passed`.
- `py_compile` wszystkich plików klastra i zmienionych modułów: PASS.
- Hermetyczny import check worktree: PASS.
- `flag_lifecycle_check.py`: `547/547 curated`, `0 errors`.
- trailing-whitespace ratchet: PASS.
- entropy dashboard: wykonał się read-only, ale sandbox widzi
  `pliki żywego silnika: 0`; auto-metryki są N/D i nie stanowią verdictu.
- mechaniczny `ziomek-cto dod` potwierdził PASS dla czterech nowych flag,
  ich testów ON≠OFF/rejestru, obu nowych metryk, regresji, E2E, wpływu i
  rollbacku. Końcowy exit pozostał FAIL/HOLD, ponieważ bez Git użyta baza
  katalogowa `integration-noc` wciągnęła obce wcześniejsze flagi i bliźniaki
  (`C2`, claim-ledger, saved-plans). Nie maskowano tych false-positive'ów;
  prawdziwy DoD trzeba powtórzyć na `git diff 82b50c5db...candidate`.

Testy uruchomiono systemowym Pythonem 3.12 z jednoznacznym
`ZIOMEK_SCRIPTS_ROOT=.test-pkgroot`, po sprawdzeniu ścieżek importu.
Kanoniczny interpreter `/root/.openclaw/venvs/dispatch/bin/python` został
odrzucony przez sandbox (`Permission denied`). Pełna suita venv pozostaje
bramką CTO; nie zastąpiono jej fałszywą pełną suitą bez `ortools`.

## Rollback

Source rollback: po odzyskaniu Git odwrócić wyłącznie jawne pliki FIX-PACK,
następnie powtórzyć klaster i hash OFF. Operacyjny kill-switch już istnieje:
wszystkie cztery flagi są OFF. Nie ma runtime/deploy/restartu ani danych do
cofania.

## Co zostaje / HOLD

- pełna kanoniczna suita venv i delta względem baseline;
- sprawny `git status/diff/log`, commit/tag/push tylko przez CTO;
- ponowny blind review kandydata i mechaniczna bramka DoD na prawdziwym diffie;
- ledger transition z SHA+hash oraz wspólny memory/todo handoff;
- 2 dni shadow, replay i wszystkie decyzje ownera dotyczące pełnego S2,
  fizycznego possession, Alarmu, capa 40 i break-glass;
- jakikolwiek flip, deploy, restart lub live nadal zabroniony w tym zadaniu.
