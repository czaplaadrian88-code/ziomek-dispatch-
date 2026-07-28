# R2 — mapa świeżości propozycji

## Kontrakt i root cause

Root cause był w cyklu życia prawdy: `NEW_ORDER` tworzył propozycję raz, natomiast
realny przydział przechodził później przez wspólny chokepoint
`panel_watcher._emit_and_apply_state`. Nie istniał writer prawdy
assignment-time. R2 nie nadpisuje starego rekordu ani renderu. Dodaje osobny,
kanoniczny epizod pomiarowy oraz rozłączny, shadow-only kanał refresh.

Zgodność wszędzie oznacza `str(proposed_cid) == str(actual_cid)`. Nazwa kuriera
nie jest identyfikatorem i nie jest zapisywana (finding P2-6 konsoli).

## Mapa kompletności

| Miejsce | Rola | Writer / consumer | Dotknięte | Powód / test |
|---|---|---|---|---|
| `panel_watcher._emit_and_apply_state` | wszystkie realne przydziały z panelu/koordynatora | chokepoint | TAK | prepare przed durable apply, commit po nim; fail-safe test |
| `durable_event_apply.emit_and_apply` | event + outbox + state | writer generacji | N-D | istniejący owner transakcji; R2 konsumuje outcome |
| `state_machine.update_from_event` | `courier_id`, `assigned_at`, `assignment_event_id`, lifecycle marker | writer stanu | N-D | istniejący owner; CAS czyta oba exact-event markery |
| `state_machine.lifecycle_apply_lock` | serializacja writerów state | boundary | TAK | obejmuje CAS + append; mutation ratchet |
| `proposal_freshness.order_event_from_state` | state → canonical decision input | jeden owner projekcji | TAK | prior proposal fields są poza allowlistą; używa też resweep |
| `core.decide(WorldState(...), _bypass_early_bird=True)` | pełna selekcja na teraz | canonical consumer | TAK | mutation test świeżego solve |
| `courier_resolver.dispatchable_fleet` | aktualna dostępna flota | canonical producer | TAK | pełny snapshot przed przydziałem |
| `proposal_freshness.commit_assignment_episode` | `assignment_episode.v1` | jedyny writer | TAK | CAS generacji, `append_jsonl_once`, rotacje, fsync |
| `assignment_episode.jsonl` | prawda pomiaru assignment-time | append-only store | TAK | osobny kontrakt, bez PII |
| `tools.pending_global_resweep.global_allocate` | re-solve wiszących orderów | canonical producer | TAK | wynik `_ga_results` używany bez drugiego solve |
| `proposal_refresh.record_refreshes` | detektor fleet-generation + anti-spam | jedyny writer refresh | TAK | atomowy state, membership + winner + cooldown |
| `shadow_decisions.jsonl` | wspólny audyt silnika | store / replay input | TAK | addytywny `SHADOW_ONLY`, bez actionable shape |
| `pending_proposals_store` | propozycja dla konsoli/1-click | writer/consumer | N-D | refresh świadomie nie mutuje |
| `global_alloc_store` | overlay konsoli | writer/consumer | N-D | R2 nie uruchamia tego writera, gdy legacy resweep OFF |
| `telegram_approver` | emituje tylko actionable proposal | consumer | N-D | refresh nie ma top-level `PROPOSE` ani `best` |
| panel `autonomy_report/feed.py` | zgodność / render propozycji | consumer poza worktree | N-D | nie zmieniamy renderu; nowy pomiar porównuje CID, nie nazwę |
| `tools/world_replay*`, analizatory | odtwarzanie decyzji | przyszły consumer | N-D | format addytywny; nie udaje rekordu zwykłej decyzji |
| `decision_eta_log` | osobna telemetria ETA | writer | N-D | R2 nie dubluje ani nie miesza kontraktów |

## `assignment_episode.v1`

Przykład PII-free (wartości skrócone):

```json
{
  "schema": "assignment_episode.v1",
  "order_id": "OID-1",
  "proposal_computed_at": "2026-07-28T01:02:03+00:00",
  "assignment_at": "2026-07-28T01:02:04+00:00",
  "assignment_generation": "assign-uuid",
  "fleet": {
    "available_count": 2,
    "available_cids": ["CID-A", "CID-B"],
    "couriers": [
      {"cid": "CID-A", "bag_size": 1, "pos_source": "gps"},
      {"cid": "CID-B", "bag_size": 0, "pos_source": "gps"}
    ],
    "generation": "sha256:..."
  },
  "proposal": {
    "winner_cid": "CID-B",
    "runner_up_cid": "CID-A",
    "winner_score": 90.0,
    "runner_up_score": 74.0,
    "score_margin": 16.0,
    "verdict": "PROPOSE",
    "routing": "ACK",
    "pool_total": 2,
    "pool_feasible": 2,
    "selection_scope": "full_pool_pre_top_n"
  },
  "actual_assigned_cid": "CID-B",
  "agreement": true,
  "cas": {
    "matched": true,
    "state_assignment_event_id": "assign-uuid",
    "state_lifecycle_marker": "assign-uuid"
  },
  "code_sha": "...",
  "flag_fingerprint": "..."
}
```

Nie występują: nazwy kurierów, restauracja, adres, uwagi, telefon ani surowe GPS.
Pozycja i zawartość worka wpływają wyłącznie na SHA-256 generacji floty; w
rekordzie jawne są tylko CID, liczność worka i typ źródła pozycji.

## Refresh i anty-spam

Stan `proposal_refresh_state.json` jest zapisywany
`temp → fsync → rename → fsync(dir)` pod `flock`. Pierwszy tick tylko ustanawia
baseline. Kolejny rekord powstaje, gdy jednocześnie:

1. zmienił się hash posortowanego zbioru dispatchowalnych CID;
2. dla wciąż nieprzypisanego orderu zmienił się zwycięski CID;
3. od ostatniego refresh orderu minęło co najmniej 120 s.

Idempotencję crash/retry zapewnia deterministyczny `event_id` oraz
`append_jsonl_once` skanujący rotacje. Błąd state/log/solve kończy wyłącznie
instrument, nie legacy resweep ani assignment.

## Aktywacja i rollback

Obie flagi są default OFF i hot-reload. Kod nie aktywuje flag, nie restartuje
usług i nie zapisuje żywego state. Aktywacja wymaga osobnej bramki R2:
co najmniej 24 h oraz `n >= 200` czystych assignment-time episodes przed
jakimkolwiek wykonaniem. Rollback pomiaru/refresh: usunięcie klucza lub `false`
w `flags.json`; zapisane logi pozostają audytowalną historią.
