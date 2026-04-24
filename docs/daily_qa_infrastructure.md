# Daily Q&A Infrastructure (V3.26)

**Location:** `/root/.openclaw/workspace/q_and_a/` (outside dispatch_v2 git repo — workspace-level).

**Purpose:** structured daily operator feedback capture. Adrian (solo operator)
codziennie wieczorem ~20:00 wypełnia daily case log z 5 cases + meta +
reason codes. Collected data → pattern analysis Claude po 7 dni → trigger
V3.28 learning_analyzer Poziom 2 po 30 dni.

## Files

| Path | Purpose |
|---|---|
| `_template.md` | Template z 5 case placeholders + Reason Codes Reference |
| `new_day.sh` | Helper — kopiuje template → `daily_YYYY-MM-DD.md` z date substituted |
| `daily_YYYY-MM-DD.md` | Adrian's per-day log (nowy generated z template) |

## Workflow

Adrian wieczorem 20:00:
```bash
/root/.openclaw/workspace/q_and_a/new_day.sh
# → creates /root/.openclaw/workspace/q_and_a/daily_YYYY-MM-DD.md
nano /root/.openclaw/workspace/q_and_a/daily_YYYY-MM-DD.md
```

Helper script wypisuje też przydatne grep commands:
- Last 50 PANEL_OVERRIDE oids
- Decision dla konkretnego oid (shadow_decisions.jsonl)
- Rationale R-11 field dla oid

## Reason Codes (7)

1. **WAVE_CONTINUATION_MISSED** — Kurier z bagiem w direction X, Ziomek
   proposal w direction Y
2. **TRAJECTORY_MISMATCH** — Multi-stop, proposal wbrew trajektorii
   (opposite quadrant)
3. **SCHEDULE_OVERRIDE** — Proposal po shift lub przed shift bez uwzględnienia
4. **PICKUP_COLLISION** — Gap pickupów fizycznie niewykonalny
5. **DRIVER_QUALITY_MISMATCH** — Nowy kurier dostaje trudną kursę; słaby
   kurier dostaje priority
6. **FLEET_BALANCE_OFF** — Jeden kurier przeciążony, inni bez orderów
7. **OTHER** — Opis w detail

## Roadmap

- **7 dni data:** Claude analysis patterns, top reason codes, operator insights
- **30 dni data:** trigger V3.28 learning_analyzer Poziom 2 (automated
  suggestion → reason code classification z text Adrian description)

## Infrastructure commit

Tag: `v326-daily-qa-infrastructure` (marker commit — artifacts na dysku
poza git repo, workspace-level).
