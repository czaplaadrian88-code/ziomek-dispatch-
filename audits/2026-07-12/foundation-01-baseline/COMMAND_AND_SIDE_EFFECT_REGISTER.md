# Rejestr komend i skutków ubocznych

Klasy: `SAFE_READ_ONLY`, `SAFE_ISOLATED_WRITE`, `POTENTIALLY_MUTATING`, `PRODUCTION_MUTATING`, `UNKNOWN`. Klasyfikacja dotyczy aktualnego hosta i sprawdzonej implementacji, nie samej nazwy komendy.

## Wykonane komendy read-only

| ID | Źródło / komenda reprezentatywna | Cel i wymagania | Sieć / zapis | Klasa | Wykonano / wynik |
|---|---|---|---|---|---|
| CMD-001 | `git status`, `rev-parse`, `log`, `show`, `describe`, `worktree list`, `ls-files` | branch, SHA, dirty, historia, struktura | brak sieci; tylko odczyt | `SAFE_READ_ONLY` | tak; exit 0 |
| CMD-002 | `rg`, `git grep`, `find` agregacyjny, `nl`, `stat`, `du`, `sha256sum` | mapy, linie dowodu, metadata i fingerprint | brak sieci; treść tylko dozwolonych plików | `SAFE_READ_ONLY` | tak; exit 0; jedna próba ścieżki env zablokowana przez guard |
| CMD-003 | `git worktree add -b codex/audit-prompt-01-… <path> c7de9f2` | odizolować artefakty | zapis tylko metadanych Git i nowego checkoutu | `SAFE_ISOLATED_WRITE` | tak; exit 0; oryginalne dirty nie przeniesione |
| CMD-004 | `git --version`, `python --version`, `pip --version`, `pytest --version`, `docker --version`, `systemctl --version` | toolchain | brak sieci/zapisu istotnego | `SAFE_READ_ONLY` | tak; exit 0 |
| CMD-005 | `/root/.openclaw/venvs/dispatch/bin/python -m pip check` | spójność zainstalowanego venv | brak resolve/install/network | `SAFE_READ_ONLY` | tak; exit 0, no broken requirements |
| CMD-006 | `systemctl show/list-timers/list-unit-files` | status, PID, NRestarts, ExecStart, user, timery | lokalny D-Bus, bez mutacji | `SAFE_READ_ONLY` | tak; exit 0 |
| CMD-007 | `tmux list-sessions`, `atq`, zredagowane `crontab -l`, `ps` z allowlistą PID/user/comm | równoległe sesje i harmonogramy | lokalny odczyt; bez cmdline/env | `SAFE_READ_ONLY` | tak; exit 0 |
| CMD-008 | `ss -ltnp`, `iptables -S`, `ip6tables -S` | listenery i host boundary | lokalny odczyt; brak skanu zewnętrznego | `SAFE_READ_ONLY` | tak; exit 0 |
| CMD-009 | `docker ps` i `docker inspect --format` bez env/labels | obrazy, ID i stan | lokalny socket read-only query | `SAFE_READ_ONLY` | tak; exit 0 |
| CMD-010 | lokalny `GET http://localhost:8888/health/parser` z allowlistą pól | parser health | loopback GET; serwer mógł zapisać access log/metrykę, bez zmiany stanu biznesowego | `SAFE_READ_ONLY` | tak; HTTP success, healthy/v2 |
| CMD-011 | SQLite URI `mode=ro`, `PRAGMA query_only=ON`, schema-only | tabela, kolumny, indeksy, user_version | brak zapisu; bez rekordów | `SAFE_READ_ONLY` | tak; exit 0 dla 4 DB |
| CMD-012 | odczyt bezpiecznych pól JSON/registry/SBOM | liczby, statusy, manifesty | brak sieci; bez wartości sekretów | `SAFE_READ_ONLY` | tak; exit 0 |
| CMD-013 | lokalne parse/regex/path/diff validation artefaktów | integralność pakietu i brak danych wrażliwych | odczyt artefaktów; bez sieci | `SAFE_READ_ONLY` | tak; dwa pierwsze złożone wywołania miały błąd składni quoting i exit 2 przed skanem, rozdzielone kontrole zakończyły się PASS |

## Test, build i checkery

| ID | Definicja / źródło | Cel i wymagania | Potencjalne skutki | Klasa | Wykonano / powód |
|---|---|---|---|---|---|
| CMD-020 | `/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q` | canonical full regression; venv dispatch, poprawny `ZIOMEK_SCRIPTS_ROOT` | temp/cache/subprocess; DEFAULT live-read; brak globalnego network deny; host load | `UNKNOWN` | nie; świeży odziedziczony baseline 5152/27/8/0 dwukrotnie |
| CMD-021 | `HERMETIC_STRICT=1 ... pytest tests/ -q` | strict FS isolation | skip quarantine, lecz subprocess fail-open/opt-out i sieć nieblokowana | `UNKNOWN` | nie |
| CMD-022 | `python -m py_compile ...` | syntax check | standardowo `__pycache__`; bezpieczny tylko z cache w temp | `SAFE_ISOLATED_WRITE` | nie; Prompt 01 nie zmienia kodu |
| CMD-023 | `tools/flag_lifecycle_check.py [--live]` | lifecycle registry | odczyt repo/live; bez pełnej inspekcji importów nie użyto | `SAFE_READ_ONLY` dla sprawdzonego trybu repo, `UNKNOWN` dla `--live` | nie; świeży raport 505/505 odziedziczony |
| CMD-024 | `tools/entropy_dashboard.py` | raport długu/entropii | może czytać wiele źródeł i generować output; istnieją stare baseline’y | `UNKNOWN` | nie |
| CMD-025 | training ML / eta calibration | modele i ewaluacja | zapis modeli/DB/raportów, możliwy OSRM, dane | `SAFE_ISOLATED_WRITE` tylko na dev snapshot; inaczej `POTENTIALLY_MUTATING` | nie |

## Daemony, deploy, migracje i operacje

| ID | Definicja / źródło | Cel i wymagania | Potencjalne skutki | Klasa | Wykonano / powód |
|---|---|---|---|---|---|
| CMD-030 | `python -m dispatch_v2.shadow_dispatcher` | główny dispatcher | state/DB/logi/sieć/alerty/pętla | `PRODUCTION_MUTATING` | nie |
| CMD-031 | `panel_watcher`, `gps_server`, `sla_tracker`, reconciliation/workery | procesy operacyjne | API, state, DB, logi, Telegram/Sheets | `PRODUCTION_MUTATING` | nie |
| CMD-032 | `systemctl start/restart/stop/enable/daemon-reload` | zarządzanie usługami | zmiana live i availability | `PRODUCTION_MUTATING` | nie; brak ACK i twardy zakaz Prompt 01 |
| CMD-033 | deploy/staged kits/rsync/copy do `/etc` lub webroot | wydanie | kod/config/unit live | `PRODUCTION_MUTATING` | nie |
| CMD-034 | zmiana `flags.json`, env drop-in, smoke flip/verdict | feature flags | decyzje/runtime/restart | `PRODUCTION_MUTATING` | nie |
| CMD-035 | `migrations.event_retry_metadata --db X` bez `--apply` | inspect retry schema | SQLite `mode=ro` | `SAFE_READ_ONLY` | nie przez CLI; równoważna schema-only inspekcja wykonana |
| CMD-036 | jw. `--apply` | dodać kolumny/indeksy | `ALTER TABLE`, indeksy, transakcja RW | `PRODUCTION_MUTATING` | nie; schema już live |
| CMD-037 | `migrations/legacy_audit_move_2026_05_07.py --dry-run` | inspekcja/move legacy audit | produkcyjny logger i SQLite RW nawet w dry-run | `POTENTIALLY_MUTATING` | nie |
| CMD-038 | legacy audit move `--apply` | transfer danych | INSERT/DELETE | `PRODUCTION_MUTATING` | nie |
| CMD-039 | `replay_dead_letter.py` bez requeue | diagnostyka DLQ | czyta DB, może drukować wrażliwe identyfikatory/dane operacyjne | `UNKNOWN` | nie; schema wystarczyła |
| CMD-040 | `replay_dead_letter.py --requeue --confirm-requeue` | ręczny replay | zmiana lifecycle eventu | `PRODUCTION_MUTATING` | nie |
| CMD-041 | `tools/flag_lifecycle_seed.py --merge --out ...` | generacja registry | zapis pliku; bez `--merge` niszczy kurację | `POTENTIALLY_MUTATING` | nie |
| CMD-042 | `tools/llm_triage.py` bez `--dry-run` | LLM triage/judge | OpenRouter egress/koszt oraz zapis live JSONL; opcjonalnie Telegram | `PRODUCTION_MUTATING` | nie |
| CMD-042A | `tools/llm_triage.py --dry-run` | LLM triage/judge bez lokalnego append | nadal OpenRouter egress/koszt; z `--telegram` zewnętrzna wiadomość | `POTENTIALLY_MUTATING`; z Telegramem `PRODUCTION_MUTATING` | nie |
| CMD-043 | ETA feature/calibration tools | kalibracja/replay | zapis `eta_calib.db`, outputów; część łączy się z OSRM | `POTENTIALLY_MUTATING` | nie |
| CMD-044 | `backup_restic.sh`, `restic backup/forget/prune` | backup/retencja | off-site write, prune, logi, WAL cleanup | `PRODUCTION_MUTATING` | nie; obserwowano tylko status timera |
| CMD-045 | `restore_from_restic.sh` | restore | dane/DB/Docker/config/decrypt | `PRODUCTION_MUTATING` | nie; DR1B HOLD |
| CMD-046 | night guard, prune, rotation `--apply` | automatyzacja hosta | zapis/kasowanie/logi/alerty | `PRODUCTION_MUTATING` | nieuruchomione przez audyt; zastane timery działają niezależnie |
| CMD-047 | `migrate_couriers --audit` | audit/migracja identity | domyślny Telegram i dane osobowe | `PRODUCTION_MUTATING` w domyślnym trybie | nie |
| CMD-048 | pełny `docker inspect`, `/proc/*/environ`, chronione env | konfiguracja efektywna | może ujawnić sekrety; powód niewykonania jest informacyjny, nie osobną klasą | `UNKNOWN` | nie; jawny zakaz bezpieczeństwa |
| CMD-049 | zastany godzinny cron auto-push | synchronizacja repo | sieć i push do remote; działa autonomicznie poza audytem | `PRODUCTION_MUTATING` | nieuruchomiony przez audyt; definicja tylko zredagowana |
| CMD-050 | zastany cron uruchamiający skrypt z historycznego `eod_drafts/` | niejednoznaczna automatyzacja hosta | skrypt może pisać state/logi lub używać sieci; nie wykonano pełnej treści | `UNKNOWN` | nieuruchomiony przez audyt |
| CMD-051 | zastany at-job `214`, termin 2026-07-13 12:15 UTC | przyszły werdykt obserwacyjny wg registry | skutki komendy niepowtórzone w raporcie; może pisać werdykt/log | `POTENTIALLY_MUTATING` | nieuruchomiony przez audyt |

## Reguła użycia rejestru

Przed późniejszym wykonaniem komendy trzeba ponownie sprawdzić aktualny kod, argumenty, defaults, fixture’y, ścieżki outputu, sieć i efektywne środowisko. Status `SAFE_READ_ONLY` nie przenosi się automatycznie na wariant z innymi flagami ani na przyszły commit.
