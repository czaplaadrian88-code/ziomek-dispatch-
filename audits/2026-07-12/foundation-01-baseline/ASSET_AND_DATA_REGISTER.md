# Rejestr aktywów i danych

Nie odczytywano rekordów. Lokalizacje runtime są podane jako bezpieczne aliasy: `runtime:state` = produkcyjny katalog stanu poza repo, `runtime:logs` = produkcyjne logi. Status wykorzystania wynika z writerów/consumerów, procesów i harmonogramów; gdy nie był potwierdzony, jest `UNKNOWN`.

## Kod, konfiguracja i infrastruktura

| ID | Aktywum / lokalizacja | Format | Producer / writer | Consumer | Wersja/schema/provenance | Użycie i odtwarzalność | Prywatność / integralność |
|---|---|---|---|---|---|---|---|
| AST-001 | kod `dispatch_v2` | Python/Git | developer workflow | daemony/timery/testy | commit `c7de9f2`; brak build artifact | active; źródło odtwarzalne, loaded state nieatestowany | dirty/shared checkout risk |
| AST-002 | sąsiedni panel Nadajesz | Python/TS/Git | osobne repo | panel/watcher/operator | osobny HEAD i dirty state | active; częściowo odtwarzalne | zawiera wrażliwy config i operacyjne helpery |
| AST-003 | `courier_api` | Python/Git + venv | osobne repo | app/GPS/API | osobny HEAD; DB schema v7 | active; częściowo | session/GPS/PIN classes |
| AST-004 | courier app | mobile/Git | osobne repo | kurierzy/API | osobny branch/HEAD, untracked docs | active deployment nieatestowany | GPS/user data |
| AST-005 | `flags.json` / flags registry | JSON | operator/deploy/hot reload | dispatcher i narzędzia | 280 live keys; registry 505 wpisów w frozen repo | active; historyczne wartości niepełne | mode 0600 live; 3-world drift |
| AST-006 | config/env/drop-iny | JSON/env/systemd | deploy/operator | wszystkie procesy | poza Git lub rozproszone | active; niepełna reprodukcja | secrets; treść celowo nieczytana |
| AST-007 | `rule_weights.json` | JSON live state | ręczna kalibracja | scoring | defaults w kodzie, live plik zewnętrzny | active; historia nieudowodniona | integrity/provenance risk |
| AST-008 | systemd units/timers | unit files | repo + ręczny `/etc` | systemd | repo niepełny mirror | active; exact deployment częściowo | root services, drift source/live |
| AST-009 | kontenery | image IDs/tags | image build/pull | OSRM, DB, monitoring, OpenClaw | część mutable tags, brak jednego manifestu | active; rebuild niedeterministyczny | supply chain/network |

## Stan, decyzje i operacje dispatch

| ID | Aktywum / lokalizacja | Format | Producer / writer | Consumer | Wersja/schema/provenance | Użycie i odtwarzalność | Prywatność / integralność |
|---|---|---|---|---|---|---|---|
| AST-020 | `runtime:state/orders_state.json` | JSON 0600 | `state_machine.py` | dispatcher/watcher/plany | lock, atomic replace, `.prev` | active; stan bieżący częściowo odbudowalny | order IDs/operacje |
| AST-021 | `runtime:state/courier_plans.json` | JSON 0600 | `plan_manager.py`, recheck | dispatcher/app/recheck | schema v1, lecz referencja do `/tmp/v319_schema.json` | active; kontrakt nie w pełni trwały | trasy/IDs |
| AST-022 | pending proposals | JSON 0600 | shadow dispatcher | panel/operator | dynamiczne | active; historia ograniczona | kandydaci/decyzje |
| AST-023 | `runtime:logs/shadow_decisions.jsonl` + rotations | JSONL 0644 | shadow serializer | replay/raporty/dashboard | rotacje; writer z flock | active; częściowy decision replay | kandydaci, metryki, IDs; zbyt szerokie prawa |
| AST-024 | `runtime:state/learning_log.jsonl` | JSONL 0644 | panel/operator linkage | learning/eval | powiązanie proposed→actual częściowe | active; correction provenance częściowe | korekty operatorów/IDs |
| AST-025 | `runtime:state/world_record/*.jsonl` | JSONL 0644, ~942 MB/7 plików | world recorder | world replay/gate | full decision-time snapshot; 14 dni | active; najlepszy krótki replay, brak long-term | GPS/orders/flags; prawa 0644 |
| AST-026 | `runtime:state/events.db` | SQLite WAL 0644 | event bus | dispatcher/workery/rebuild | retry/DLQ schema live; event ~48h, audit ~90d | active; pełny payload po ~2d niedostępny | IDs/payload/integrity |
| AST-027 | decision outcomes / SLA logs | JSONL | timery i lifecycle | ETA/eval/reports | rotacje/as-of częściowo | active; proxy i assignment anchors | order/courier IDs |
| AST-028 | health scoreboards/verdict cards | JSON/Markdown 0600 w state | monitors/gates | operator/timers | timestamped, różne oracles | active | aggregate, ale opsec |

## GPS, ETA, routing i cache

| ID | Aktywum / lokalizacja | Format | Producer / writer | Consumer | Wersja/schema/provenance | Użycie i odtwarzalność | Prywatność / integralność |
|---|---|---|---|---|---|---|---|
| AST-030 | last-known GPS / GPS API | JSON/HTTP | `gps_server`, courier app | resolver/feasibility/scoring/monitors | bbox guard, atomic write części głównych plików | active; historyczne coverage częściowe | dokładne współrzędne i IDs |
| AST-031 | `courier_api.db:gps_history` | SQLite v7 0644 | courier API | API/analytics | schema-only potwierdzona | active; dynamiczne | GPS, sessions, IP, PIN classes |
| AST-032 | restaurant dwell / courier ground truth / delivery truth | JSON/JSONL | geofence/app/validation | ETA dataset/eval | whole-map niewersjonowane; snapshot reconstructability false | active/derived; historyczny as-of ograniczony | GPS/address/order/courier |
| AST-033 | SLA ETA i live order ETA | JSON/JSONL | dispatcher/SLA | panel/eval | klik/proxy vs observable rozdzielone | active; nie pełny physical truth | order/customer timing |
| AST-034 | `runtime:state/eta_calib.db` | SQLite | feature/calibration tools | ETA calibration | dynamic schema, decision/outcome features | użycie offline/periodic; mutujące narzędzia | IDs/coordinates/features |
| AST-035 | OSRM service/cache | HTTP/SQLite/JSON | OSRM i callers | routing/ETA/replay | lokalny kontener, cache dynamiczny | active; image/rebuild niepełny | trasy/coords |
| AST-036 | geocode cache | JSON 0600 | geocoder | dispatcher/panel | dynamiczny live state | active; historia niepełna | adresy/koordynaty |
| AST-037 | `docs/eta/06_ground_truth_contract.md` | Markdown contract | repo workflow | dataset/reviewer | schema v1, `canonical_kpi_event=unbound` | active candidate | bez raw danych; bezpieczny opis |

## Modele, reguły, prompty i ewaluacja

| ID | Aktywum / lokalizacja | Format | Producer / writer | Consumer | Wersja/schema/provenance | Użycie i odtwarzalność | Prywatność / integralność |
|---|---|---|---|---|---|---|---|
| AST-040 | primary LGBM v1.1 poza repo | LightGBM text + pickle + JSON | offline training | `ml_inference.py` | version string 1.1; pliki nieśledzone; snapshot SHA-256: model `ef5f3f…c3e7`, encoders `c90a63…1ea`, columns `4eb63e…27f` | ścieżka istnieje; primary influence flag niepotwierdzony, shadow active | pickle code-execution risk; mode 0644 |
| AST-041 | `ml_data_prep/models_twomodel/` | model text/pickle/JSON | offline training | LGBM shadow/router | tracked Git blobs | shadow active; odtwarzalne z Git w zakresie artefaktów | trusted provenance konieczne dla pickle |
| AST-042 | ETA residual model v1/v2_drop poza repo | model artifacts | offline calibration | ETA correction/shadow | nieśledzone; external metadata | wpływ decyzyjny OFF/niepotwierdzony | provenance/integrity |
| AST-043 | reguły HARD/SOFT i defaults | Python/JSON | code + operator | feasibility/scoring/selection | część Git, część live weights/flags | active; pełne zachowanie niebajtowe | business critical |
| AST-044 | `tools/llm_triage.py` prompts | embedded Python strings | developer | OpenRouter models, opcjonalny Telegram | modele nazwane, brak runtime prompt catalog | dormant/manual; brak harmonogramu | egress/prompt injection; raport może zawierać dane |
| AST-045 | replay/golden/tests | Python/JSON/fixtures | developers/tools | CI-like gates/reviewer | 541 test files, audit artifacts | active; trust różny, część live-read | część fixtures zawiera PII/aliasy |
| AST-046 | Audit360 findings/tool trust | JSON/CSV/Markdown | audit workflow | reviewer/backlog | baseline `70af4fa`, validator | historical/partially current | zredagowane, ale opsec |
| AST-047 | tracked duży calibration JSONL | JSONL ~5.6 MB | offline calibration | eval/research | Git blob, konkretna data | historical dataset; użycie live niepotwierdzone | możliwe IDs/operacyjne; treści nieotwierane |

## Backup, obserwowalność i dashboardy

| ID | Aktywum / lokalizacja | Format | Producer / writer | Consumer | Wersja/schema/provenance | Użycie i odtwarzalność | Prywatność / integralność |
|---|---|---|---|---|---|---|---|
| AST-050 | dispatch restic backups | snapshot/off-site | timer + live script | restore/sentinel | job exit 0; live script starszy niż source | active backup, restore unproven | credentials oddzielne, nieczytane |
| AST-051 | state snapshots/panel/Papu backups | snapshot/dump/restic | timery | recovery | ostatnie joby success | active; pełny DR nieudowodniony | szeroki zakres danych |
| AST-052 | backup sentinel | service/report | timer | operator/monitor | live == repo source byte-for-byte | active; freshness/integrity, nie restore | opsec |
| AST-053 | Prometheus/Grafana/GlitchTip i lokalne monitory | containers/services/logs | aplikacje/exportery | operator | obrazy częściowo pinned | active; dashboard schema rozproszona | telemetry/opsec |
| AST-054 | stage timing sidecar | JSONL 0600 | observability instrumentation | canary/verdict | jawny secure writer | active observation | aggregate performance |

## Podsumowanie metadanych i retencji

- `runtime:state`: 470 plików / około 2,51 GB; 252 mode `0644`, 211 mode `0600`.
- `runtime:logs`: 195 plików / około 876 MB; 192 mode `0644`.
- world record: 7 plików / około 942 MB, wszystkie `0644`, retencja 14 dni.
- event bus: processed około 48 h, audit około 90 dni; pełny `NEW_ORDER` około 2 dni.
- główne modele zewnętrzne i residual ETA nie mają pełnego Git provenance.
- dostęp read-only do schema i metadanych był bezpieczny; dostęp do rekordów został celowo pominięty.

## Luki provenance/replay

1. Brak wspólnego release manifestu kod+flags+env+weights+models+schema+images.
2. Brak atestacji loaded code dla aktywnych PID.
3. Retencja world/event/log nie daje pełnej historii.
4. Whole-map GPS/ground-truth nie zachowuje historycznych wersji.
5. Operator corrections mają użyteczne linkage, ale historyczna kompletność nie została dowiedziona.
6. Backup obejmuje szeroki zakres, ale restore i zewnętrzny magazyn poświadczeń nie zostały potwierdzone.
