# HANDOFF → następna sesja (po sesji dziennej 03.07, zamknięta ~13:40 UTC)

**Od:** sesja dzienna 03.07 (werdykt perf + 3 fale + L7×4 + deploy za ACK). **HANDOFF_tmux14_rano.md = SKONSUMOWANY W CAŁOŚCI** (zad.1 werdykt zrobiony, zad.2 REFUTED rano, zad.3 naprawiony rano).
**READ ORDER:** (1) ten plik; (2) tracker `eod_drafts/2026-07-02/ZIOMEK_STAN_AUDYTY_1i2.md` (góra + §2) + `ZIOMEK_FINDINGS_LEDGER.md` **§22**; (3) protokół `memory/ziomek-change-protocol.md`. Zawsze: `git log --oneline -10` + `atq` + `tmux ls` (⚠ multi-sesja: żyje worktree `wt-audyt` innej sesji — nie ruszać).
**Baseline regresji kanonu (03.07 ~13:05): 4165 passed / 0 failed / 26 skipped (23+3 okna czasowe preshift) / 11 xfailed.** HEAD `a6473a4` (pushed).
**Stan flag po sesji:** `ENABLE_R_DECLARED_TRIPWIRE=true` + `ENABLE_SPLIT_LAYER_GUARD=true` (flip 13:20, restart shadow 13:18 + pw 13:19 czyste; backup `flags.json.bak-pre-l7-flips-20260703`) · `ENABLE_FLAG_FINGERPRINT_GUARD_ALERT=false` (jawnie) · `ENABLE_PERF_LAZY_MEMBERS=true` (werdykt GO).

## ZADANIE 1 — odczyty DZIŚ wieczór (read-only → decyzje)
- **at-200 18:10 UTC** (objm L6.D peak-verdict checkpoint) i **at-201 19:00 UTC** (werdykt L2.1 sentinel-ingest, `l21_flip_review`): odczytać logi/`dispatch_state/scheduled_flips*`, wpisać werdykty do notatek tematycznych ([[l21-sentinel-ingest-2026-07-01]]) + tracker. **at-201 czysty → ODBLOKOWANY sprint L6.C** (największy P0: geometria w selekcji + de-pile, C2+C3 RAZEM — spec w recon sesji 03.07 / `backing/F_roadmap.md:172-179`; protokół #0 + ACK).
- Sprawdzić strażniki po flipach: `wc -l dispatch_state/r_declared_tripwire.jsonl` (oczekiwane: wpisy dla znanych ~11 naruszeń przy upsertach) + `split_layer_guard.jsonl` (oczekiwany wpis kind=verdict_write_outside_l5 przy 1. FEAS_CARRY_READMIT) + `grep frozen_total_duration dispatch_state/bug4_reseq_shadow.jsonl | wc -l` rośnie.

## ZADANIE 2 — instalacja fingerprint-guard ✅ WYKONANE 13:32 (ręką Adriana)
Timer enabled (co 30 min), 1. tick = level=OK, 4/4 procesy, fingerprint 102 flagi spójny, 0 drift/cold. ZOSTAJE: po ≥1 dniu log-only → flip `ENABLE_FLAG_FINGERPRINT_GUARD_ALERT=true` za osobnym ACK (Telegram edge-triggered).

## KALENDARZ weekend
- **So 04.07 08:00 at-209**: bramkowany flip `ENABLE_PERF_SLO_ALERT` (ACK już jest; spodziewany 1. alert = peak-p95 breach — świadome). · **12:35/12:50 at-202/203** auto-flipy L3/L4 · **14:30 at-204** verify · **werdykt S1** (sla_anchor 2 dni) → **flip O2 K1 za ACK** (HANDOFF_po_dniu_0207 §2 poz.1) · bramka **L5** (ETA load-aware).
- **Pn 06.07**: at-205/206 GC-real+verify · at-208 review λ=0 · weryfikacja H2 Parys.
- **~10.07**: świeża mapa 0a (`eta_truth_map --since 2026-07-02T12:00`) → Fala A roadmapy deep-dive · events.db 90d.

## KOLEJKA po L6.C (z recon 03.07 + bramki)
USE_V2_PARSER→ETAP4 (Fala D, jawnie OSTATNIA; wymaga OPEN-1 domkniętego Falą C/L3 + shadow-compare v1↔v2 + mapa wołających OPEN-2; restart pw off-peak) · L7.2 (po L4/L5) · L7.6 (⛔D5 measure-first) · L7.7 (⛔ACK VETO) · free_at_min→L4 (recon w raporcie pasa l73; 🔴 replay+ACK) · **security P0 = Adrian krok 0 (Hetzner Cloud FW)** · fale C/D migracji flag (pod-ACK) · O2 K2 (po ustabilizowaniu L3 + parytety z `kind_review_o2_k2_prereq_raport.md`).

## ⭐ Nowe fakty decyzyjne z tej sesji (nie zgub)
1. **Ogon perf w peaku NIE jest z IO** (peak p95 1810≈1847 mimo −27% p50) — osobna fala perf: profil compute pod obciążeniem (OR-Tools/OSRM/pool).
2. **R-DECLARED łamane w ~6,5% zleceń** (11/168) — tripwire ON zbiera; po tygodniu danych → decyzja co z tym robić (źródło: koordynator? gastro? parser?).
3. **feas-carry: projekcja regret przeszacowana** (83,7% wybaczeń fizycznie nieszkodliwych; realne re-admity 0/4 wykonane) — KAŻDY przyszły re-flip #483000 tylko przez `tools/feas_carry_outcome_join.py`.
4. **Tripwire objektywu bug4 żyje** — oracle reverdict „oceniale" rośnie od 0; przy najbliższym re-werdykcie bug4 użyć osi OBJEKTYWU, nie drive.

## MINY sesji (nie powtórz)
- Merge ZAWSZE z KANONU; worktree per pas; `git -C <kanon> status` czysty = DoD pasa (wszystkie 7 pasów sesji przeszło czysto).
- Regresja w worktree przez pkgroot-symlink daje 3 fałszywe FAIL script-runnerów (test_f4_courier_pos_pickup_proxy / test_panel_aware_availability / test_panel_packs_bag_reconstruction) — weryfikuj na kanonie zanim uznasz regres.
- Nowy klucz w flags.json → ratchet `test_flag_doc_coverage` wymaga wpisu w `ZIOMEK_LOGIC_REFERENCE.md` (dobrze — nie obchodź baseline'em).
- Nowa trwała usługa systemd = classifier wymaga JAWNEJ zgody Adriana (skrypt-plik + `! bash` = ścieżka robocza).
