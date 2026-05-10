# Sprawa #1 Response Template — 2026-05-05 wieczór

Skopiuj poniżej do Telegram DM (`@NadajeszBot`) o ~17:00 UTC po dinner peak.
CC parsuje line-by-line (atomic per-record migration; rollback per-line gdy fail).

---

## Format strict (jeden mapping per linia, separator = spacja)

Każda linia: `<full_name> <cid> <tier>`
- `full_name` — imię + nazwisko dokładnie jak w grafiku (`schedule_today.json`).
- `cid` — integer (panel courier_id).
- `tier` — case-insensitive: `gold | Std+ | Standard | Slow | new`.

Linie zaczynające się od `#` lub puste = ignorowane (komentarze).

```
# UNMAPPED (5 — full mapping cid + tier + auto-PIN gen)
Kuba Olchowik <cid> <tier>
Marcin Bystrowski <cid> <tier>
Gabriel Ostapczuk <cid> <tier>
Szymon Bawerna <cid> <tier>
Daniel Malicki <cid> <tier>

# PARTIAL (4 — re-confirm tier dla idempotency, auto-PIN gen)
Mykyta Kumeiko 426 std
Szymon Sadowski 522 new
Grzegorz Rogowski 500 new
Filip Prończuk 354 std
```

> Dla PARTIAL `cid` w linii jest informacyjny — parser i tak czerpie cid z
> audit_buckets (`partial_by_name[full_name]`). Zachowane dla spójności
> formatu i idempotency wizualnej.

---

## Tiery valid (HUMAN_TO_INTERNAL_TIER mapping)

| Wpisz (case-insensitive) | Internal |
|---|---|
| `gold` | gold |
| `std+` / `standard+` / `standard plus` | std+ |
| `std` / `standard` | std |
| `slow` | slow |
| `new` | new |

Przykłady akceptowane: `Standard`, `STANDARD`, `std`, `Std`, `STD` → wszystko `std`.

---

## Auto-PIN

Dla każdej linii (UNMAPPED + PARTIAL) CC generuje 4-cyfrowy PIN przez
`generate_pin()`:
- collision-checked vs `kurier_piny.json` (current keys)
- exclude obvious patterns: 0000/1111/.../9999 (4× ten sam digit), 1234/2345/.../6789 (ascending), 9876/8765/.../3210 (descending), 1212/2525/3737/... (repeating pair)
- random.SystemRandom 4-digit, max 10000 attempts → RuntimeError

PIN jest persisted do `kurier_piny.json` (PIN→panel_name) atomic write.

---

## Workflow Adriana

1. **Adrian otrzymuje** audit message Telegram ~17:00 UTC (cron lub manual fire `cmd_audit`).
2. **Adrian kopiuje** powyższy szablon, wypełnia `<cid>` + `<tier>` dla 5 UNMAPPED, zostawia 4 PARTIAL bez zmian (lub poprawia tier jeśli inny).
3. **Adrian wysyła** odpowiedź do CC (Telegram/file/clipboard → CC zapisuje do `/tmp/adrian_response_2026-05-05.txt`).
4. **CC executes** apply (z explicit ACK):

```bash
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.migrations.migrate_couriers_2026-05-05 \
  --apply /tmp/adrian_response_2026-05-05.txt
```

5. **CC zwraca** Telegram DM z confirmation:
   - `OK   {full_name} → cid=X, {tier}, PIN=YYYY` (per success)
   - `FAIL {full_name}: {reason}` (per failure, z rollback przeprowadzonym)
   - `Skipped lines: N` (parser warnings)
   - `Action items dla Adriana` (lista PIN-ów do rozesłania kurierom).

---

## Action items POST-deploy (Adrian)

1. **Wyślij PIN-y kurierom** (Telegram/SMS): 9 PIN-ów dla 5 nowych + 4 PARTIAL.
   - Jeśli kurier ma już hard-printed PIN (offline known) — zachowaj fizyczny, eksponuj nowy w state.
2. **GPS app registration DEFER do TASK D czwartek** (TASK D Phase 2 dorzuca courier-api integration; dziś NIE).
3. **Verify** (idempotent re-audit):

```bash
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.migrations.migrate_couriers_2026-05-05 verify
```

   Expected: `mapped=45 partial=0 unmapped=0`.

---

## Dry-run pre-flight (przed Adrian wysyła final)

Adrian może (lub CC w jego imieniu) odpalić `--dry-run` żeby zobaczyć co się
zmapuje BEZ pisania do storów:

```bash
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.migrations.migrate_couriers_2026-05-05 \
  --apply /tmp/adrian_response_2026-05-05.txt --dry-run --no-telegram
```

Wynik: lista `WOULD APPLY: ... -> panel=X cid=Y tier=Z missing=[...]` + skipped warnings.
Zero zapisu do `kurier_ids.json` / `courier_tiers.json` / `kurier_piny.json`.
