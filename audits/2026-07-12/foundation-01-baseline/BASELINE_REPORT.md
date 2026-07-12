# Prompt 01 — baseline i rozpoznanie read-only

Status: **PARTIAL**

Zamrożony kod: `c7de9f29127851a59507fac92d14f328336afe61`

Gałąź audytowa: `codex/audit-prompt-01-20260712T140957Z`

Początek obserwacji: `2026-07-12T14:09:57Z`

Klasy twierdzeń: **FAKT**, **WNIOSEK**, **HIPOTEZA**, **NIEWIADOMA**.

## 1. Streszczenie wykonawcze

Baseline kodu został zamrożony w osobnym worktree bez przenoszenia zmian użytkownika. Kod, dokumentacja, sąsiednie projekty, aktywne usługi, konfiguracja na poziomie nazw i fingerprintów, schematy baz, metadane danych operacyjnych, automatyzacja hosta, backup i rollback zostały zbadane w trybie read-only. Nie odczytano wartości sekretów, chronionego rejestru nazw, surowych rekordów GPS/adresów ani treści decyzji produkcyjnych i nie skopiowano pełnych danych osobowych.

Wynik to `PARTIAL`, ponieważ nie istnieje jeden wersjonowany artefakt pozwalający odtworzyć efektywne zachowanie. Uruchomione procesy interpretują kod z kilku checkoutów hosta, a część flag, konfiguracji, wag, modeli i stanu leży poza Git. Zależności są tylko częściowo zadeklarowane, obrazy kontenerowe nie zawsze są przypięte digestem, a sukces backupów nie został potwierdzony realnym restore’em. Z drugiej strony mapa źródeł wiedzy jest wystarczająca, aby bezpiecznie rozpocząć późniejszy, nadal read-only etap rekonstrukcji North Star i kanonu.

### Ledger głównych findings

Okno obserwacji poniższych komend: `2026-07-12T14:09:57Z–14:45:11Z`; ukończone wskazane odczyty zakończyły się exit `0`. Jedna próba metadata-only na chronionej ścieżce env została zablokowana przez guard przed odczytem i jest odnotowana w CMD-002.

| ID | Kategoria / status | Ważność | Pewność | Dowód | Wpływ | Co pozostaje nieznane |
|---|---|---|---|---|---|---|
| P01-GIT-001 | Git / FAKT | MEDIUM | wysoka | CMD-001; frozen `c7de9f2`, później source `c1b576e`; trzy SHA dirty zgodne | baseline jest stabilny, ale współdzielony master zmienił się równolegle | przyszłe zmiany innych sesji |
| P01-REP-001 | reprodukcja / FAKT+WNIOSEK | CRITICAL | wysoka | `common.py:12-17,65-105`; `dispatch_pipeline.py:67-93`; `ml_inference.py:37-47`; `plan_manager.py:32-33`; CMD-002 | sam HEAD nie odtwarza decyzji | historyczne config/flags/weights/models |
| P01-RUN-001 | runtime / FAKT+NIEWIADOMA | HIGH | wysoka dla braku ID | CMD-006, PID/NRestarts/ExecStart; `systemd/README.md:3-12`; brak embedded SHA | nie można dowieść dokładnego kodu w pamięci procesu | loaded modules i dirty state przy starcie |
| P01-DEP-001 | zależności / FAKT | HIGH | wysoka | dwa manifesty i ich SHA; `tools/dependency_inventory.py:127-135`; CMD-005 exit 0 | rebuild venv może różnić się mimo zielonego `pip check` | pełny graph wszystkich venv/timerów |
| P01-TEST-001 | testy / FAKT | MEDIUM | wysoka | `conftest.py:18-21,40-81`; `docs/HERMETIC_TESTS.md:22-38`; wcześniejszy raport `GRF02...:64-74` | brak nowego niezależnego runu Prompt 01 | pełna suita z globalnym network deny |
| P01-DATA-001 | dane/replay / FAKT | HIGH | wysoka | CMD-011 schema-only; `world_record.py:1-18,55-58,203-243`; `event_bus.py:406-562`; `docs/eta/06...:89-112` | pełny historyczny replay poza retencją jest niemożliwy z odkrytych źródeł | zewnętrzne archiwum z provenance |
| P01-SEC-001 | privacy/permissions / FAKT | HIGH | wysoka | CMD-002 metadata-only: state 470/2,51 GB, 252×0644; logs 195/876 MB, 192×0644; `core/jsonl_appender.py:49,112-124` | lokalny odczyt danych GPS/decyzji jest zbyt szeroki | prawa we wszystkich rotacjach/backupach |
| P01-NET-001 | host boundary / FAKT+NIEWIADOMA | HIGH | wysoka lokalnie | CMD-008; INPUT ACCEPT, pusty DOCKER-USER, listenery all-interface | port debug `9222` byłby krytyczny przy braku filtra dostawcy | faktyczna ekspozycja z Internetu |
| P01-DR-001 | backup/restore / FAKT | HIGH | wysoka | CMD-006 status jobów; SHA live/source `5f5615…e9e8` vs `565df5…007d`; sentinel `df2c91…8deb`; DR1A card | backup success nie dowodzi odzyskania; heavy-op lock source-only | realny restore, RTO/RPO, credential store |
| P01-KNOW-001 | wiedza/kanon / FAKT | HIGH | wysoka | ADR-001, ADR-008, registry flag, mapy i aktualna instrukcja; CMD-001/002 | drift dokumentacji może prowadzić do błędnej semantyki | trigger/exit alarmu i kompletność bliźniaków |
| P01-TOOL-001 | tooling / FAKT | HIGH | wysoka | `tools/llm_triage.py:1-7,55-89,176-183,217-297`; legacy migration | etykiety offline/read-only/dry-run mogą uruchomić egress lub zapis | inne niesprawdzone tryby narzędzi |

## 2. Zakres i ograniczenia

Objęto:

- repo `dispatch_v2` na zamrożonym commicie;
- powiązania z panelem Nadajesz, `courier_api`, aplikacją kuriera i nadrzędnym workspace;
- lokalny host, systemd, timery, kolejkę `at`, procesy, listenery i kontenery — wyłącznie przez zapytania odczytowe;
- schematy SQLite przez URI `mode=ro` i `query_only`, bez odczytu rekordów;
- metadane live state i logów: typ, rozmiar, prawa, mtime i rola, bez treści;
- mapy, ADR-y, kanon, pamięć, backlog, handoffy, audyty i reprezentatywną historię Git;
- definicje build/test/deploy/migracji oraz ich skutki uboczne.

Nie objęto treści sekretów i plików zabronionych, surowych danych produkcyjnych, zewnętrznych kont chmurowych, provider firewall, realnego restore’u, zewnętrznego skanu portów ani pełnej treści 843 historycznych plików `eod_drafts/`. Pełnej suity testowej nie uruchomiono w tym audycie z powodów opisanych w sekcji 10.

Konflikt instrukcji został rozstrzygnięty jawnie: starszy globalny protokół wymaga aktualizacji i pushu repo pamięci, lecz aktualny Prompt 01 zabrania zmiany oryginalnych repozytoriów i pushu oraz zezwala wyłącznie na artefakty audytu. Zastosowano najnowsze polecenie: nie zmieniono backlogu ani pamięci i nie wykonano pushu.

## 3. Git i ochrona zmian użytkownika

### Stan początkowy

- repo źródłowe: bezpieczny alias `repo:dispatch_v2`;
- branch `master`, upstream `origin/master`, ahead/behind `0/0`;
- `HEAD=c7de9f29127851a59507fac92d14f328336afe61`;
- trzy zastane modyfikacje tracked, zero untracked i zero submodułów;
- chronione ścieżki i ich początkowe SHA-256:
  - `ZIOMEK_BACKLOG.md`: `6984e790c1380a8432f022d87fcb8ccbd90fc068117bc5afb74c54dd459ee4a9`;
  - `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`: `d418c6d6daf045d9efbab06ad014f4d8d397ddd868d96a5bf6bf4991f581a7bb`;
  - `eod_drafts/2026-07-12/GRF02_TMUX50_CLOSE.md`: `02320c912aaaea0728ee082d1011eb4e33099eeb9f84eeb7a4da22e9ffddfad7`.

Utworzono lokalny worktree `/root/codex_audit_prompt01_20260712T140957Z` i gałąź `codex/audit-prompt-01-20260712T140957Z` z dokładnie tego commita. Niezacommitowanych zmian nie przenoszono.

### Równoległa zmiana

Po zamrożeniu inna sesja przesunęła `master` do `c1b576e96365fc6895c0d8b1f3d35a8185a78870`, commitując wyłącznie dokumentację GRF-02. To potwierdzone przez czas commita, zmianę HEAD i aktywne sesje `tmux`; nie było skutkiem tego audytu. Baseline pozostaje celowo na `c7de9f2`, aby był deterministyczny. Końcowe porównanie znajduje się w sekcji 20.

Repo nie ma submodułów, Git LFS ani `.gitattributes`; na końcu skanu istniało 41 worktree łącznie z audytowym. Sanitizowana lista alias+branch+HEAD znajduje się w `BASELINE_MANIFEST.json` pod `worktree_inventory`; dirty każdego obcego worktree nie był odpytywany, aby nie kolidować z aktywnymi sesjami. Sąsiednie repozytoria miały własne zastane zmiany; nie dotknięto ich i w raporcie zredagowano nazwy mogące ujawniać identyfikatory operacyjne.

## 4. Mapa repozytorium

**FAKT:** zamrożony checkout ma 2 134 tracked pliki i 34 122 583 bajty. Dominujące obszary to `eod_drafts/` (843 pliki), `tests/` (569), `tools/` (231), `docs/` (110), `systemd/` (58). Dominują Python (1 159) i Markdown (655), dalej JSON, TXT, unity systemd, shell i JSONL.

Główne warstwy:

| Obszar | Rola | Stan |
|---|---|---|
| `core/`, `dispatch_pipeline.py`, `feasibility_v2.py`, `route_simulator_v2.py`, `plan_recheck.py` | decyzja, HARD/SOFT, plan i recheck | kod żywy |
| `shadow_dispatcher.py`, `panel_watcher.py`, `sla_tracker.py`, `gps_server.py` | daemony runtime | kod żywy |
| `identity/`, `daily_accounting/`, `reconciliation/`, `shift_notifications/` | domeny pomocnicze | kod żywy |
| `tools/`, `monitoring/`, `migrations/` | diagnostyka, replay, ewaluacja, operacje | mieszane skutki uboczne |
| `systemd/`, `deploy/`, `deploy_staging/`, `docs/deploy/` | źródła i staged deployment kits | nie są pełną prawdą wdrożenia |
| `tests/`, root `conftest.py` | duża suita i hermetic guard | częściowa izolacja |
| `docs/`, `audits/`, `eod_drafts/` | wiedza bieżąca, historyczna i dowodowa | wymaga hierarchii zaufania |

To nie jest formalny monorepo/workspace. Zachowanie zależy jednak od sąsiednich repozytoriów i katalogów hosta: panel Nadajesz, `courier_api`, aplikacja kuriera, workspace OpenClaw, repo pamięci i zewnętrzne modele.

## 5. Stack i zależności

- Ubuntu 24.04.4 LTS, Linux `6.8.0-117-generic`, x86_64;
- Git `2.43.0`, Bash `5.2`, systemd `255`, Docker CLI `29.3.1`;
- Python dispatch `3.12.3`, pip `24.0`, pytest `9.0.3`;
- runtime potwierdzony przez istniejący SBOM: numpy `2.4.4`, pandas `3.0.2`, OR-Tools `9.15.6755`, LightGBM `4.6.0`, scipy `1.17.1`, scikit-learn `1.8.0`, PyYAML `6.0.3`.

Jedyne manifesty w repo to:

- `requirements-dispatch-venv.txt`, 9 dokładnych pinów, SHA-256 `08c78a7e16e7ce6a8c65080d8755476e318e43f5e6fe01836834a993b008524b`;
- `tools/eta_calibration/requirements.txt`, 4 dolne ograniczenia bez górnych pinów, SHA-256 `98e6f012dfddb93b8ccb060b4c0e5483b3c8a2f4e3b81b3b84b824d4d2f84e26`.

Brak lockfile, formalnego packagingu, manifestu narzędzi/testów, obrazu OS i kompletnej deklaracji wszystkich venv. `A360_DEP0_SBOM.json` obejmuje 6 procesów i 2 venv; jego generator błędnie nazywa każdy zainstalowany pakiet spoza manifestu „transitive”, więc `unmanaged=0` nie jest dowodem kompletności (`tools/dependency_inventory.py:127-135`).

## 6. Entrypointy i procesy

Główne wejścia kodowe:

- `shadow_dispatcher.py` — pętla decyzji;
- `panel_watcher.py` — polling panelu i lifecycle;
- `gps_server.py` — serwer GPS; POST mutuje stan;
- `sla_tracker.py` — pętla SLA;
- `monitoring/detector_419.py` — monitor;
- `plan_recheck.py`, reconciliation, pending-pool, accounting, Sheets i shift notifications — one-shot lub timery.

Kod jest interpretowany bez osobnego artefaktu build. Import wymaga parent directory pakietu w `PYTHONPATH`/`ZIOMEK_SCRIPTS_ROOT`.

Snapshot usług aktywnych podczas audytu:

| Usługa | Stan | PID / restarty | Źródło procesu |
|---|---|---|---|
| dispatch-shadow | active/running | `573430` / `0` | venv dispatch, `-m dispatch_v2.shadow_dispatcher` |
| panel-watcher | active/running | `3659486` / `0` | venv dispatch, checkout `dispatch_v2` |
| SLA tracker | active/running | `2998575` / `0` | venv dispatch |
| GPS | active/running | `534721` / `0` | venv dispatch |
| monitor 419 | active/running | `534903` / `0` | venv dispatch |
| courier-api | active/running | `925329` / `0` | osobny checkout i venv |
| nadajesz-panel | active/running | `2028171` / `0` | osobny checkout |

**WNIOSEK:** chwila startu procesu względem historii Git pozwala zawęzić wersję źródła, ale nie dowodzi dokładnego kodu załadowanego do pamięci. Brak wbudowanego build ID; repo mogło być dirty podczas startu.

## 7. Środowiska i runtime

`/etc/systemd/system` jest prawdą wdrożeniową, a repo jedynie niepełnym mirrorem (`systemd/README.md:3-12`). Flagi istnieją w trzech światach: silnik, panel oraz courier API. Audyt odczytał strukturę i bezpieczne wartości wybranych flag silnika, lecz nie czytał plików env ani sekretów.

Fingerprint silnika:

- `flags.json`: mode `0600`, 280 kluczy, SHA-256 `568436…848bf`;
- fingerprint procesu potwierdza `USE_V2_PARSER=true`, `ENABLE_AUTO_ASSIGN=false`, `ENABLE_STAGE_TIMING_OBSERVATION=true`, `ENABLE_CLAIM_LEDGER_INVARIANT_CHECK=true` i `ENABLE_ENGINE_CLAIM_LEDGER=true`;
- wyłącznie plik `flags.json` potwierdza `ENABLE_ORTOOLS_DET_TIME_LIMIT=true` oraz `ENABLE_ETA_LOAD_AWARE=false`; nie udaje to efektywnej wartości każdego procesu;
- efektywnych wartości panelu nie potwierdzono z plików `EnvironmentFile`, bo ich odczyt jest zabroniony; `systemctl show Environment` nie obejmuje ich treści.

Health parsera na `localhost:8888` zwrócił HTTP success i `healthy`, parser v2, brak anomalii, zero błędów i zero pending w chwili odczytu. To punktowy health, nie test całego dispatchu.

Aktywna kolejka `at` zawierała jeden werdykt obserwacyjny na 13 lipca. Dwie sesje `tmux` prowadziły równoległe prace. `dispatch-telegram` pozostaje celowo wyłączony; nie potraktowano go jako awarii.

Punktowy snapshot pokazał 134 enabled timers i 31 aktywnych wpisów crona. Wśród zastanej automatyzacji jest godzinny push repo oraz skrypt uruchamiany z historycznego `eod_drafts/`; audyt ich nie uruchamiał ani nie modyfikował. Większość głównych usług dispatch/panel/API działa jako root, co zwiększa blast radius procesu.

## 8. CI/CD i wdrożenia

Repo nie zawiera GitHub Actions, GitLab CI ani innego formalnego pipeline’u. Hostowe timery, night guard i checkery pełnią rolę operacyjnych bramek, ale działają na współdzielonym hoście i mogą pisać logi, state lub wykonywać akcje produkcyjne. Nie są hermetycznym CI.

Repo nie ma Dockerfile, Compose, Kubernetes ani Terraform. Działa kilka kontenerów, w tym obrazy oznaczone `latest` i obrazy bez jednoznacznego digestu. Stan systemd bywa tworzony ręcznie; część głównych unitów daemonów nie ma tracked odpowiednika. Deploy i staged kits są rozproszone między `systemd/`, `deploy/`, `deploy_staging/` i `docs/deploy/`.

Nie wykonano deployu, restartu, `daemon-reload`, flipa flagi ani smoke’a mutującego produkcję.

## 9. Bazy, kolejki, cache i integracje

Live state miał około 2,51 GB (około 2,34 GiB), a logi około 876 MB. Zbadano wyłącznie metadane i schematy.

| Aktywum | Format / rola | Kontrakt i odtwarzalność |
|---|---|---|
| `events.db` | SQLite: event bus, audit, processed | retry/DLQ schema i indeksy live; auto-retry OFF, brak workera |
| `eta_calib.db` | SQLite: cechy i cache OSRM | schema istnieje; dane dynamiczne |
| `fleet_analytics.db` | SQLite v5 | metryki order/courier/vehicle |
| `courier_api.db` | SQLite v7 | sesje, GPS, PIN/status; wysoka wrażliwość |
| `orders_state.json`, `courier_plans.json`, pending pools | JSON state | live, poza Git; część zapisów atomowa |
| geocode/OSRM caches | JSON/SQLite i lokalny OSRM | dynamiczne; provenance częściowe |
| `shadow_decisions.jsonl`, SLA, outcomes, learning, world record | JSONL/JSON | rotacje i różne retencje; niepełny replay historyczny |
| Google Sheets, Telegram, OpenRouter, panel/courier API, OSRM | integracje | różne venv i granice bezpieczeństwa |

Migracja retry/DLQ jest już obecna w live `events.db`: 16 kolumn i indeksy `idx_events_retry_due` oraz `idx_events_dead_letter`. `event_retry.AUTOMATIC_RETRY_ENABLED=False`, brak unitu automatycznego retry, a requeue wymaga jawnego confirm. To schema i diagnostyka, nie aktywny automatyczny recovery.

## 10. Build i baseline testów

Nie ma osobnego builda produktu; środowisko uruchamia moduły Python bezpośrednio. Canonical test command to:

```text
/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q
```

Nie wykonano jej w Prompt 01. Powody są dowodowe, nie czasowe:

1. DEFAULT uruchamia kwarantannę live-read; root guard blokuje tylko wybrane korzenie zapisu.
2. Subprocess guard jest jawnie fail-open i ma opt-out (`conftest.py:46-67`).
3. Guard nie ustanawia globalnej blokady sieci/chmury.
4. Na hoście trwał 48-godzinny pomiar stage timing, którego rejestr wskazuje host-load jako kontaminację.
5. Dwa pełne przebiegi na kodzie zgodnym z zamrożonym baseline’em zakończyły się bezpośrednio przed audytem: oba `5152 passed / 27 skipped / 8 xfailed / 0 failed`, drugi w `13:53:34–13:58:19Z` (`eod_drafts/2026-07-12/GRF02_TMUX50_CLOSE.md:64-74`).

Nie uruchomiono również focused testu: nie znaleziono reprezentatywnego przypadku z dowiedzioną izolacją importów i sieci, a niedawny pełny baseline dawał szerszy, choć odziedziczony sygnał bez dodatkowego obciążenia hosta.

To jest **odziedziczony świeży baseline**, nie wynik uruchomiony przez ten audyt. `pip check` w venv dispatch wykonano read-only i zwrócił exit `0`, ale nie dowodzi kompletności manifestów.

## 11. Stan odtwarzalności

| Element | Ocena | Dowód |
|---|---|---|
| kod `dispatch_v2` | częściowo odtwarzalny | dokładny commit, ale brak tagu na HEAD i możliwy dirty runtime |
| Python core | częściowo | 9 pinów, pozostałe zależności niepełne |
| panel/API/apka/Sheets | częściowo/nie | osobne repo/venv, brak jednego locku i release manifestu |
| flagi i env | niepełne | live fingerprint tylko części procesów; env files poza raportem |
| modele | niepełne | two-model tracked; primary v1.1 i residual ETA poza Git |
| reguły/wagi | niepełne | `rule_weights.json` i część configu w live state |
| state i decyzje | niepełne | dynamiczne pliki, rotacje i retencja; whole-map GPS niewersjonowane |
| systemd/infra | niepełne | `/etc` jest prawdą; repo to mirror, brak IaC |
| kontenery/OS | niepełne | mutable tags i brak pełnego obrazu hosta |

**WNIOSEK:** można odtworzyć źródła na `c7de9f2` i część venv, ale nie można bajt-w-bajt odtworzyć decyzji ani całego wdrożenia z samych wersjonowanych artefaktów.

## 12. Backup i rollback

Timery `dispatch-restic` i `backup-sentinel` zakończyły ostatni bieg sukcesem; również panel/state/Papu mają aktywne backupy lub snapshoty. To dowód wykonania jobów, nie dowód restore’u.

Live `backup_restic.sh` (`5f5615…e9e8`) różni się od nowszego źródła w `docs/deploy/ha-lite/backup_restic.sh` (`565df5…007d`); aktualizacja źródła z blokadą ciężkich operacji nie jest wdrożona. Sentinel live i source mają identyczny hash `df2c91…8deb`. DR1B, realny restore, RTO i RPO pozostają `HOLD`/niewiadome. `.secrets` jest świadomie wykluczony z restic, więc pełne odtworzenie zależy od niezweryfikowanego zewnętrznego magazynu poświadczeń. Nie istnieje dowód, że rollback obejmuje spójnie kod, schemat, dane, konfigurację, modele i flagi.

Tagi produkcyjne istnieją dla wybranych rolloutów, lecz żaden pojedynczy tag nie opisuje wszystkich uruchomionych procesów i zewnętrznych artefaktów. Rollback przez flagę/revert jest opisany dla wielu zmian, ale nie został w tym audycie wykonany ani uznany za potwierdzony.

## 13. Lokalizacja wiedzy projektowej

Najwyższe bieżące kandydatury:

- instrukcje: `/root/AGENTS.md`, workspace `AGENTS.md` jako źródło konfliktowe oraz `CLAUDE.md:1-36` jako bieżąca głowa;
- mapy: `docs/CODEMAP.md`, `docs/ARCHITECTURE.md`, `ZIOMEK_ARCHITECTURE.md`;
- kontrakty: `ZIOMEK_INVARIANTS.md`, `ZIOMEK_DEFINITION_OF_DONE.md`, ADR-y;
- kanon biznesowy: alias `memory:ZIOMEK_REGULY_KANON.md`, zawsze weryfikowany kodem/runtime;
- bieżące wykonanie: `ZIOMEK_BACKLOG.md`, `memory:todo_master.md`, top `memory:sprint_timeline.md` i handoffy;
- operacje cross-project: `/root/handover/MAPA_WIEDZY.md`, `/root/handover/CO_TRZEBA_ZROBIC.md`;
- niezależny materiał dowodowy: `audits/2026-07-10/full-system-360/` i `eod_drafts/2026-07-11..12/`.

Pełna klasyfikacja i konflikty są w `KNOWLEDGE_SOURCE_REGISTER.md`.

## 14. Lokalizacja wiedzy operacyjnej

- aktywne usługi i drop-iny: systemd na hoście;
- efektywne flagi silnika: `flags.json` + journal fingerprint;
- live state: alias `runtime:dispatch_state`;
- logi decyzji: alias `runtime:logs/shadow_decisions.jsonl`;
- health parsera: udokumentowany lokalny endpoint;
- timery/werdykty: systemd timers, `atq`, `shadow-jobs-registry.md`;
- decyzje o rollout/rollback: top handoffów, karty EOD i tagi Git.

Dokumenty są mapą, nie substytutem runtime. Historyczna część `CLAUDE.md`, stare tabele flag i stare numery linii są jawnie niekanoniczne.

## 15. GPS, ETA, trasy, decyzje i korekty

Zlokalizowano na poziomie schema/metadanych:

- GPS live i historię: API GPS, `courier_last_pos`, `gps_history`, ground-truth/geofence i walidację dostaw;
- ETA: SLA/shadow, kalibrację, residual models, load-aware replay, OSRM cache i ground-truth contract;
- trasy: plan, route simulator, plan recheck, world record/replay;
- decyzje: kandydaci i finalna decyzja w shadow JSONL, outcomes/event bus, pending proposals;
- korekty operatorów: akcje panelu i assignment anchors (`PANEL_OVERRIDE`, `PANEL_AGREE`, inne jawne typy), bez odczytu rekordów;
- wyniki: SLA, delivery validation, learning log, analytics i health scoreboards.

`docs/eta/06_ground_truth_contract.md:8-24,40-49,89-112` potwierdza, że nie ma kompletnego historycznie wersjonowanego physical pickup/handoff. Whole-map GPS/GT ma `snapshot_reconstructability=false`; arrival i klik są proxy o ograniczonej semantyce. Pełny historyczny replay decyzji nie jest obecnie gwarantowany.

## 16. Największe ryzyka

1. **P01-REP-001 / CRITICAL:** zachowanie zależy od niewersjonowanych konfiguracji, wag, modeli i state.
2. **P01-RUN-001 / HIGH:** brak dokładnego build ID i jednolitego release manifestu dla uruchomionych procesów.
3. **P01-DR-001 / HIGH:** backupy biegną, ale restore i pełny rollback nie są udowodnione.
4. **P01-DATA-001 / HIGH:** retencja i niewersjonowane whole-map aktywa uniemożliwiają pełny replay historyczny.
5. **P01-SEC-001 / HIGH:** liczne live logi/DB/aktywa z danymi operacyjnymi mają prawa `0644`; `courier_api.db` zawiera wrażliwe klasy pól.
6. **P01-NET-001 / HIGH:** host ma liczne listenery na wszystkich interfejsach, a skutecznego provider firewall nie zweryfikowano.
7. **P01-DEP-001 / HIGH:** deklaracje zależności i SBOM są niepełne, a część obrazów jest mutowalna.
8. **P01-KNOW-001 / HIGH:** kanon, ADR-y, flag inventory i mapy mają potwierdzone rozbieżności.
9. **P01-TOOL-001 / HIGH:** nazwy „offline”, „read-only” i „dry-run” nie gwarantują braku efektów ubocznych.
10. **P01-TEST-001 / MEDIUM:** suita ma mocny FS guard, ale nie jest globalnie odcięta od sieci i ma fail-open w subprocesach.

Dodatkowo `P01-PRIV-001 / HIGH`: większość daemonów działa jako root; oraz `P01-AUTO-001 / MEDIUM`: zastane timery/cron kontynuują autonomiczne operacje, w tym push, niezależnie od audytu.

`P01-SECRETS-001 / MEDIUM`: sam katalog magazynu sekretów ma mode `0755`, więc lokalni użytkownicy mogą poznać nazwy/metadane wpisów; treści i prawa plików wewnętrznych nie były czytane, dlatego nie wolno wnioskować o dostępie do wartości.

## 17. Największe niewiadome

- dokładne commity i dirty fingerprint kodu załadowanego przez każdy aktywny proces;
- historyczne wartości wszystkich env/flag/modeli/wag dla konkretnej decyzji;
- pełna lista zależności wszystkich venv, timerów, kontenerów i system packages;
- skuteczność provider firewall z perspektywy zewnętrznej;
- RTO/RPO i wynik realnego restore’u produkcyjnego backupu;
- wersjonowana prawda physical pickup i customer handoff;
- kompletna historia decyzji poza retencją i rotacjami;
- bezpieczny, całkowicie network-denied wariant pełnej suity;
- właściciele części historycznych dokumentów i niektórych ręcznych unitów.

## 18. Sprzeczności do przyszłej weryfikacji

- ADR-001 i część `ZIOMEK_ARCHITECTURE.md` opisują R6 jako `35/40 tier-aware`; aktualna jawna instrukcja rozstrzyga `35 normalnie / 40 tylko alarm/ratunek, nigdy klasa kuriera`. Dokumentacja i miejsca kodu mają więc drift; nierozstrzygnięte pozostają trigger/wyjście alarmu i kompletność bliźniaków.
- ADR-008 mówi, że fizycznego rdzenia nie przeniesiono; nowsze `CODEMAP` i `ARCHITECTURE` opisują żywe `core/` po refaktorze 06.07.
- `docs/flags/INVENTORY_2026-07-10.md` zawiera drift `USE_V2_PARSER`; nowszy rejestr i runtime pokazują drift zamknięty i `true`.
- `ZIOMEK_LOGIC_REFERENCE.md` przedstawia się jako current, lecz zawiera historyczne statusy/test counts i wymaga weryfikacji.
- dokumenty systemd/deploy opisują stany, które zmieniły się na hoście; `/etc` jest rozstrzygające.
- `tools/llm_triage.py` opisuje się jako offline/read-only i bez Telegramu, podczas gdy kod wykonuje sieć, zapis i opcjonalny Telegram.

Nie rozstrzygano semantyki biznesowej ani nie naprawiano tych konfliktów.

## 19. Gotowość do Promptu 02

**READY — wyłącznie dla kolejnego etapu read-only.** Zidentyfikowano źródła prawdopodobnego North Star i kanonu, hierarchię zaufania oraz konflikty. Prompt 02 powinien:

1. zachować oddzielnie `zamierzone`, `zaimplementowane` i `efektywne live`;
2. używać kanonu pamięci jako kandydata, nie samowystarczalnej prawdy;
3. przyjąć rozstrzygnięte `R6=35 normalnie / 40 alarm, nigdy klasa kuriera` oraz ustalić tylko trigger/wyjście alarmu, precedencję i kompletność implementacji;
4. nie używać starych promptów, `CLAUDE.md` body ani pojedynczego audytu jako automatycznego kanonu;
5. nie przechodzić do implementacji, flipa ani deployu bez osobnego zakresu i ACK.

## 20. Wynik i kontrola końcowa

Wynik: **PARTIAL**.

Powód: baseline kodu i środowiska jest wiarygodnie sfingerprintowany, ale kluczowych elementów wdrożenia i historycznej decyzji nie można obecnie odtworzyć, a równoległa sesja legalnie zmieniła oryginalny `master` po zamrożeniu. Audyt nie może więc zadeklarować `PASS` ani globalnie twierdzić, że cały współdzielony host pozostał niezmieniony.

Końcowa kontrola potwierdziła:

- o `2026-07-12T15:00:36Z` oryginalny repo był na `c1b576e`, a dirty pozostał tylko pierwotnie chroniony claim-ledger; wszystkie trzy początkowe SHA treści nadal były identyczne;
- oryginalny repo pozostał nietknięty **przez audyt**, przy jawnie odnotowanej równoległej zmianie dokumentacji;
- audyt nie wykonał operacji mutujących ani sieci poza loopback i nie wykonał pushu, merge, deployu, migracji, restartu ani flipa; wykonał lokalne zapytania read-only do systemd/Docker oraz parser-health GET, który mógł wygenerować access log/metrykę;
- zastane zmiany użytkownika nie zostały nadpisane, stashowane ani skopiowane;
- jedyne zapisy to metadane/worktree/branch Git, osiem plików tego katalogu audytu i wymagany awaryjny snapshot `/tmp/codex_handoff_2026-07-12_1500_prompt01_baseline.md`;
- manifest i 82 rekordy JSONL przechodzą parser, cached diff-check jest zielony, ścieżki istnieją, a skan sekretów/PII nie znajduje wartości wrażliwych;
- niezależne review repo/build, wiedzy/historii i runtime/security zakończyły się `PASS_AFTER_CORRECTIONS`.

Szczegóły komend, aktywów, ryzyk i bramy znajdują się w pozostałych plikach pakietu.
