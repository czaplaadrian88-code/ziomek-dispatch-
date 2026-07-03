# ADR — Architecture Decision Records (Ziomek / dispatch_v2)

Krótkie zapisy KLUCZOWYCH decyzji projektowych, odtworzone z kodu/historii, żeby nowa sesja nie musiała ich re-derywować. Każdy ADR ≤40 linii, wg szablonu: Kontekst → Decyzja → Konsekwencje → Źródła. Produkt Fazy 2 wielkiego audytu (`docs/audyt/10-PLAN.md` K2.3).

**Uwaga:** ADR-y opisują STAN OBOWIĄZUJĄCY, ale nie zastępują Przykazania #0 (`memory/ziomek-change-protocol.md` — JAK bezpiecznie zmieniać) ani kanonu (`ZIOMEK_ARCHITECTURE.md`/`ZIOMEK_INVARIANTS.md`). Konflikt reguł / niejasny priorytet → najpierw kanon, potem PYTAJ Adriana.

| ADR | Decyzja (w skrócie) |
|---|---|
| [ADR-001](ADR-001-pipeline-hard-przed-soft.md) | Pipeline 10 warstw; HARD (feasibility) zawsze przed SOFT (scoring), SOFT nigdy nie osłabia HARD; `_assert_feasibility_first`; fix u źródła, nie na krawędzi. |
| [ADR-002](ADR-002-shadow-first-flip-za-ack.md) | Shadow-first: flaga default OFF → pomiar/replay → dowód pozytywnego wpływu → flip TYLKO za ACK Adriana; rollback (flaga+`.bak`+tag) gotowy zawczasu. |
| [ADR-003](ADR-003-always-propose.md) | Always-propose: Ziomek nigdy „brak kandydatów" — eskalacja feasible→łamiący R6→best-effort z tagiem ALERT; KOORD tylko early-bird/czasówka ≥60min. |
| [ADR-004](ADR-004-flagi-trzy-swiaty.md) | Flagi = 3 światy (silnik=flags.json po D3 / panel=flags.systemd.env+inline / apka=drop-iny+config.py); stan EFEKTYWNY czytaj z procesu, nie z env-default. |
| [ADR-005](ADR-005-stan-runtime-poza-repo.md) | Stan runtime POZA repo (`workspace/dispatch_state/` ~1 GB); repo `dispatch_v2/dispatch_state/` = tylko epaka (zbieg nazw); `shadow_decisions.jsonl` w `scripts/logs/`. |
| [ADR-006](ADR-006-srodowiska-venv.md) | Trzy interpretery: venv `dispatch` (silnik+testy; ortools; HTTP przez stdlib urllib) / venv `sheets` (Google) / system py (mosty); testy TYLKO venv dispatch. |
| [ADR-007](ADR-007-multi-sesja-worktree.md) | Multi-sesja: worktree per sesja mutująca (C12b); add+commit atomowo po jawnych ścieżkach (C1-git); merge/restart seryjnie za ACK; nigdy nie cofać cudzego live. |
| [ADR-008](ADR-008-rdzen-nie-przenoszony.md) | Rdzeń silnika NIE przenoszony fizycznie (systemd `-m dispatch_v2.X` + import cross-repo); nawigacja = CODEMAP/ARCHITECTURE; pakietyzacja = osobny sprint pod #0. |
