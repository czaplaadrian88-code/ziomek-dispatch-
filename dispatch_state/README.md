# ⚠ TEN KATALOG TO **NIE** JEST STAN SILNIKA

**Żywy stan runtime Ziomka** = `/root/.openclaw/workspace/dispatch_state/` (poza gitem, ~1 GB, ~318 plików: `orders_state.json`, `courier_plans.json`, shadow-jsonl itd.).

Ten katalog w repo zawiera **wyłącznie dane epaki** (`epaka_data/` — cenniki/prowizje/zamówienia; pisze je `tools/epaka_fetcher.py`, cron 06:00). Zbieg nazw to pułapka nawigacyjna potwierdzona audytem 2026-07-03 (`docs/audyt/00-INWENTARYZACJA.md §0`); docelowe rozstrzygnięcie (rename?) = WD-9 w `docs/audyt/10-PLAN.md`.

Główny log decyzji silnika: `/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl` (NIE tutaj).
