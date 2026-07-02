# L7.5 delta-fix — telegram pisze DELTAMI zamiast blind-overwrite (pas telegram-delta)

**Worktree** `/root/.openclaw/workspace/wt-telegram-delta`, **branch** `fix/telegram-delta`.
Build-only, ZERO flip/restart/push. Domknięcie design-only z raportu `pending-fcntl_raport.md`
(checklist pre-re-enable Telegrama, poz. „W3 delta-fix").

## Problem (O1 / W3, ostatni krok)
Pas L7.5 wpiął telegram w LOCK_EX przez `save_pending → locked_save`, ale ZOSTAWIŁ resztkowy
lost-update: telegram trzyma pełny `state["pending"]` w pamięci (load raz na starcie) i przy
KAŻDEJ operacji nadpisywał CAŁY plik. Nawet pod lockiem blind-overwrite z NIEŚWIEŻEJ pamięci
zdejmuje wpis dołożony współbieżnie przez shadow (`upsert_proposals`) PO ostatnim load telegramu.
Lock serializuje ZAPIS, nie treści → cudzy klucz ginie. Naiwny union-merge NIE działa (gubi POP-y),
więc fix = per-operacja DELTA na świeżym stanie spod locka.

## Mapa operacji telegramu na pending — PRZED → PO
Wszystkie 7 pisarzy (1 add + 5 pop + 1 drain). Read-only (lookup po message_id / max sent_at /
`get(oid)` / iteracja expired / `oid in pending`) — BEZ zmian (nie piszą).

| # | Miejsce | PRZED | PO |
|---|---|---|---|
| ADD | `proposal_sender` ~2113 | `state["pending"][oid]=entry; save_pending(cały dict)` | `set_pending(path,oid,entry)` (disk-first, tylko klucz) `; state["pending"][oid]=entry` (cache) |
| POP-1 | REPLY_OVERRIDE reply ~2958 | `pending.pop(matched_oid); save_pending(cały)` | `pop_pending(path,matched_oid); pending.pop(matched_oid)` |
| POP-2 | REPLY_OVERRIDE free-text ~3025 | `pending.pop(latest_oid); save_pending(cały)` | `pop_pending(path,latest_oid); pending.pop(latest_oid)` |
| POP-3 | callback ASSIGN/NIE/INNY/KOORD ~4042 | `pending.pop(oid); save_pending(cały)` | `pop_pending(path,oid); pending.pop(oid)` |
| POP-4 | `_process_expired_pending` SUPERSEDED ~4108 | `pending.pop(oid); save_pending(cały)` | `pop_pending(path,oid); pending.pop(oid)` |
| POP-5 | `_process_expired_pending` TIMEOUT ~4127 | `pending.pop(oid); save_pending(cały)` | `pop_pending(path,oid); pending.pop(oid)` |
| DRAIN | `_shutdown_drain` ~4275 | `save_pending(cały dict)` (blind) | `locked_merge_missing(pending)` (ADDITIVE — dołóż brakujące, NIGDY nie kasuj) |

**Kolejność w każdej op: DYSK PIERWSZY (źródło prawdy), potem cache w pamięci.** Crash między
tymi liniami: dysk już ma prawdę, restart czyta dysk → brak zgubionego proposal-a. To ważne dla
ADD (proposal wysłany do Telegrama = musi być trwały). `state["pending"]` zostaje jako cache do
logiki callbacków; źródłem prawdy jest dysk pod lockiem.

**Drain — dlaczego additive, nie usunięty:** każda op zapisuje deltę SYNCHRONICZNIE, więc pamięć
telegramu jest już zflushowana → drain nie ma nic do flushu. Zostawiony jako bezpiecznik ADDITIVE
(`setdefault`): dołoży własny wpis telegramu brakujący na dysku, NIGDY nie skasuje cudzego (shadow)
ani nie wskrzesi popa (popnięte klucze nie są w pamięci → nie w `entries`). Blind-overwrite w drainie
byłby tą samą dziurą lost-update, tyle że przy zamykaniu.

## Kanon w store (`pending_proposals_store.py`) — 3 nowe helpery per-op DELTA
Wszystkie na istniejącym `locked_mutate` (RMW pod LOCK_EX na dedykowanym lockfile). `mutate_fn`
NIE woła żadnej `locked_*` (zagnieżdżony LOCK_EX = deadlock — udokumentowane).
- `locked_set(key, value, path)` — ustaw jeden klucz na świeżym stanie.
- `locked_pop(key, path)` — usuń jeden klucz (idempotentne).
- `locked_merge_missing(entries, path)` — additive reconcile (`setdefault`), dla drainu.

Telegram woła je przez cienkie wrappery `set_pending`/`pop_pending` (lazy import, styl 1:1 z
istniejącym `save_pending`). `save_pending` ZOSTAJE (publiczne API, teraz bez wewnętrznych callerów)
— zero innych zmian w pliku.

## Deadlock — analiza kolejności locków
Callback popuje pending PO tym jak `append_learning` w PEŁNI zwrócił. `append_learning` →
`jsonl_appender.append_jsonl` bierze `fcntl.flock` na INNYM pliku (learning_log.jsonl) i zwalnia
przed returnem (implicit on close). Moje helpery biorą LOCK_EX na `pending.json.lock`. Nigdy nie są
trzymane naraz, różne pliki, brak zagnieżdżenia → deadlock niemożliwy. Postpone (INNY postpone_10min)
pisze do WŁASNEGO pliku i `return` przed popem pending — nie dotyka pending. Zachowana idempotencja
callbacków: `entry = state["pending"].get(oid)`; drugi klik → entry gone → „Unknown order".

## Dowody (nie deklaracje) — `tests/test_telegram_delta_l75.py` (5) + istniejący L7.5 (5) = 10/10
- **A. `test_delta_pop_preserves_concurrent_shadow_entry`**: shadow dokłada S1 PO snapshot telegramu;
  `locked_pop(T1)` → S1 PRZEŻYWA. Kontrola negatywna w tym samym teście: `locked_save(snapshot)` (stary
  blind) → S1 SKASOWANY. Bezpośredni dowód różnicy delta vs blind.
- **C. `test_delta_concurrent_shadow_survives`** (wieloproces, realny fcntl): telegram-pop ‖ shadow-upsert
  naraz (60‖60) → 0 wpisów shadow zgubionych + 0 pozostałych kluczy telegramu.
- **B. `test_blind_concurrent_reproduces_shadow_loss`** (MUTACJA — cofnij fix do blind-save): ten sam
  scenariusz → część wpisów shadow ZGUBIONA (asercja `missing` niepusta). Cofnięcie fixa = test PADA.
- **D1. `test_telegram_wrappers_roundtrip_and_readers`**: `set_pending`/`pop_pending` → format
  niezmieniony (indent=2, `"ą":"ł"` dosłownie), czytelnicy wsteczni 1:1 (`store.load` == `tg.load_pending`
  == `ps._load_json_safe`); pop idempotentny.
- **D2. `test_drain_merge_missing_is_additive`**: drain dołoży brakujący T3, zachowa cudzy S1, NIE wskrzesi
  popniętego T1.
- **Round-trip / format**: potwierdzony (D1) — plik czytają identycznie wszyscy trzej wsteczni czytelnicy.

**Regresja (worktree, `pytest dispatch_v2/tests/`, ZIOMEK_SCRIPTS_ROOT=pkgroot→worktree):**
Po zmianie: **23 failed, 4056 passed, 23 skipped, 9 xfailed, 2 xpassed** (110s). 23 failed =
DOKŁADNIE pre-existing artefakty worktree (15× `test_a2_selection_shadow` custom `SkipTest`
reportowany jako FAILED — `_key_bucket` nieimportowalny w CLI-only build + 8× `test_courier_reliability`
path-layout, oba nazwane w baseline; zero dotyka pending/telegram). ZERO nowych failów; +5 testów delta
i 2 naprawione testy `test_mp10_...shutdown_drain` w puli passed. Pierwszy bieg (przed naprawą mp10)
miał 25 failed — 2 nadmiarowe to testy drainu kodujące STARY kontrakt blind-save (log „flushed" +
`save_pending` jako powierzchnia błędu); zaktualizowane do nowego kontraktu additive.

## ⚠ INCYDENT — zanieczyszczenie żywego stanu (zgłoszony team-leadowi, czeka na ACK cleanup)
Podczas budowy testu worker `_shadow_upsert_worker` wołał `upsert_proposals(upserts, now)` BEZ `path=`,
a ten defaultuje do PRODUKCYJNEGO `PENDING_PATH` (nie tmp) → 60 fejkowych wpisów `S_0..S_59` trafiło do
`/root/.openclaw/workspace/dispatch_state/pending_proposals.json`. Zero realnych danych zmieszanych
(wszystkie 60 kluczy to śmieć). **Test naprawiony** (`path=path` wymuszony + `assert path != PENDING_PATH`;
ponowny bieg NIE dotyka prod — potwierdzone). Cleanup chirurgiczny (pop S_0..S_59 pod lockiem store) =
mutacja żywego stanu poza worktree → auto-classifier zablokował, czeka na ACK team-lead/Adrian.
Lekcja: `upsert_proposals`/store-funkcje mają `path=PENDING_PATH` default → w testach ZAWSZE przekazuj
`path=` jawnie; guard `assert path != PENDING_PATH` w workerach testowych.

## Checklist „przed re-enable Telegrama (C2)" — aktualizacja
- [x] O1 3-writer no-lock — LOCK_EX na wszystkich 3 pisarzy (pas L7.5).
- [x] Kolizja współdzielonego `{path}.tmp` — usunięta (pas L7.5).
- [x] **W3 delta-fix telegramu — ZROBIONY (ten pas).** Blind-overwrite z nieświeżej pamięci wyeliminowany:
  add/pop/drain przez per-op deltę na świeżym stanie spod locka. Wpisy shadow przeżywają operacje telegramu.
- [ ] Klaster postpone 5 dead-paths (schema `.get('cid')`/wrappera) — osobny sprint (nie blokuje O1).
- [ ] Restart dispatch-telegram — TYLKO explicit ACK Adriana (kod żywy DOPIERO po restarcie).

## UWAGA deploy
Kod telegramu jest ŻYWY DOPIERO po restarcie `dispatch-telegram` — a to WYŁĄCZNIE przy przyszłym C2
(re-enable Telegrama), z explicit ACK Adriana. Ten pas = build-only, NIE restartuje niczego. Merge
`fix/telegram-delta` → master + restart to osobne zadanie C2. Rollback: `git revert` (kanon addytywny;
`save_pending` zachowane, sygnatury store bez zmian).
