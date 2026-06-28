# HANDOFF — sesja 20 CLOSE (2026-06-28 noc) → jutro dokończyć

**Lane:** czasówka-w-uwagach + (nowo) projekt 5b (GPS-geofence dostawy). **Pamięć żywa:** [[czasowka-uwagi-deadline-2026-06-28]] (auto-load). Protokół: `memory/ziomek-change-protocol.md`.

## TL;DR — gdzie jesteśmy
1. **czasówka-w-uwagach: ingest+parser+oracle = LIVE + zacommitowane** (flaga `ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW` ON). Observability-only, zero wpływu na decyzje.
2. **Stage 4 (decyzyjne) = ZAPROJEKTOWANE, NIE zbudowane** — czeka na czysty ground truth (#5) + decyzje + ACK.
3. **5b (GPS-geofence dostawy) = ZAPROJEKTOWANE (ETAP 0/1), NIE zbudowane** — czeka na koordynację z sesją 18 (kontrakt) + ACK + cross-repo build.
4. **⚠ RESTART panel-watcher ODROCZONY** — `common.py`/`sla_tracker.py` dirty (WIP #5 sesji 18). Mój fix precyzji committed, wejdzie live na NASTĘPNYM CZYSTYM restarcie.

## CO ZACOMMITOWANE (master, origin w sync)
| commit | tag | co |
|---|---|---|
| `8220220` | `czasowka-uwagi-shadow-2026-06-28` | Stage 1a: parser `czasowka_uwagi.py` + ingest `panel_client.normalize_order` + persist `state_machine` + flaga ETAP4 + oracle + 11 testów |
| `15e39bb` | `czasowka-uwagi-stage2-2026-06-28` | Stage 2: broaden recall (odwrotna kolejność/słowa/stem/literówka) + separator `,;` + sanity-gate deadline≥pickup |
| `7e6ded3` | `czasowka-uwagi-precision-niewczesniej-2026-06-28` | precyzja: „nie wcześniej"=earliest-bound→None + oracle parse_miss honest |

**LIVE teraz:** flaga ON, parser **Stage-1a** biegnie w panel-watcher (restart z 21:52). **Stage-2 + precyzja są committed ale NIE live** (restart odroczony — patrz niżej). Pole `delivery_deadline_uwagi` zapisywane do orders_state na nowych NEW_ORDER.

## ⚠ RESTART — dlaczego odroczony + jak dokończyć
`common.py` + `sla_tracker.py` są DIRTY w WSPÓLNYM working-tree (WIP #5 sesji 18, uncommitted). Restart panel-watcher (importuje `common.py`) = **deploy cudzego WIP**. **KROK JUTRO:** gdy sesja 18 zacommituje #5/5a → **jeden czysty restart** `sudo systemctl restart dispatch-panel-watcher` aktywuje Stage-2+precyzję (moje) ORAZ 5a (18). Weryfikacja: log czysty + `delivery_deadline_uwagi` na świeżych zleceniach. Rollback: flaga OFF (hot) / `.bak-pre-czasowka-{uwagi,stage2}-2026-06-28`.
**LEKCJA (do protokołu, wzorzec shared-tree):** restart importera `common.py` ładuje WIP każdej sesji; commituj jawne pliki, NIE restartuj póki cudze pliki dirty.

## MULTI-SESJA (C1 — stan na 28.06 noc)
- **Sesja 18:** #5 (GPS-confirm delivered: 5a walidator `sla_tracker`+`courier_ground_truth`, uncommitted) + lista „kłamiących mierników" (#6/#9/bug4/b_route/drive_speed/objm_lexr6). Dzieli ze mną **kontrakt ground_truth** (5b touchpoint).
- **Sesja 15:** feas_carry/would_hard_cap/conftest + audyt multi-city.
- **Sesje 17/19:** reassignment (NIE tykać).
- **Moje (20):** czasówka + 5b-design. `czasowka_uwagi.py`/`panel_client.py`/`state_machine.py`/`tools/czasowka_uwagi_oracle.py` = MOJE, brak kolizji. **NIE tykać** `common.py`/`sla_tracker.py`/`courier_ground_truth`/courier-api (18).

## STAGE 4 (czasówka decyzyjne) — projekt gotowy
Spec: `eod_drafts/2026-06-28/CZASOWKA_UWAGI_STAGE4_DESIGN.md`. Skrót:
- Deadline = DRUGIE absolutne ograniczenie (nie modyfikacja R6; R6 dalej tier-aware 35/40). Always-propose + R27 nietykalne.
- Mapa: OrderSim `delivery_deadline_uwagi` (route_simulator:208 + dispatch_pipeline:3016/3417 + bag-members) → 3 bliźniaki SLA (`_count_sla_violations:635` / `feasibility_v2:825/1135` / `plan_recheck._o2_key:683`) → serializer A+B (wzorzec #16 jawnie) → flaga `ENABLE_CZASOWKA_UWAGI_DEADLINE_PENALTY` + scoring gradient.
- **Brama:** czysty ground truth (#5 sesji 18) + decyzje D1-D5 Adriana + replay **≥2% PEWNY netto** (Adrian 28.06; przy 2% szum przepycha zły flip → czysty pomiar obowiązkowy).
- **Decyzje Adriana (D1-D5):** D1 HARD-vs-SOFT (rekom. SOFT/scoring), D2 kara gradient 3-bucket, D3 próg breach tol ~3-5min, D4 paczki (rekom. dotyczy), D5 okno 14 dni.

## 5b (GPS-geofence dostawy) — projekt gotowy
Spec: `eod_drafts/2026-06-28/GPS5b_DELIVERY_GEOFENCE_DESIGN.md`. Skrót:
- **Po co:** #5 audytu = delivered_at to klik ±3min (0/377 GPS). Mój audyt czystości: szum ±3min = **17,6pp** ≈ 9× próg 2% → realny 2% NIEMIERZALNY na button-press. 5b daje fizyczną prawdę.
- **Backend-geofence ODPADA:** tylko 5,3% realnego GPS w `fleet_position_history` (reszta interpolacja) → circular.
- **Apka JUŻ geofence'uje ODBIÓR** (`/root/courier-app` `AutoStatusEngine.kt`: 3→4→5, ENTER 150m/EXIT 230m/accuracy-gate, `OrderGeo` niesie `delLat/delLon`). **Świadomie kończy na 5; status 7 ręczny suwak = ROOT „delivered=klik".**
- **Projekt:** dołóż geofence punktu DOSTAWY → `gps_arrived_at` (measurement-only, BEZ auto-7) → CourierApi → courier-api zapis ground_truth → 5a(18)+oracle czasówki konsumują (podmiana button-press → fizyczne przybycie → kasuje 17,6pp szumu).
- **Brama:** (1) **koordynacja z 18** (kontrakt ground_truth: nowe pole `gps_arrived_at` vs `delivered_at`; courier-api = obszar 18), (2) ACK D1 (measurement-only vs auto-confirm-7), (3) cross-repo Android build+soft-release. Apka repo CZYSTE (brak kolizji).
- **Opcjonalnie root:** czemu tylko 5% realnego GPS dociera (apka/upload/bateria) — większy wątek.

## PIERWSZE KROKI JUTRO (sugestia)
1. **Reconcile multi-sesja** (`tmux ls` + `git log -3` + `git status` shared files) — czy 18 zacommitowało #5/5a (→ czysty restart mojego Stage-2).
2. **Decyzje Adriana:** 5b D1/D2 (measurement-only? kontrakt ground_truth?) + Stage-4 D1-D5.
3. Jeśli 5b GO: koordynacja z 18 na kontrakt → ETAP 1 apki (`AutoStatusEngine`+`LocationService`+`CourierApi`) → design lock → build (apka, JVM-testy w `AutoStatusEngineTest.kt`) → backend receiver (z 18) → release.
4. Stage 4 czasówki dopiero po wylądowaniu #5 (czysty ground truth).

## ORACLE czasówki (do pomiarów) — gotowy
`tools/czasowka_uwagi_oracle.py` (real delivered_at vs deadline; raportuje effective/suspekt/recall, segment elastic/czasówka, wrażliwość tol). Najnowszy wynik: 51 effective deadline'ów, elastic n=9 mediana −2,6min / 33% late>3min p90 +18,5 (czasówka n=42 43% late). Werdykt: zjawisko realne, ALE pomiar na button-press nie udźwignie 2% (→ 5b). Rows: `czasowka_uwagi_oracle_rows.jsonl`.

## BACKUPY / ROLLBACK
- `.bak-pre-czasowka-uwagi-2026-06-28` (common/panel_client/state_machine/flags.json — Stage 1a)
- `.bak-pre-czasowka-stage2-2026-06-28` (czasowka_uwagi/panel_client/oracle — Stage 2)
- Rollback flagą: `ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW=false` (hot) / `git revert 7e6ded3 15e39bb 8220220`.
