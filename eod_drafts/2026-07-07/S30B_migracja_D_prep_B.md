# S30-B — Migracja D (apka) wykonana + Przygotowanie B (panel)

## D — apka backend `courier_api/courier_orders.py` (3 gałęzie build-view)

Gałąź 1 `console_podjazdy` (LIVE dominująca: `APP_ROUTE_FROM_CONSOLE=1`) → **już
delegowała** do `route_podjazdy.order_podjazdy` = teraz `route_order` (re-eksport,
0-diff dowiedzione). Gałęzie 2/3 = FALLBACK (rzadkie: tylko gdy gałąź 1 nie odpali —
brak importu / pusty worek / wyjątek fail-soft).

| Gałąź | Dziś (OFF) | Po flipie (ON) |
|---|---|---|
| 2 `ziomek_plan` | `_prioritize_carried_dropoffs` + `_reorder_pickup_steps_by_committed` (2 częściowe reordery, ETA jedzie z krokiem) | `_reorder_steps_to_canon` → PEŁNA kolejność kanonu (`build_stop_sequence`), ETA jedzie z krokiem |
| 3 `fallback_nn` | `optimize_route` NN + carried-first + committed reorder | `optimize_route` NN → `_reorder_steps_to_canon` → kanon; **ETA doklejane PO** (`_attach_fallback_eta`) → spójne |

**Bliźniak `_repair_dropoffs_after_pickups(kind_key)`** (task 3): scalony do
`route_order.repair`. Silnik `plan_recheck` deleguje; apka trzyma lokalną kopię
(fallback bez dispatch_v2 = wymóg odporności) związaną testem parytetu. Patrz S30A §1.

**Dlaczego OFF = bajt-identyczne:** flaga `ROUTE_ORDER_UNIFIED` OFF → gałęzie 2/3
wykonują DOKŁADNIE dotychczasowy kod (`elif`/`else` zachowane 1:1). Regresja to
potwierdza (brak nowych faili). **ON = ŚWIADOMA konwergencja** kolejności do jednego
źródła — to „warto" (koniec rozjazdu gałęzi fallback z gałęzią 1/konsolą), ryzyko
ODROCZONE za flagą, flip = następny cykl za ACK Adriana (C2: flip = pełny deploy).

⚠ **Uczciwy caveat (ETA):** `_reorder_steps_to_canon` przestawia CAŁE kroki z ich
ETA (jak dotychczasowe reordery step-owe). W gałęzi 3 ETA doklejane PO reorderze →
spójne. W gałęzi 2 ETA z planu jedzie z krokiem (identycznie jak dziś `_reorder_
pickup_steps_by_committed`) — nie pogarsza, ale pełne wyrównanie ETA-po-kolejności do
gałęzi 1 (`build_stop_sequence`+`_attach_fallback_eta`) = osobny follow-up (dotyka ETA
= poza zakresem tmux 30). Zakres tej sesji = TYLKO kolejność.

**Walidacja D:** py_compile OK; import courier_orders z worktree OK; regresja
courier_api (patrz sekcja niżej). Konwergencja ON vs OFF mierzona na korpusie —
gałęzie 2/3 rzadkie na żywo, więc materialność ograniczona (gałąź 1 dominuje).

## B — panel `nadajesz_clone/panel/backend/.../fleet_state._build_route` (PRZYGOTOWANE)

**NIE APLIKOWANE** (cross-repo, osobny deploy `nadajesz-panel.service`, własny venv,
edytowane przez RÓWNOLEGŁE sesje CC — [[feedback-multisession-shared-deploy]]).
Parytet konsola↔kanon JUŻ zielony (monitor Q3 `mismatches=0` + `test_route_order_
parity_golden`), więc migracja B = SAFE refaktor (0-diff z konstrukcji), nie zmiana
zachowania.

**Granica delegacji (kolejność deleguje, ETA zostaje):** w `_build_route` blok
`order = ...` (fleet_state ~442-464: `_order_from_plan_seq` + coverage / carried-first
rebuild) → zastąp delegacją. Wszystko PO (ETA OSRM `_eta_chain`, floory, monotonic,
wrapping) ZOSTAJE lokalne.

**Patch (do zastosowania w OSOBNYM cyklu, za ACK + koordynacją sesji panelu):**
```python
# fleet_state.py — import (góra pliku; panel backend NIE ma scripts/ na sys.path →
# wymaga wpięcia ścieżki, jak narzędzia parytetu):
import sys
if "/root/.openclaw/workspace/scripts" not in sys.path:
    sys.path.insert(0, "/root/.openclaw/workspace/scripts")
try:
    from dispatch_v2 import route_order as _route_order
except Exception:
    _route_order = None

# w _build_route — zamiast bloku `order = _order_from_plan_seq(...) / carried-first`:
order = None
if _route_order is not None and flag("ROUTE_ORDER_UNIFIED"):
    try:
        order = _route_order.order_podjazdy(
            bag, plan_doc,
            plan_aware=flag("PLAN_AWARE_PODJAZDY"),
            trust_canon=(trust_canon_ok and flag("TRUST_CANON_ORDER")))
    except Exception:
        order = None            # fail-soft → lokalna kopia niżej
if order is None:
    ... # DOTYCHCZASOWY blok (lustro) NIETKNIĘTY jako fallback
```

**Wymagania przed flipem B (C2/C5):**
1. **Ścieżka importu:** panel backend (`.venv` uvicorn) musi mieć `scripts/` na
   sys.path w PROCESIE (dziś dispatch_v2 dostępny tylko przez subprocess). Weryfikacja
   OSIĄGALNOŚCI gałęzi na żywej konfiguracji (C5).
2. **Flaga panelu** `PANEL_FLAG_ROUTE_ORDER_UNIFIED` (env drop-in `nadajesz-panel.
   service.d`), OFF default.
3. **Parytet-siatka:** `tools/route_order_live_parity_check.py` (już istnieje) +
   golden `test_route_order_parity_golden` — czerwony blokuje.
4. **Koordynacja multi-sesja** (C1/C12) — panel edytowany równolegle; deploy seryjny.
5. Po flipie B + stabilizacji: usuń lokalną kopię `_order_from_plan_seq`/`_pickup_runs`
   z fleet_state → INV-SRC-ROUTE-ORDER domknięty KONSTRUKCYJNIE (3 repa, 1 reguła).

## Kolejność domknięcia (dźwignia P3)
1. ✅ moduł `route_order` + dowód 0-diff (S30A).
2. ✅ migracja D (gałąź 1 przez re-eksport; 2/3 za flagą OFF; twin scalony).
3. ⏭ flip app `ENABLE_ROUTE_ORDER_UNIFIED` (następny cykl, ACK, 2 dni obs).
4. ⏭ migracja B (patch wyżej, osobny cykl, ACK, koordynacja panelu).
5. ⏭ usunięcie kopii fallback (apka `_repair`, panel `_order_from_plan_seq`) → INV-SRC 🟢.
