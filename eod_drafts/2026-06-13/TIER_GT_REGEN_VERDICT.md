# TIER-01 + #21 + FAIL-04 regen — werdykt (2026-06-13, sesja auton/tier-gt-regen)

Regen STALE danych statystyk wg AUDIT_FIX_PLAN_2026-06-10 (TIER-01 reszta) +
tech-debt #21 (drive_min GT) + FAIL-04 prep-variance meta. **Tylko DANE, zero
zmian logiki** (common.py DWELL/SPEED #179 b16100a NIETKNIĘTE).

Źródło świeże, autonomicznie dostępne: **Postgres `nadajesz_panel.delivery`**
(papu-postgres :5433, 57k wpisów, ciągły ingest do dziś; cid przez
`courier.external_id`) + **`/root/panel_history_new/*.csv`** (eksport gastro z
`czas restauracji`, do 2026-06-09).

## 1. TIER-01 — regen stale percentyli ✅

**Co:** percentyle w `courier_tiers.json` (orders_per_wave_p50/p90/p99,
bag_time_p90_min, speed.delivery_time_p90_min, bundle.*) były z V3.19g zamrożone
**2026-04-20** (plik `/tmp/v319g_..._preview.json` dawno zniknął). Zregenerowane z
`delivery` (ostatnie 60 dni, wave-gap=8min jak speed_tier_tracker, eligibility
≥50 zleceń).

**Ważne ustalenie konsumentów:** produkcja czyta z `courier_tiers.json` TYLKO
`bag.tier` (label) + `bag.cap_override` (per-pora capy, dispatch_pipeline:3914).
Percentyle = **dekoracyjne/obserwacyjne** (analityka, ludzki widok tierów) — żaden
kod scoringu/feasibility ich nie czyta. Dlatego regen NIE zmienia zachowania
dispatchu; odświeża tylko obraz statystyk. **Labels tierów NIETKNIĘTE** (E5/E7 +
panel #179; ostatni edit 2026-06-10 `370→std/207→slow/289→std+`).

**Diff (przed→po, n=34 kurierów ze stats):**
| cid | kurier | tier (bez zmian) | deliv_p90 stale→regen |
|---|---|---|---|
| 123 | Bartek O | gold | 32.0 → 26.0 |
| 179 | Gabriel | gold | 36.0 → 25.0 |
| 441 | Sylwia L | std+ | 35.0 → 27.0 |
| 289 | Grzegorz W | std+ | 34.0 → 26.0 |
| 207 | Marek | slow | 39.0 → 38.5 |

Trend: flota szybsza niż w oknie 04.2026 (krótsze p90, niższe orders_per_wave_p90
3→2/3 — mniej bundlowania w obecnym składzie). 10 kurierów <50 zleceń = bez stats
(label zostaje).

**Pliki:**
- `eod_drafts/2026-06-13/courier_tier_stats.json` — NOWY plik danych (per-cid stats + _meta).
- `eod_drafts/2026-06-13/courier_tiers.regen.json` — kopia `courier_tiers.json` z
  odświeżonymi percentylami, **tier/cap_override zachowane**, cid=61 usunięty,
  `_meta.last_tier_stats_regen` dopisane.
- generator: `eod_drafts/2026-06-13/regen_tier_stats.py` (stdlib + psql, dispatch venv).

## 2. TIER-01 — usunięcie cid=61 (Krystian) z ground-truth ✅

cid=61 = **ex-courier od 2026-04-23** (`inactive:true` w pliku), **0 dostaw w 60
dniach** (Postgres potwierdza). Mimo to był nadal `gold` w
`_meta.tier_ground_truth_cids` + miał martwy gold-entry z percentylami →
zatruwał agregaty kohorty gold i utrzymywał fikcyjny gold w GT.

**Co zrobione:**
- usunięty z `_meta.tier_ground_truth_cids` i jako entry w `courier_tiers.regen.json`;
- usunięty z hardcoded `TIER_GROUND_TRUTH` w `build_v319h_courier_tiers.py` (komentarz
  z uzasadnieniem) — żeby przyszły rebuild też go pominął.

## 3. #21 [P2] — backfill ground-truth drive_min ✅ (już operacyjne)

**Ustalenie:** "ground-truth drive_min" to NIE `courier_ground_truth.json` (to live
GPS status-store, single-writer courier-api — NIE dotykać). To
**`drive_min_enriched.jsonl`** (#21 Opcja C), produkowany przez żywy cron
`dispatch-shadow-enrichment.timer` (5 min, `tools/shadow_outcome_enricher.py`):
joinuje shadow_decisions z `events.db audit_log` → realny `actual_assign_to_pickup_min`.

**Stan (zmierzony):**
- 3828 wpisów, świeże (ostatni 2026-06-12 21:20), cron `active`+`enabled`.
- **`actual.actual_assign_to_pickup_min` (ground-truth) = 100% (3828/3828).**
- `predicted.drive_min` = 3619/3828; **209 brakuje** — to verdykty `early_bird`/
  `no_solo_candidates` (maj/wczesny czerwiec) gdzie shadow `best` nie miał ani
  drive_min ani travel_min (żadna trasa nie była liczona) → **nieodtwarzalne i
  bez sensu** (brak predykcji = nic do backfillu).
- Źródło NAPRAWIONE: ostatnie 2000 PROPOSE mają drive_min+travel_min 275/275 (100%).
- Backlog enrichera = 0 (dry-run 720h: enriched=3, reszta deduped/pending).

**Werdykt #21:** backfill GT drive_min **działa autonomicznie i jest aktualny** —
żadna akcja regen nie była potrzebna poza potwierdzeniem zdrowia. Luka 209 jest po
stronie predykcji dla verdyktów bez trasy (poprawne). NIE pisałem do live
`drive_min_enriched.jsonl` (utrzymuje go cron — ręczny zapis = duplikaty).

## 4. FAIL-04 — regen prep-variance meta ✅

`restaurant_meta.json` (źródło `prep_variance_min.median` + `flags.prep_variance_high`
dla PREP_VARIANCE_ANOMALY shadow) policzony z CSV **2026-04-12** → stale.
Zregenerowany TYM SAMYM generatorem (`tools/gap_fill_restaurant_meta.py`, format 1:1)
na świeżym scalonym CSV `/root/panel_history_new/*.csv` (dedup po nr zlecenia,
najświeższy plik wygrywa — wzorzec restaurant_prep_bias.py).

**Diff (stale→regen):**
| metryka | stale 2026-04-12 | regen 2026-06-09 |
|---|---|---|
| restauracje | 68 | 87 |
| dostarczone zlecenia | 23 607 | 55 356 |
| prep_variance_high | 19 | 29 |
| fleet_prep_variance_median | 13.0 | 14.0 |

Nowo flagowane high (14): Baanko, Burger Station, Dr Tusz, HoNoTu!, Pablos kebab,
Piri Piri, Ramen Base, Restauracja Kumar's, Street Mama Thai, Szklanki Talerze,
Bar Eljot, Zachodnia Kawiarnia i Bistro… Zeszły z high (2): Bar Merino, Pawilon Towarzyski.

**Pliki:**
- `eod_drafts/2026-06-13/restaurant_meta.regen.json` — gotowy do podmiany (sandbox).
- generator: `eod_drafts/2026-06-13/regen_restaurant_meta.py`.

Test `test_prep_variance_anomaly_fail04.py` 18/18 PASS na zregenerowanym schemacie
(format zgodny).

## Podmiana na produkcję (poza tą sesją — wymaga człowieka/ACK)

Sesja pisała WYŁĄCZNIE do worktree/eod_drafts. Live `dispatch_state/*` NIETKNIĘTE.
Podmiana (atomic, z .bak), gdy ACK:
```
cp dispatch_state/courier_tiers.json dispatch_state/courier_tiers.json.bak-pre-regen-2026-06-13
cp eod_drafts/2026-06-13/courier_tiers.regen.json dispatch_state/courier_tiers.json   # hot-reload mtime
cp dispatch_state/restaurant_meta.json dispatch_state/restaurant_meta.json.bak-pre-regen-2026-06-13
cp eod_drafts/2026-06-13/restaurant_meta.regen.json dispatch_state/restaurant_meta.json  # cache mtime-invalidated
```
courier_tiers: `_load_courier_tiers` ma mtime-invalidation (bez restartu).
restaurant_meta: `_load_restaurant_meta_cached` analogicznie. #21/drive_min: nic — cron żyje.

## Czego NIE dało się zregenerować autonomicznie

- **prep_variance z Postgres `delivery`** — kolumna `ready_at` (deklarowana gotowość
  restauracji) jest **NULL dla 100% wierszy**; panel nie wypełnia jej do `delivery`.
  Dlatego FAIL-04 liczony z CSV gastro (`czas restauracji`), nie z Postgres. Działa,
  ale to jedyne źródło z deklarowaną gotowością (CSV do 2026-06-09; nowsze dni
  doleci następnym eksportem panelu).
- 209 `predicted.drive_min` w drive_min_enriched — nieodtwarzalne (brak trasy w źródle).
