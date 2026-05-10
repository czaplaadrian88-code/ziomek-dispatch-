# Sprawa #1 Parser Validation — 2026-05-05

Walidacja `parse_response()` w `dispatch_v2/migrations/migrate_couriers_2026-05-05.py`
przed wieczornym apply z odpowiedzią Adriana. Wszystkie testy uruchomione w
`--dry-run --no-telegram` mode, zero zapisu do prod stores.

**Pre-test audit (sanity check):**

```
mapped=36 partial=4 unmapped=5 skipped_noise=5
UNMAPPED: Kuba Olchowik, Marcin Bystrowski, Gabriel Ostapczuk, Szymon Bawerna, Daniel Malicki
PARTIAL:  Mykyta Kumeiko (cid=426, std), Szymon Sadowski (cid=522, new),
          Grzegorz Rogowski (cid=500, new), Filip Prończuk (cid=354, std)
```

→ Adrian otrzyma 9 mappingów do uzupełnienia (5 UNMAPPED + 4 PARTIAL).

---

## Test results — 5/5 PASS

| # | Test | Expected | Actual | Verdict |
|---|------|----------|--------|---------|
| 1 | Happy path 9-line full response | 9 valid, 0 skipped | 9 valid, 0 skipped | PASS |
| 2 | Missing tier (Daniel Malicki ostatnie tokeny `604`) | 8 valid, 1 skipped | 8 valid, 1 skipped (`unknown tier '604'`) | PASS |
| 3 | Duplicate cid (Marcin również 600) | 8 valid, 1 skipped (Marcin) | 8 valid, 1 skipped (`cid=600 duplicated within input`) | PASS |
| 4 | Typo panel_name (Mikyta zamiast Mykyta) | 8 valid, 1 skipped (Mikyta) | 8 valid, 1 skipped (`cid=426 already in kurier_ids (duplicate)`) | PASS (defense-in-depth) |
| 5 | Tier case insensitivity (STANDARD/Std/std/Standard mix) | 9 valid, 0 skipped | 9 valid, 0 skipped | PASS |

**Test fixtures:** `eod_drafts/2026-05-05/_sprawa1_test_{1..5}_*.txt`
(prefix `_` żeby trzymać poza INDEX.md final outputs).

---

## Edge cases handled

### Test 1 — happy path (baseline)
Wszystkie 9 linii poprawnie sklasyfikowane:
- 5 UNMAPPED → `panel_name` syntezowany jako `"FirstName L"` (np. `Kuba O`, `Daniel M`).
- 4 PARTIAL → `panel_name` z `partial_by_name` (np. `Mykyta K`, `Filip P`).

Note dla Szymon Sadowski / Grzegorz Rogowski: panel_name w istniejącym
`kurier_ids.json` to `"Szymon Sadowski"` / `"Grzegorz Rogowski"` (full name),
nie `"Szymon S"` / `"Grzegorz R"`. Parser to honoruje (partial_by_name
forwarding).

### Test 2 — too few tokens
Linia `Daniel Malicki 604` ma 3 tokeny → tokens[-1]=`604`, tokens[-2]=`Malicki`.
HUMAN_TO_INTERNAL_TIER lookup `604` → None → skip z reason
`"unknown tier '604' (valid: gold|Std+|Standard|Slow)"`. Parser NIE rzuca, NIE
psuje pozostałych 8.

### Test 3 — duplicate cid w jednym response
`seen_cids` set (line 442) wyłapuje cid=600 drugi raz (Marcin) i odrzuca:
`"cid=600 duplicated within input"`. Pierwszy (Kuba) accepted.

### Test 4 — typo (Mikyta zamiast Mykyta)
Defense-in-depth — parser ma 3 niezależne guardy które łapią ten case:
1. `full_name in partial_by_name` → False (Mikyta ≠ Mykyta) → spada do unmapped path.
2. `cid_int in existing_cids` → True (426 już w kurier_ids dla Mykyta K).
3. (gdyby nie 2) `full_name not in sched_names` → też skip.
Actual reason zwrócony: `"cid=426 already in kurier_ids (duplicate)"`.
Wynik: typo bezpiecznie odrzucony, pozostałe 8 valid.

### Test 5 — tier case insensitivity
`HUMAN_TO_INTERNAL_TIER` wszystko via `.lower()` na input → wszystkie warianty
(`STANDARD`, `Std`, `std`, `Standard`) mapują na canonical `std`. `slow` /
`SLOW`, `new` / `NEW` analogicznie.

---

## Atomic transaction guarantee — verified

`migrate_one()` (linie 547-650) implementuje all-or-nothing rollback per kurier:
- Step 1 (`kurier_ids`) → na fail return False bez side-effect (snapshot fallback).
- Step 2 (`courier_tiers`) → na fail rollback step 1 z `snap_ids` deep copy.
- Step 3 (`kurier_piny`) → na fail rollback step 2 + step 1.
- `_atomic_write_json` używa `fcntl.LOCK_EX` + tempfile + fsync + rename
  per Lekcja #71.

W dry-run NIE odpalamy `migrate_one()` — `cmd_apply` wraca po wypisaniu
WOULD APPLY (linie 680-686). Zero ryzyka write.

**Verified post-test:** mtimes prod files niezmienione:
```
courier_tiers.json   May  1 16:15  (pre-test)
kurier_ids.json      Apr 24 19:49  (pre-test)
kurier_piny.json     Apr 24 19:49  (pre-test)
schedule_today.json  May  5 12:56  (today's load — read-only)
```

---

## Adrian instructions IF parser warns

### Warning: `unknown tier '<X>'`
Tier nie matchuje `gold | std+ | standard+ | std | standard | slow | new`.
**Fix:** Adrian poprawia tier w odpowiedniej linii i wysyła ponownie. Parser
jest line-by-line — kontynuuje pozostałe valid lines. Adrian może wysłać
tylko linię z poprawką (idempotent rerun NIE konfliktuje, bo poprzednie OK
linie są już w stores).

### Warning: `cid=N duplicated within input`
Adrian napisał ten sam cid dla 2 kurierów w tej samej odpowiedzi. **Fix:**
Adrian sprawdza panel (`/admin2017/new/kurierzy`), poprawia jeden z cid.

### Warning: `cid=N already in kurier_ids (duplicate)`
cid jest już zmapowany na innego kuriera. 2 możliwości:
1. Adrian podał cid który już jest used — sprawdza panel, podaje wolny cid.
2. **Typo w panel_name** (Test 4): kurier ma już mapping pod inną pisownią
   (np. `Mykyta` vs `Mikyta`). Adrian poprawia pisownię żeby trafić w
   `partial_by_name` lookup (case-sensitive!) — pisownia musi być **dokładnie**
   jak w `schedule_today.json`.

### Warning: `name '<X>' not in schedule_today.json (typo?)`
Adrian napisał kuriera którego NIE ma w dzisiejszym grafiku. **Fix:** Adrian
sprawdza pisownię vs grafik. Jeśli kurier rzeczywiście nie pracuje dziś →
poczekaj do TASK D czwartek (full multi-day flow).

### Warning: `panel_name collision '<X>'`
Synthesized `"FirstName L"` (lub `"FirstName Las"`) już istnieje. Bardzo
rzadkie (drugi kurier o tym samym imieniu i pierwszej literze nazwiska).
**Fix:** Adrian wysyła do CC explicit `panel_name` w komentarzu, CC ręcznie
modyfikuje migrate_one input (one-off case, manual override).

---

## Recommendation

Parser w obecnym kształcie jest gotowy do wieczornego apply. Adrian może
spokojnie wysyłać odpowiedź w formacie z `sprawa_1_response_template.md`.

**Przed apply (wieczorem):**
1. CC odpala `cmd_audit` z `--no-telegram` → potwierdza że audit dalej pokazuje
   5 UNMAPPED + 4 PARTIAL (idempotent — schedule może się rozszerzyć w ciągu
   dnia, ale żaden z tych 9 nie zniknie).
2. CC odpala `--apply <plik> --dry-run --no-telegram` → pokazuje WOULD APPLY
   list.
3. Adrian ACK.
4. CC odpala `--apply <plik>` (bez `--dry-run`) → atomic write + Telegram
   confirmation.
5. CC odpala `verify` → expect `mapped=45 partial=0 unmapped=0`.
