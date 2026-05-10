# czasowka_observability_monitor — README

Multi-source live observability dla TASK A czasówka proactive (5 sources).

## Sources polled

| # | Source | Path | Purpose |
|---|--------|------|---------|
| 1 | state file | `dispatch_state/czasowka_proposals_state.json` | orders + triggers_fired (T-50/T-40/T-0) + final_assignment |
| 2 | candidate_decisions | `dispatch_state/observability/candidate_decisions_YYYYMMDD.jsonl` | filter `source=='czasowka_proactive'` — verdict + best cid/score |
| 3 | learning_log tail | `dispatch_state/learning_log.jsonl` | events: CZASOWKA_PROPOSAL, CZASOWKA_TRIGGER_FIRE, CZASOWKA_DECISION, FLAG_FLIP_TASK_A |
| 4 | journalctl | `dispatch-czasowka.service` | last 5 min ERROR/WARN |
| 5 | journalctl | `dispatch-telegram.service` | last 5 min ERROR/WARN |

## CLI

```bash
# One-shot snapshot (np. pre-flight check)
/root/.openclaw/venvs/dispatch/bin/python \
    /root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-05/czasowka_observability_monitor.py \
    --snapshot

# Watch mode — poll co 30s, print TYLKO przy detekcji zmiany (state count,
# triggers_fired count, final_assignment, lub nowy ts w cands/learning)
/root/.openclaw/venvs/dispatch/bin/python \
    /root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-05/czasowka_observability_monitor.py \
    --watch &

# zapisz PID i wyjście
echo $! > /tmp/czasowka_monitor.pid

# Stop
kill $(cat /tmp/czasowka_monitor.pid)
```

## Output redirection

```bash
# tee (live + log file)
... --watch 2>&1 | tee /tmp/czasowka_monitor_$(date +%Y%m%d_%H%M).log

# pure background log
nohup ... --watch >> /tmp/czasowka_monitor.log 2>&1 &
```

## Read-only guarantee

Monitor jest READ-ONLY:
- state file: `open()` z `LOCK_SH` semantics (json.load nie blokuje writers — to są nasze własne reads bez fcntl tutaj, czyli najmniej-disruptywne, polling pattern)
- candidate_decisions: append-only file, sequential read całego pliku
- learning_log: tail last 200KB (`f.seek(0,2); f.seek(size-chunk)`) — bez blokowania writer
- journalctl: subprocess `--no-pager -q`, 10s timeout

ZERO writes do prod data.

## Watch mode change detection

Signature tuple per cycle:
1. `len(orders)` w state
2. sorted tuple(orders.keys())
3. per-order: `(oid, len(triggers_fired), final_assignment_cid)`
4. `len(czasowka_cands)` + `last_cand.ts`
5. `len(learning_events)` + `last_event.ts`

Zmiana sygnatury = nowy block printed. Brak zmiany = silent (no log spam).

## Expected first-fire output (TASK A T-50, dziś wieczorem)

Gdy `czasowka_scheduler.tick()` wystrzeli T-50 dla #470756:

1. State file zostanie zainicjalizowany z 1 order
2. `triggers_fired["50"]` zostanie zapisany z proposed_cid + score
3. `candidate_decisions` dostanie wpis z `decision.verdict=PROPOSED`
4. (jeśli dispatch-telegram poprawnie zadziała) brak ERROR/WARN w journal
5. (po odpowiedzi z buttonu) `triggers_fired["50"].decision = TAK|NIE|CZEKAJ`

Watcher wyrenderuje fresh block przy każdym z tych 5 kroków.

## Ograniczenia

- **Tail 100 linii learning_log** — events starsze niż ~ostatnie 100 linii NIE pokażą się (FLAG_FLIP_TASK_A z rana NIE widać o 13:00). Świadoma decyzja — focus na "co teraz", nie pełna historia.
- **journalctl wymaga sudo lub user-w-systemd-journal** (na serwerze ok).
- **Per-day rotation** candidate_decisions: po północy plik się zmieni — monitor automatycznie przełączy się dzięki `_today_yyyymmdd()`.
- **Brak alertu do Telegrama** — monitor jest pure-stdout. Adrian ręcznie obserwuje terminal lub `tail -f` log file.

## Cleanup

Brak. Skrypt nie tworzy żadnych artefaktów (Plan: ewentualne future state-cache w `/tmp/czasowka_monitor_state.json`, deferred).
