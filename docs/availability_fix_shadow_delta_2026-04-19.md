# Availability fix shadow delta — 2026-04-19

Ground truth from `learning_log.jsonl` (5310 KB).

## Fleet data sources

- `kurier_piny.json`: 38 PIN keys (4-digit Courier App logins)
- `kurier_ids.json` + `courier_names.json`: 45 real courier_id
- **Phantom PIN** (PIN not in real ID space): 37

## last_4h — 126 PROPOSE decisions

| metric | count | % |
|---|---|---|
| PHANTOM PIN as best | 58 | 46.0% |
| PHANTOM PIN anywhere top-4 | 83 | 65.9% |
| identical top-scores (tie) | 17 | 13.5% |
| all-no_gps top-4 | 13 | 10.3% |

**Phantom PIN breakdown (best-ranked):**

- cid=5333 (PIN `Michał Ro`): **57×** best-candidate
- cid=7924 (PIN `Andrei K`): **1×** best-candidate

## last_24h — 269 PROPOSE decisions

| metric | count | % |
|---|---|---|
| PHANTOM PIN as best | 165 | 61.3% |
| PHANTOM PIN anywhere top-4 | 197 | 73.2% |
| identical top-scores (tie) | 17 | 6.3% |
| all-no_gps top-4 | 13 | 4.8% |

**Phantom PIN breakdown (best-ranked):**

- cid=9928 (PIN `Mateusz O`): **70×** best-candidate
- cid=5333 (PIN `Michał Ro`): **57×** best-candidate
- cid=4257 (PIN `Mateusz Bro`): **35×** best-candidate
- cid=2824 (PIN `Michał Rom`): **2×** best-candidate
- cid=7924 (PIN `Andrei K`): **1×** best-candidate

## all_time — 1140 PROPOSE decisions

| metric | count | % |
|---|---|---|
| PHANTOM PIN as best | 546 | 47.9% |
| PHANTOM PIN anywhere top-4 | 677 | 59.4% |
| identical top-scores (tie) | 40 | 3.5% |
| all-no_gps top-4 | 31 | 2.7% |

**Phantom PIN breakdown (best-ranked):**

- cid=6881 (PIN `Szymon P`): **107×** best-candidate
- cid=7924 (PIN `Andrei K`): **100×** best-candidate
- cid=9928 (PIN `Mateusz O`): **84×** best-candidate
- cid=5333 (PIN `Michał Ro`): **57×** best-candidate
- cid=4257 (PIN `Mateusz Bro`): **48×** best-candidate
- cid=4657 (PIN `Bartek O.`): **35×** best-candidate
- cid=3547 (PIN `Kacper Sa`): **33×** best-candidate
- cid=2584 (PIN `Tomasz Ch`): **20×** best-candidate
- cid=1434 (PIN `Adrian R`): **19×** best-candidate
- cid=2824 (PIN `Michał Rom`): **14×** best-candidate

## Interpretation

Per `kurier_piny.json`, większość kurierów używających Courier App ma **PIN nie mapowany** w `kurier_ids.json`. Wcześniejszy kod `build_fleet_snapshot` dodawał PIN-keys jako osobnych kurierów → fleet duplikaty → dedup po name gubi wpis który powinien wygrać (real cid z bagiem vs phantom PIN no_gps). W ciężkich warunkach (panel_watcher lag → real cid też no_gps → tie) phantom PIN wygrywał w 45-61% propozycji.

Po fixie (`STRICT_COURIER_ID_SPACE=True`) phantom PINy znikają z fleet. Real couriers pozostają jako jedyne kandydaci per name → Telegram propozycje zawsze z prawdziwym `courier_id` → koordynator może assign przez panel API bez ręcznego fallback.

## Pending LIVE

Fix committed (3 commits + 3 tagów + master tag TBD). WYMAGA restart `dispatch-panel-watcher.service` + `dispatch-shadow.service` — Python nie hot-reloaduje. `dispatch-telegram.service` nie wymaga (nie woła build_fleet_snapshot).
