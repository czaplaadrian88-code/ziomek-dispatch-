# Mapa modułów, wywołań i ownership

| Domena | Wejście / owner | Główni konsumenci | Efekty / zapis | Ryzyko ownership |
|---|---|---|---|---|
| Ingest | `panel_watcher` | state machine, event bus, decide | orders/event/plan signals | parser + writer w jednym procesie |
| Decyzja | `core.decide` / `dispatch_pipeline` | shadow, czasówka, tools | wynik + bufor efektów | część stanu globalna per proces |
| HARD | `feasibility_v2` | candidates/selection | feasibility verdict/metrics | bliźniaki anchorów w replanie |
| SOFT | `core.candidates`, `scoring` | selection | score + terms | część flag env-latched |
| Selekcja | `core.selection`, `objm_lexr6` | shadow serializer | best/verdict/reason | best_effort ma osobne obejścia |
| Routing | `route_simulator_v2`, `tsp_solver` | feasibility, planner | plan/stops/ETA | serializacja nie zachowuje każdej semantyki |
| Plany | `plan_manager` | panel watcher, recheck, UI/API | courier_plans | panel ma cross-repo writer |
| Stan ordera | `state_machine` | niemal wszystkie powierzchnie | orders_state | legacy writer + observer FSM |
| Replay | `world_record`, `world_replay` | nightly gate, CI | rekord/werdykt | niepełny snapshot live inputs |
| Powierzchnie | panel + courier API | człowiek/kurier | assign/cancel/route/status | wspólny kod i cross-repo write paths |

Owner pliku nie jest tożsamy z ownerem kontraktu. Przykładowo kontrakt planu ma
writerów w dispatcherze i panelu; dlatego mapa kompletności musi obejmować oba
repo, a nie tylko `rg save_plan` w jednym katalogu.

## Najważniejsze fan-in/fan-out

- `common.py`: flagi, ścieżki, stałe i logger — najwyższy fan-in.
- `dispatch_pipeline` + `core/*`: orkiestracja nadal łączy wiele warstw.
- `plan_manager`: mały interfejs o dużym wpływie cross-process.
- `panel_client`: granica HTML/CSRF i źródło zależności zewnętrznej.
- `osrm_client`: routing, fallback, cache, recorder i health muszą zachować
  provenance.
