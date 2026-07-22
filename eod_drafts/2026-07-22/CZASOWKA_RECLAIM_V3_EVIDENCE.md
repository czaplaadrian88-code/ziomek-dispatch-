# Czasówka reclaim v3 — evidence bramki

Model: `model_tier=sol`, `effort=ultra` — lifecycle/FIFO, durable state,
HARD carried/committed i kanoniczny ledger mają wysoki koszt błędu.

## Mapa kompletności

| miejsce | rola | writer/consumer | status | dowód |
|---|---|---|---|---|
| `lifecycle_downstream.py` | kolejność efektów | consumer receipt | TAK | writer shadow fail-soft przed invalidacją |
| `czasowka_reclaim.py` | ewaluator i writer | writer | TAK | pełny snapshot worka, exemption store, detail+canonical |
| `shadow_dispatcher.py` | serializer | writer A/B | TAK | wspólny `_propagate_prefixed_metrics` dla A i B |
| `tools/ledger_io.py` | kanoniczny reader | consumer | TAK | observations opt-in, stare mianowniki bez zmian |
| `tools/czasowka_reclaim_shadow_report.py` | raport metryki | consumer | TAK | `read_shadow_metrics` czyta canonical z opt-in |
| `reclaim_exemptions.py` | stan wyjątków | writer/reader | TAK | lock + temp + fsync + rename + fsync dir + audit |
| `tools/czasowka_reclaim_exempt.py` | operacje ownera | writer CLI | TAK | add/remove/list, allowlista reason-code |
| `state_machine.py` | defense-in-depth LIVE stub | consumer eventu | TAK | target committed/carried superseded |
| `panel_watcher._save_plan_on_assign_signal` | recanon assign | handler | N-D | shadow nie mutuje worka/planu |
| `panel_watcher._advance_plan_on_deliver` | recanon deliver | handler | N-D | shadow nie mutuje worka/planu |
| `panel_watcher._recanon_after_plan_cleanup` | recanon return/cancel/reassign | handler | N-D | shadow nie mutuje worka/planu |
| `panel_watcher._update_plan_on_picked_up` | recanon pickup | handler | N-D | shadow nie mutuje worka/planu |

N-D: `feasibility_v2.py` — reclaim shadow ma własne fail-closed guardy i nie
zmienia feasibility przydziału.

N-D: `route_simulator_v2.py` — brak nowego planu lub zmiany kolejności trasy.

N-D: `plan_recheck.py` — istniejąca invalidacja nadal działa; shadow nie recanonizuje.

N-D: `panel_watcher.py` — cztery handlery recanon sprawdzone; wszystkie dotyczą
realnej mutacji worka, której etap shadow celowo nie wykonuje.

N-D: `claim_ledger.py` — lifecycle observation nie wybiera ani nie claimuje
zlecenia z puli; istniejąca obrona double-reclaim pozostaje bez zmian.

N-D: `tools/pending_global_resweep.py` — observation nie uruchamia resweepu ani
nie mutuje pending; istniejąca obrona race z kandydatem pozostaje bez zmian.

N-D: `core/candidates.py` — syntetyczny rekord OBSERVE jest wyłącznie transportem
metryk do serializera, nie kandydatem selekcji.

N-D: `scoring.py` — `would_reclaim` nie wchodzi do final_score i nie zmienia
żadnej decyzji lub tie-breaku.

## Dowody

regresja: failed=0 nowych; baseline 5360 passed / 225 failed / 114 skipped / 9 xfailed / 39 errors; final 5377 passed / 225 failed / 111 skipped / 9 xfailed / 39 errors; delta +17 passed, 3 zegarowe skipy `test_preshift_window` przeszły do pass; zbiory 264 złych nodeidów identyczne.

e2e: 69 passed, 0 failed — durable FIFO full-disk, canonical JSONL A/B,
ledger consumer isolation, exemption CLI/store i carried/committed.

pozytywny-wplyw: fault injection OSError zachowuje invalidację oraz receipt
`applied`; flaga ON zapisuje canonical fields, OFF wykonuje zero I/O; guardy
odrzucają target i sibling carried/committed.

rollback: flaga `ENABLE_CZASOWKA_RECLAIM_SHADOW=false` pozostaje kill-switchem;
kod można cofnąć czterema commitami `git revert` od końca. Nie było deployu,
restartu, migracji ani zapisu żywego stanu.

## Stan wydania

- Flaga shadow: default OFF, bez flipu.
- Flaga LIVE: default OFF, bez callera.
- Operacje live: brak.
- OPEN QUESTIONS: brak dla etapu shadow; semantyka przyszłego LIVE nadal wymaga
  osobnej karty, dowodu i ACK.
