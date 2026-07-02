# L7.5 — fcntl na `pending_proposals.json` (audyt 2.0 finding O1)

**Pas D, worktree `wt-pending-fcntl`, branch `fix/pending-fcntl`.** Build-only, ZERO flip/restart/push.
Prerekwizyt PRZED re-enable Telegrama (C2) i PRZED 1. flipem autonomii.

## Problem (O1)
`pending_proposals.json` ma potencjalnie 3 pisarzy robiących read-modify-write CAŁEGO dicta.
`os.replace` daje TYLKO brak torn-read (czytelnik nie widzi połówki), **NIE** serializuje cykli RMW
→ klasyczny lost-update. Dziś bezpieczne tylko **emergentnie** (2 z 3 pisarzy uśpione: telegram muted
od 26.06, `postponed_proposals.json={}`), nie strukturalnie. Dodatkowo telegram i store dzieliły ten
sam plik tymczasowy `{path}.tmp` → kolizja przy współbieżnym zapisie.

## ETAP 0 — mapa pisarzy i czytelników (zweryfikowana grepem + na żywo)

| # | Miejsce | Operacja | Proces | Stan | Zmiana |
|---|---|---|---|---|---|
| W1 | `shadow_dispatcher.py:1356` → `pending_proposals_store.upsert_proposals` | RMW load→sweep→merge→save | dispatch-shadow (tick ~5s) | **active** (jedyny realny writer; `ENABLE_PENDING_PROPOSALS_WRITE=True`) | `upsert_proposals` teraz **pod LOCK_EX** → shadow bez zmiany kodu |
| W2 | `postpone_sweeper.py:150-158` | RMW load→setitem→save | dispatch-postpone-sweeper.timer (60s) | active-ale-idle (`postponed={}`) | przepięte na `locked_mutate` |
| W3 | `telegram_approver.py:save_pending` | blind-overwrite z pamięci | dispatch-telegram | **inactive** (muted 26.06) | przepięte na `locked_save` (LOCK_EX + koniec współdzielonego tmp) |

**Czytelnicy (nie piszą, atomic-safe — BEZ zmiany):** `panel_watcher.py:222/386/452` (PANEL_OVERRIDE / PANEL_AGREE / `_save_plan_from_pending`, wszystkie `open(...,"r")`), `tools/pending_global_resweep.py:264` (read-only), `daily_briefing.py` (read-only). Potwierdzone grepem: żaden nie jest 4. pisarzem.

**Na żywo:** `pending_proposals.json` = 4 wpisy, wszystkie w schemacie store `{message_id,sent_at,expires_at,decision_record}` (pisane przez shadow). Brak widocznej anomalii licznika (lost-update to cicha strata sygnału, nie crash — niewidoczna w snapshocie; dlatego dowód = test współbieżności, nie inspekcja pliku).

## Naprawa u źródła — jeden kanon dostępu (`pending_proposals_store.py`)
Wzorzec 1:1 z `plan_manager._locked` / `state_machine._locked_write` (O2 = wzorzec do naśladowania):
- `_locked(path, exclusive=True)` — `fcntl.LOCK_EX` na dedykowanym lockfile `{path}.lock` (przeżywa `os.replace` pliku pending).
- `locked_mutate(mutate_fn, path)` — RMW pod lockiem: `load→mutate_fn(in-place)→save`, cały cykl w jednym locku. **W2 (postpone) tu.**
- `locked_save(pending, path)` — blind-overwrite pod lockiem (serializacja + koniec kolizji tmp). **W3 (telegram) tu.**
- `upsert_proposals` (alias `locked_upsert`) — jego load→sweep→merge→save owinięte w `_locked`. **W1 (shadow) tu, bez edycji shadow.**
- `save` → unikalny tmp (`mkstemp`) zamiast współdzielonego `{path}.tmp`; format JSON (`indent=2, ensure_ascii=False`) **niezmieniony**.
- `load`/`save` pozostają prymitywami BEZ locka (wołane wewnątrz locka); `mutate_fn` NIE wywołuje `locked_*` (zagnieżdżony LOCK_EX na osobnym fd = deadlock — udokumentowane w docstringu).

### Co przepięte, a co design-only
- **W1 shadow, W2 postpone = w pełni race-free** — oba robią prawdziwy RMW pod tym samym LOCK_EX → wzajemnie się serializują, zero lost-update. postpone dodatkowo naprawiony jakościowo: blind write → RMW-pod-lockiem, więc **zachowuje wpisy dołożone współbieżnie przez shadow** (wcześniej mógł je nadpisać).
- **W3 telegram = MINIMALNIE (1 ciało funkcji)**: `save_pending` → `locked_save`. To wpina telegram w dyscyplinę locka (serializacja zapisu + koniec współdzielonego tmp), ale **NIE eliminuje resztkowego lost-update z nieświeżej pamięci**: telegram trzyma pełny dict w pamięci (ładowany raz na starcie) i blind-nadpisuje — jeśli shadow dołoży wpis, którego telegram nie zna, blind-overwrite (nawet pod lockiem) go usunie.
  - **DESIGN-ONLY (osobny ACK, część checklisty pre-re-enable — NIE w tym pasie, bo zmienia semantykę zapisu najczulszego pliku):** telegram powinien pisać DELTAMI przez `locked_mutate` per-operacja zamiast blind-overwrite. Dwa call-site'y:
    - dodanie propozycji (`telegram_approver.py:~2081`): `pending_proposals_store.locked_mutate(lambda p: p.__setitem__(oid, entry), path)` zamiast `state["pending"][oid]=entry; save_pending(...)`.
    - pop po assign/expire/drain (`~2933/3000/4017/4083/4102`): `pending_proposals_store.locked_mutate(lambda p: p.pop(oid, None), path)`.
    - `state["pending"]` (pamięć telegramu) pozostaje jako cache do logiki callbacków; źródłem prawdy staje się dysk pod lockiem. Naiwny union-merge NIE działa (gubi POP-y) — dlatego delta, nie merge.
  - Alternatywa (mniejsza): przy każdym callbacku przeładować `state["pending"]` z dysku pod LOCK_SH przed mutacją. Też poza „minimalne".

### Klaster postpone (5 dead-paths z audytu)
Osobny mechanizm od wyścigu O1 — dotyczy `postpone_sweeper` re-emit/schema (`.get('cid')` vs `courier_id`, brak wrappera `orders` itd., wzorzec #14). **NIE objęty tym pasem** (to logika re-emitu, nie współbieżność pliku). Jedyny punkt styku — zapis pending w `run_once` — już przepięty na kanon. Reszta klastra = osobny sprint schema-fix (design-only, poza L7.5).

## Dowody (nie deklaracje)
Test: `tests/test_pending_fcntl_concurrency_l75.py` (5/5), harness worktree (`ZIOMEK_SCRIPTS_ROOT=pkgroot→worktree`).
- **KANON → 0 lost:** 3 PROCESY × 120 wpisów przez `locked_mutate` → 360/360 kluczy obecnych.
- **MUTACJA #1 (zdejmij lock) reprodukuje wyścig:** ten sam scenariusz surowym load→save BEZ `_locked` → część wpisów zgubiona (asercja `missing` niepusty — dowód, że lock realnie serializuje).
- **MUTACJA #2 (zdejmij atomic):** zapis in-place (truncate+write) → czytelnik łapie torn-read (JSONDecodeError); atomowy `save` (os.replace) → 0 torn-read.
- **Mutacja na PRODUKCYJNYM `_locked` (C13, in-proc threads):** realny `_locked` → 0/160 lost; `_locked`→no-op → **120/160 lost**. Strażnik behawioralny, nie tekstowy.
- **Round-trip / format:** `locked_save` → plik z `indent=2` + unicode dosłownie; `store.load` == `telegram_approver.load_pending` == `postpone_sweeper._load_json_safe` == payload (czytelnicy wsteczni bez zmiany).
- **`locked_mutate` widzi świeży dysk:** wpis A na dysku + mutate dokłada B → po RMW obecne OBA (kontrast do blind-overwrite).
- Stabilność: test file ×3 = 5/5 za każdym razem (nie-flaky).

**Regresja (w worktree, `pytest tests/`):**
- Baseline: `23 failed, 3970 passed, 26 skipped, 9 xfailed, 2 xpassed` (23 failed = znane artefakty path-layout worktree, m.in. `test_courier_reliability` — niezwiązane z pending).
- Po zmianie: `23 failed, 3975 passed, 26 skipped, 9 xfailed, 2 xpassed`.
- **Δ = +5 passed = dokładnie 5 nowych testów L7.5; failed/skipped/xfailed/xpassed bez zmian** → żaden istniejący test nie flipnął (arytmetyka wyklucza pass→fail i fail→pass). Touched-area (pending/postpone/telegram/panel_agree/a2/smoke_override/bug1) = 46/46 green.

## SEKWENCJA DEPLOYU (ZA ACK — nie wykonana w tym pasie)
Deploy = koordynator, seryjnie, po merge o2-capz. `pending_proposals_store.py`/`postpone_sweeper.py`/`telegram_approver.py` to pliki żywych serwisów.
1. Merge `fix/pending-fcntl` → master (po pasie rdzeniowym o2-capz; brak przekroju plików).
2. `.bak` 3 plików → `py_compile` → `pytest tests/` z KANONU (nie worktree) vs baseline 3993/0.
3. `git log -3` (kolizja sesji) → restart **dispatch-shadow** (W1 — jego `upsert_proposals` jest teraz locked; kod locka ładuje się dopiero po restarcie).
4. Restart **dispatch-postpone-sweeper** (oneshot/timer — świeży proces per tick, efekt od następnego ticku; W2).
5. **dispatch-telegram — NIGDY bez EXPLICIT ACK Adriana w czacie** (twarda reguła). Telegram i tak muted → restart telegramu NIE jest częścią re-enable'u O1; wykonać dopiero w ramach osobnego zadania „re-enable Telegrama (C2)", razem z delta-fixem W3 wyżej.
6. Weryfikacja: `pending_proposals.json` dalej zapisywany (shadow log `PENDING_PROPOSALS_WRITE upserted=N`), `{path}.lock` powstaje, brak `{path}.tmp` sierot.

**Rollback:** `git revert` merge’a + restart dispatch-shadow + dispatch-postpone-sweeper (telegram bez zmian, bo muted). Kanon jest addytywny — `load`/`save`/`upsert_proposals` zachowują sygnatury.

## Checklist „przed re-enable Telegrama (C2)"
- [x] O1 3-writer no-lock — kanon LOCK_EX na wszystkich 3 pisarzy (ten pas).
- [x] Kolizja współdzielonego `{path}.tmp` — usunięta (mkstemp + telegram przez store).
- [ ] **W3 delta-fix telegramu** (design-only wyżej) — WYMAGANY przed re-enable, bo blind-overwrite z nieświeżej pamięci nadpisze wpisy shadow mimo locka. Osobny ACK (zmiana semantyki zapisu telegramu).
- [ ] Klaster postpone 5 dead-paths (schema `.get('cid')`/wrappera) — osobny sprint (nie blokuje O1, ale blokuje realny re-emit postpone).
- [ ] Restart dispatch-telegram — TYLKO explicit ACK Adriana.

## Ryzyka
- **Deadlock:** kanon = leaf-lock (jeden `_locked` per operacja, zwolniony przed czymkolwiek innym; `load`/`save` bez locka; `mutate_fn` bez `locked_*`). Telegram nie trzyma żadnego innego fcntl gdy woła `save_pending` (potwierdzone: brak fcntl w telegram_approver poza jsonl_appender na INNYM pliku). Zero zagnieżdżenia.
- **Kontencja:** LOCK_EX serializuje pisarzy pending; wolumen mały (kilka PROPOSE/tick), sekcja krytyczna = drobny JSON → koszt pomijalny (kontrast do O2 whole-file 8MB).
- **Format:** round-trip test pilnuje `indent=2`+unicode → czytelnicy wsteczni bez zmiany.
