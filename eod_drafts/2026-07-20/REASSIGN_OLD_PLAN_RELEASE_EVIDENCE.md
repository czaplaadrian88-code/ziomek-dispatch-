# REASSIGN-RELEASE — zwolnienie planu STAREGO kuriera po przerzuceniu (evidence, 2026-07-20)

**Branch:** `fix/reassign-old-plan-release` (baza `master@7e57085` = baseline zadania).
**Zgłoszenie ownera:** „u kurierów są po przerzuceniu opóźnienia w pokazywaniu".
**Deploy:** CIEMNY — flaga `ENABLE_REASSIGN_OLD_PLAN_RELEASE` default OFF (const common.py,
klucza NIE ma w flags.json). Flip wyłącznie za ACK ownera. ZERO live w tej sesji.

## 1. Root cause (ETAP 1 — u źródła)

`panel_watcher._diff_and_emit`, branch reassign (`elif was_assigned and is_assigned_now …`):
po wykryciu zmiany kuriera emituje `COURIER_ASSIGNED(source=panel_reassign)`,
`update_from_event`, `_save_plan_on_assign_signal(zid, panel_courier)` — sygnał idzie
**TYLKO DO NOWEGO** kuriera (u niego `_invalidate_plan_on_bag_change` → bump → SSE →
apka odświeża). Planu STAREGO (`state_courier`) nic nie tyka: jego
`courier_plans/<cid>` dalej zawiera stop, `plan_version` stoi → apka starego pokazuje
zabrane zlecenie aż do fallbacku 180 s (`PlanPoller.FULL_REFRESH_FALLBACK_MS`) albo
5-min `plan_recheck`.

Asymetria względem sąsiadów: deliver (`advance_plan`) i cancel/return
(`_remove_stops_on_return`) robią shrink+recanon poprawnie. Protokół #0 (Załącznik B,
„Recanon"): *„każda tranzycja KURCZĄCA worek (cancel/deliver/reassign-loser) musi wołać
`plan_manager.remove_stops`/`advance_plan` PRZED recanon"* — **reassign-loser był jedyną
kurczącą tranzycją bez tego**. Fix w warstwie zapisu/kanonu (panel_watcher — writer
courier_plans), nie łatka na renderze.

## 2. Zmiana (lustrzana, zero wynalazków)

- **`panel_watcher._release_plan_on_reassign(old_courier_id, order_id)`** — lustrzane do
  `_remove_stops_on_return`: gate `ENABLE_SAVED_PLANS` → gate
  `decision_flag("ENABLE_REASSIGN_OLD_PLAN_RELEASE")` (idiom sąsiada
  `_invalidate_plan_on_bag_change`: najpierw SAVED_PLANS, potem flaga funkcji) →
  `plan_manager.remove_stops` (bump `plan_version` — zweryfikowane plan_manager.py:433/437)
  → best-effort `plan_recheck.recanon_courier(reason="reassign_out")` (samo-bramkuje na
  `ENABLE_RECANON_ON_WRITE`, no-op gdy worek pusty/brak planu). Każdy krok w osobnym
  try/except z `log.warning` — błąd NIGDY nie psuje diff loopu.
- **Call-site #1 (branch reassign):** po sukcesie emit/update, po `_check_panel_agree/override`,
  **PRZED** `_save_plan_on_assign_signal`: `if state_courier and state_courier != panel_courier:`.
- **Call-site #2 (PANEL_PACKS FALLBACK — bliźniak):** przed `_save_plan_on_assign_signal`:
  `if _state_cid and _state_cid != _target_cid:` — guard liczony **PO trust-raw**
  (patrz §4, przypadek krytyczny).
- **Telemetria:** `INFO "REASSIGN-RELEASE cid_old=… oid=… — plan starego zwolniony
  (remove_stops → bump plan_version)"` — spójna z logami sąsiadów (REASSIGNED /
  PACKS_CATCHUP / BUG-1 invalidate / FIX-E), asertowana testem (caplog).
- **Flaga:** `common.py` — wpis w `ETAP4_DECISION_FLAGS` (strip w testach + widoczność
  w `flag_fingerprint`) + stała-fallback `ENABLE_REASSIGN_OLD_PLAN_RELEASE = False`
  (kanon po flipie = flags.json; rollback hot = klucz false/brak klucza).
- **Docs:** wiersz w tabeli flag `ZIOMEK_LOGIC_REFERENCE.md` (uprzedza drift
  flag_doc_coverage po flipie — checker skanuje flags.json, klucz wejdzie tam przy flipie).
- **Rejestr lifecycle:** seeder `tools/flag_lifecycle_seed.py --merge` (2×, kuracja
  zachowana: „MERGE: zachowano pola kuracji dla 506→507 wpisów") + **KURACJA wpisu**:
  heurystyka seedera klasyfikuje po nazwie (`LEGACY|_OLD|DEPREC` → deprecated) — `_OLD_`
  w nazwie znaczy tu „plan STAREGO kuriera", nie legacy → ręcznie `lifecycle=planned`,
  `curated_at=2026-07-19`, `lifecycle_seeded=false`, `owner.service=dispatch-panel-watcher.service`
  (czytelnik biega pod panel-watcherem, nie shadow). Dowód trwałości: ponowny `--merge`
  zachowuje kurację; `tools/flag_lifecycle_check.py` → **✅ OK — 0 błędów (507 flag)**, exit 0.

### Kolejność: najpierw release STAREGO, potem sygnał NOWEMU (decyzja + uzasadnienie)

1. **Latencja starego jest bugiem** — bump jego `plan_version` to jedyna rzecz, na którą
   czeka apka starego; `_save_plan_on_assign_signal` nowego ciągnie za sobą
   recanon+redecide (OSRM/OR-Tools, bywa setki ms) i nie może opóźniać bumpa.
2. **Brak hazardu poprawności** — plany są per-cid wpisami w `courier_plans.json`
   (plan_manager pod fcntl-lockiem); kolejność wpływa tylko na latencję, nie na wynik.
3. **Symetria semantyczna** — zlecenie najpierw OPUSZCZA stary worek, potem ląduje w nowym;
   tak samo sekwencjonują deliver/return (shrink → recanon reszty).

## 3. MAPA KOMPLETNOŚCI (ETAP 3) — klasa „reassign bez zwolnienia planu starego"

Klasa = miejsca emitujące `COURIER_ASSIGNED`, w których w `orders_state` może istnieć
POPRZEDNI kurier z niezwolnionym planem. Wszystkie emitery w panel_watcher.py (grep
`emit_audit("COURIER_ASSIGNED"` — 5 miejsc, linie wg master@7e57085):

| # | Emiter (source) | Poprzedni kurier możliwy? | Werdykt |
|---|---|---|---|
| 1 | `panel_initial` (~1441, nowe zlecenie od razu z kurierem) | NIE — zlecenie świeżo weszło do state, nie miało kuriera | **N-D** |
| 2 | `panel_diff` planned→assigned (~1556, `not was_assigned`) | NIE w tej klasie — status ≠ assigned; jedyna droga „miał kuriera wcześniej" prowadzi przez `ORDER_RETURNED_TO_POOL`/`COURIER_REJECTED_PROPOSAL`, a state_machine (:1081/:1091) ustawia wtedy `courier_id: None` → ten emiter NIE ZNA starego kuriera (nie ma czego zwalniać z poziomu tej gałęzi) | **N-D** |
| 3 | `panel_reassign` (~1588) | TAK — `state_courier` trzyma starego | **POKRYTE** (call-site #1) |
| 4 | `packs_fallback` (~1694) | TAK — `previous_cid` w payload; łapie przerzuty, które diff przegapił (budżet `MAX_REASSIGN_PER_CYCLE=5`/tick, zerwany parse) | **POKRYTE** (call-site #2) — werdykt zadania: TA SAMA DZIURA, ta sama ścieżka sygnałów (`_save_plan_on_assign_signal` tylko dla nowego) |
| 5 | `cold_start_scan` (~2418) | NIE — konstrukcyjnie `if _state_cid: continue` (odpala TYLKO przy pustym cid w state; state nie zna starego, nie ma czego zwalniać) | **N-D** |

Konsumenci sygnału (apka `/plan-version` + SSE) — bez zmian; zwalnianie korzysta z
istniejących API `plan_manager.remove_stops` + `plan_recheck.recanon_courier` (te same,
których używają deliver/return — żadnej nowej semantyki w warstwie planu).
Silnik decyzyjny (feasibility/scoring/selekcja/serializer) NIETKNIĘTY — zmiana żyje w
warstwie zapisu kanonu/sygnałów panel_watchera; brak nowych metryk shadow_decisions
(telemetria = INFO w journalu panel-watchera, asertowana testem) → wpis „metryka w
jsonl" = N-D z powodem.

### Przypadek krytyczny pokryty guardem (packs, trust-raw)

W packs po `_raw` niezgodnym z mapą nicków kod robi `_target_cid = _panel_cid` (trust
raw). Gdy raw wskaże kuriera RÓWNEGO `_state_cid` (zła mapa nicków, state już w sync),
emit i tak odpala (nowe event_id `_packs`) — release wtedy **zdarłby stop z AKTUALNEGO
planu kuriera**. Guard `_state_cid != _target_cid` liczony PO trust-raw to blokuje
(test `test_packs_fallback_trust_raw_guard_no_release`).

## 4. Dowody (ETAP 4)

- **py_compile:** common.py + panel_watcher.py OK (cfile→tmp, zero .pyc w worktree).
- **Nowy plik `tests/test_reassign_old_plan_release.py`: 13/13 passed** (harness pkgroot):
  - helper: real store (fake courier_plans 2 kurierów) → stop znika TYLKO u starego,
    `plan_version` 3→4, cudzy plan bajt-w-bajt, recanon `("207","reassign_out")`,
    telemetria w caplog;
  - **ON≠OFF:** flaga OFF → store bajt-w-bajt nietknięty, zero recanon; default const
    = False + rejestracja w ETAP4 (osobny test);
  - brama lustrzana `ENABLE_SAVED_PLANS` OFF → no-op; pusty stary cid → no-op;
  - `remove_stops` rzuca → nie propaguje, recanon dalej próbowany; recanon rzuca → nie propaguje;
  - **branch-level (`_diff_and_emit`, harness wzorem test_assignment_lag_fix):** reassign
    woła `("release","207",zid)` PRZED `("signal",zid,"310")`; ten sam kurier → zero
    wywołań; **bliźniak packs** → release+signal; trust-raw → release ZABLOKOWANY;
  - strażnicy dryfu: helper zawiera remove_stops+recanon_courier+reassign_out
    (inspect.getsource); `_diff_and_emit` zawiera OBA call-site'y (kompletność bliźniaków).
- **Kontrola negatywna (C13/C14 — asercje umieją sfailować):** ten sam plik testów
  przeciw `master` (bliźniaczy worktree baseline, bez fixu) → **13/13 FAILED**
  (helper/flaga/call-site'y nie istnieją). Uwaga metodyczna: bieg z cwd=worktree
  rozwiązuje `dispatch_v2` do WORKTREE (rootdir-insert pytest wygrywa z conftest
  `_SCRIPTS_ROOT`) — kontrola negatywna wymagała osobnego worktree na masterze,
  „brak env = master" NIE zachodzi przy cwd wewnątrz worktree (sonda RESOLVED w sesji).
- **Checkery flag:** `flag_lifecycle_check.py` ✅ 0 błędów / 507 flag / exit 0;
  kuracja przeżywa ponowny `--merge` (dowód w §2).
- **PEŁNA regresja `pytest tests/` — DWA harnessy, werdykt = DELTA zbiorów FAILED
  w IDENTYCZNYM harnessie (lekcja pkgroot):**
  - **Harness B (kanoniczny pkgroot** — `ZIOMEK_SCRIPTS_ROOT=<pkgroot>` + kopia
    flags.json + `logs/` obok `dispatch_v2`, layout 1:1 jak wzorcowy wt-thr-pkgroot):
    **FIX 5221 passed / 0 failed / 24 skipped / 8 xfailed, EXIT=0** = dokładnie
    baseline-master 5208 + 13 nowych testów. **Zbiór FAILED pusty.**
  - **Harness A (bez env** — skanery-strażnicy czytają ŻYWY kanon `tests/`):
    baseline master w bliźniaczym worktree **5208/0/24/8 EXIT=0**; FIX
    5220 passed / **1 failed** (`test_flag_effect_coverage::test_no_new_untested_decision_flag`)
    — **artefakt harnessu**: checker skanuje kanoniczne `tests/` (tam nowego pliku
    jeszcze nie ma). Dowód: w harnessie B test przechodzi (3/3); po merge kanon
    zawiera plik → zielony. Delta passed +12 = +13 nowych − 1 (flag_effect
    przesunął się z passed do failed w A). **DELTA zbiorów FAILED poza tym
    artefaktem = ∅.**
  - Liczby kanonu z zadania (5222/0/27/8 na żywym checkoucie master@7e57085) różnią
    się od dowolnego harnessu worktree o ~17 testów kolekcjonowanych środowiskowo
    (parametryzacja po żywych artefaktach) — dlatego werdykt = DELTA w identycznym
    harnessie, nie liczba bezwzględna (wprost wg lekcji pkgroot).

## 5. ETAP 5 (pozytywny wpływ) — plan pomiaru po flipie

Deploy ciemny (OFF) = zachowanie bajt-w-bajt jak dziś (dowód: testy OFF + pełna
regresja). Metryka docelowa po flipie: **czas od `REASSIGNED …` do zniknięcia zlecenia
z widoku starego kuriera** — proxy mierzalne w journalu `dispatch-panel-watcher`:
odstęp `REASSIGNED zid stary->nowy` → `REASSIGN-RELEASE cid_old=… oid=…` (powinien być
<1 s; dziś odpowiednik = fallback 180 s poller / ≤5 min plan_recheck). Watch okno 2 dni
po flipie: `journalctl -u dispatch-panel-watcher | grep REASSIGN-RELEASE` + brak
`REASSIGN-RELEASE remove_stops fail` / `recanon-on-reassign-out fail`.

## 6. Checklist deployu (dla MAIN/ownera — NIC z tego nie wykonane w tej sesji)

1. Merge `fix/reassign-old-plan-release` do master (baza 7e57085; **master zdryfował**
   w trakcie sesji do 736b323 — manifest v10 nocnego strażnika, pliki się NIE nakładają
   z tą zmianą; zwykły merge/rebase bez konfliktów oczekiwany).
2. `py_compile` + pełna suita na KANONIE (żywy checkout) — wg lekcji pkgroot werdykt
   liczy się z kanonu.
3. Restart **`dispatch-panel-watcher.service`** (jedyny serwis czytający ten kod;
   NIE telegram, NIE w peaku, za ACK).
4. Deploy = flaga OFF (brak klucza w flags.json) → zachowanie identyczne jak dziś;
   log NIE pokaże REASSIGN-RELEASE (to poprawne przy OFF).
5. **FLIP za ACK ownera:** `flags.json` += `"ENABLE_REASSIGN_OLD_PLAN_RELEASE": true`
   (hot-reload przez decision_flag per-call — bez restartu). Po flipie watch §5 (2 dni).
6. **Rollback:** hot = klucz `false`/usunięcie klucza z flags.json (natychmiast, bez
   restartu); pełny = `git revert` commita + restart panel-watchera. Ryzyko rezydualne
   przy OFF: zero (jedyny nowy kod na ścieżce OFF to odczyt flagi w helperze).

## 7. Ryzyka / uwagi

- **Wyścig „stary właśnie dostarczył":** koordynator przerzuca, a stary w tym samym oknie
  klika DOSTARCZONE — remove_stops u starego jest wtedy no-op/idempotentny (stop już
  zdjęty przez advance_plan) albo zdejmie stop, którego panel i tak już u niego nie
  trzyma; obie gałęzie bez szkody (idempotentne API, jak w cancel/return dziś).
- **Recanon reason:** nowy literał `reason="reassign_out"` — recanon traktuje reason
  wyłącznie opisowo (telemetria), brak gałęzi po reason w plan_recheck (grep).
- **`_release_plan_on_reassign` przy planie nieaktywnym:** remove_stops no-op gdy plan
  brak/invalidated — czyli dokładnie stan sprzed fixu (≤).
- **Seeder-heurystyka `_OLD`:** świadomie NIE zmieniona (ryzyko przeklasyfikowania
  cudzych wpisów); kuracja = mechanizm projektowy rejestru i przeżywa re-seed. Gdyby
  przyszłe flagi z `_OLD` w tym sensie się mnożyły — kandydat na poprawkę heurystyki
  osobnym tematem.

## 8. FINDING POBOCZNE (poza chirurgicznym scope — do kolejki, NIE ruszone tutaj)

**RETURN-path bez remove_stops w branchu „disappeared":** diff-loop, zlecenie znika
z HTML ze statusem 8/9 (~1518-1538 na masterze) → emituje `ORDER_RETURNED_TO_POOL`
(`event_id …_panel_diff`) + `update_from_event` **BEZ** `_remove_stops_on_return` —
jedyne wywołanie tego helpera żyje w sekcji RECONCILE (`…_reconcile`, inne event_id).
Po update state jest terminalny → reconcile już go nie podniesie → stop zostaje w
planie starego kuriera do regeneracji planu przez 5-min tick. Ta sama klasa „tranzycja
kurcząca worek bez remove_stops" co niniejszy fix, ale INNA tranzycja (return, nie
reassign), PRE-ISTNIEJĄCA (nie wprowadzona tym fixem) i wymagająca własnej decyzji
(nowy call-site zmieniałby zachowanie live BEZ flagi). Werdykt: zgłoszone do MAIN
jako kandydat naprawy, świadomie N-D w tym branchu (scope przypięty przez CTO =
reassign; dyscyplina dark-deploy tej sesji).
