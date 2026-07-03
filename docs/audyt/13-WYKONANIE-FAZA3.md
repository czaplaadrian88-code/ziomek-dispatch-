# 13 — LOG WYKONANIA: Faza 2-wdrożenie + Faza 3 (2026-07-03)

**Autoryzacje Adriana:** „akceptuję plan" (Faza 1) → „1234 ack" (merge + P-1..P-6 + kompakt MEMORY + WD-11/12) → „wykonaj P-2, faza 3 wg rekomendacji, tomtom wygasić".

## A. Wykonane

| Krok | Co | Dowód / commit |
|---|---|---|
| K2.8 merge #1 | Warstwa nawigacyjna na master (`542dfa1`) | regresja post-merge **4145/0** |
| P-1 | `workspace/CLAUDE.md` SKASOWANY (router-widmo N5) | backup `CLAUDE.md.bak-pre-audyt-P1-2026-07-03` |
| P-2 | `/root/.claude/CLAUDE.md` — sekcja Ruflo usunięta (N14) | 1. próba zablokowana przez klasyfikator uprawnień; wykonane po jawnym „wykonaj P-2"; backup `.bak-pre-audyt-P2` |
| P-3 | 5 poprawek `/root/CLAUDE.md` (N1/N2/N6/N12 + krok 0 nawigacji) | skrypt z asercjami 5/5; backup `.bak-pre-audyt-P3` |
| P-4 | `ZIOMEK_REGULY_KANON.md` §6 → „3 światy flag" (stary opis zachowany jako historyczny) | 3 podmiany z asercjami |
| P-5/P-6 | fraza flag w MEMORY.md (w kompakcie) + baner todo_master | 2 podmiany |
| K2.6 kompakt | **MEMORY.md 223 644 B → 17 335 B (−92%), 69 linii** — cały indeks ładuje się sesjom | backup `MEMORY.md.bak-pre-shrink-2026-07-03`; 240/240 linków istnieje; przeniesienia C2/C4/C6 dopisane (C1/C3/C5 już pokryte); auto-commit memory `83912f9` |
| WD-2 | **Cron TomTom WYGASZONY** (4 linie GATE B z crontaba roota) | backup `/root/crontab.bak-pre-audyt-tomtom-2026-07-03` (59→55 linii) |
| K3.1-K3.3 | git rm 4 sieroty (`events.db` 0B [kod używa workspace/dispatch_state/events.db], geocoding-wip, foodage-proven-bak, draft courier_resolver 1620 l.) + **untrack 9 plików danych** (tomtom ×4, epaka ×5 — zostają na dysku) + `.gitignore` klasy danych/backupów | `09f132e` |
| K3.5 | usunięcie 4 martwych `.py` (bundle_geo_experiment + verify_obj_f1/f2/f4) — grep referencji=0 (repo+panel+courier_api+systemd+cron) | `14294e4` |
| K3.6b | 4 ciężkie dumpy (~20 MB) → archiwum poza repo `workspace/eod_archive/2026-07-03-audyt-dumps/` | `f4a4dbd` |
| K3.6 | **archiwizacja 30 pozycji → `docs/archive/`** (26 starych docs + AUDIT_2026-05-07 + AUDIT_2026-06-03 + 2 handoffy z korzenia) + README-indeks + aktualizacja wskaźników (CLAUDE.md, CODEMAP ×6, 3 docstringi testów) | `cda1ab7` |
| K3.7 | konsolidacja systemd: `reconciliation/systemd` + `shift_notifications/systemd` → `systemd/<moduł>/` + `systemd/README.md` (mapa źródła↔wdrożone) + instrukcje instalacji zaktualizowane | `66ccb02`; pre-check: 0 symlinków /etc→repo |
| merge #2 | Faza 3 na master (`e621006`) — z konfliktem modify/delete rozstrzygniętym na korzyść untracku; **pliki danych przywrócone na dysk po ff** (backup→ff→restore) | sweep-commit przed merge (house-pattern); status po: czysty |
| K3.4 | czystka żywego drzewa: **172 pliki `.bak-*` >14 dni usunięte (3,6 MB)**, 163 świeże zostają; 25× `__pycache__` w eod_drafts; `.aider.chat.history.md` | lista: scratchpad `bak_to_delete.txt` |
| K3.8 | **7 wystrzelonych one-shot timerów disabled** (objm-smoke-flip/verdict, pending-resweep-review/watchdog, reassignment-shadow-eval, b-route-shadow-review, bundle-calib-review) — wszystkie odpalone, werdykty skonsumowane (rejestr shadow-jobs) | `systemctl disable` ×7 |
| K3.9 | lista 40 zmergowanych gałęzi → **czeka na zatwierdzenie Adriana** (WD-14) | scratchpad `branches_merged.txt` |

## B. Świadome ODSTĘPSTWA od litery planu (z uzasadnieniem)

1. **`docs/deploy/ha-lite/backup_sentinel.py` NIE usunięty/NIE zarchiwizowany** — to źródło ŻYWEGO `backup-sentinel.service` (deployowany jako `scripts/backup_sentinel.py` poza repo; timer codziennie 08:00). Graf B nie widział /etc/systemd. Cały kit `docs/deploy/` zostaje.
2. **`czasowka_proactive/handlers.py` NIE usunięty** — kandydat B z niepewną prowenancją; grep czysty, ale to moduł żywego pakietu → wymaga głębszej weryfikacji (→ rezydua).
3. **`eod_drafts/2026-06-29/calibration/` ZOSTAJE** — udokumentowany trwały artefakt kalibracji (memory ziomek-calibration).
4. **Statyczne dane historyczne w gicie (stare jsonl/log w eod_drafts) ZOSTAJĄ trackowane** — nie churnują; `.gitignore` chroni przed NOWYMI; untrack objął tylko pliki żywo pisane (tomtom/epaka).
5. **Kity staged (`deploy/`, `deploy_staging/`) zostają na miejscu** — spójne zestawy instalacyjne; opisane w `systemd/README.md`.

## C. Test nowej sesji (Definicja ukończenia) — 5/5 ✅ (odpowiedzi WYŁĄCZNIE z CLAUDE.md + docs/CODEMAP.md)

| Pytanie | Odpowiedź z mapy | Weryfikacja |
|---|---|---|
| Gdzie obsługa czasówek? | `czasowka_scheduler.py` + `czasowka_proactive/` (CODEMAP §3) | pliki istnieją ✅ |
| Jak zmienić flagę silnika / sprawdzić flagę panelu? | `flags.json` hot-reload + `common.flag()`; panel: `flags.systemd.env` ⚠ nie przez `systemctl show` (CODEMAP §3 + START TUTAJ + ADR-004) | flags.json istnieje ✅ |
| Gdzie kanoniczny log decyzji? | `scripts/logs/shadow_decisions.jsonl` (CODEMAP §3/§4 pułapka #2) | istnieje ✅ |
| Jak uruchomić testy? | TYLKO `venvs/dispatch/bin/python -m pytest tests/ -q` (START TUTAJ + CODEMAP pułapka #3) | bieg 4145/0 + finalny ✅ |
| Gdzie kanon kolejności trasy? | `plan_manager.py` + `plan_recheck.py` (`_apply_canon_order_invariants`) + 4 handlery recanon w `panel_watcher` (CODEMAP §3) | pliki istnieją ✅ |

## D. Rezydua (świadomie otwarte — poza zakresem audytu)

- **P0 SECURITY** (firewall/porty/rotacja tokenów WD-1) → sprint security AUDYT2 S5 (Adrian).
- **WD-14**: kasacja 40 zmergowanych gałęzi — po zatwierdzeniu listy.
- **R-20/R-21 odłożone**: `schedule_utils.py` do pakietu; `monitoring/`↔`observability/` — osobne sprinty pod #0.
- `dispatch-cod-weekly.service` FAILED co pon. (WD-13, w todo) · `czasowka_proactive/handlers.py` (weryfikacja) · **legacy `@reboot` ŻYJE** (`/root/gps_server.py` PID 1010, `/root/dispatch_control.py` PID 1006 — działają od bootu; relacja do `dispatch-gps.service` do wyjaśnienia — NIE ruszone) · WD-9 rename `dispatch_state/` (README wystarcza na dziś).

## E. Stan testów na koniec

Baseline ruchomy w trakcie audytu (inne sesje dowoziły): 4109/0 (11:47) → 4145/0 (po merge #1) → **finalny bieg po merge #2 (13:3x UTC): **4163 passed, 26 skipped, 11 xfailed, 139 warnings****. Finalny bieg: **2 failed — OBA spoza audytu** (dowody): (a) `test_split_layer_guard_l73::test_flag_gate_reflects_constant` — świeży test lane'u L7.3 innej sesji (commit `0f2b04f`, dziś; istniał na masterze przed merge #2), pada też W IZOLACJI: oczekuje flagi OFF, a `_split_layer_guard_on()`=True (klasa conftest-leak/stan-flagi z protokołu; żadna zmiana audytu nie dotyka flag/dispatch_pipeline) → do domknięcia przez lane L7.3 (notka w trackerze relay); (b) `test_flag_doc_coverage::test_no_new_undocumented_decision_flag` — w izolacji PRZECHODZI (order/cross-suite flaky przy równoległych suitach). Jedyny failed unit systemu: `dispatch-cod-weekly` (pre-existing, znany).
