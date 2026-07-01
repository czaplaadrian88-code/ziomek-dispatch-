# HANDOFF — Faza 3 audytu spójności Ziomka (stan po sesji 01.07 wieczór)

**Dla nowej sesji CC bez kontekstu. Przeczytaj to → potem READ ORDER niżej → kontynuuj Fazę 3 falami.**

---

## GDZIE JESTEŚMY (relay: memory `ziomek-unified-audit-2026-06-30` — aktualizuj tam status po swojej fali!)

- **Faza 0-1 ✅** — audyt spójności (7 deliverables `eod_drafts/2026-06-30/FAZA1_00..06` + backing; commit `43cfb28`).
- **Faza 2 ✅ 01.07** — Adrian ZATWIERDZIŁ 8 kontraktów W CAŁOŚCI (FAZA1_04 = obowiązujący cel) + kolejność napraw + szkielet architektury w git (commit `76daf25`: `ZIOMEK_ARCHITECTURE.md` / `ZIOMEK_INVARIANTS.md` / `ZIOMEK_DEFINITION_OF_DONE.md` / `tools/entropy_dashboard.py` — kanon obok Przykazania #0 w CLAUDE.md).
- **Faza 3 🟡 W TOKU** — fale wykonane w sesji 01.07 wieczór:
  - **L1.1 serializer completeness ✅ LIVE** (commit `85d92f7` + tag, dispatch-shadow zrestartowany 20:10 UTC za ACK). Allowlist 35 prefiksów → deny-lista `_METRICS_EXCLUDE`; 38 ginących kluczy (14 HARD: `sla_violations` detail / `eta_source` / `r6_*` / `c2_*` / `d2_*`) dociera do ledgera. Memory: `serializer-completeness-l11-2026-07-01`.
  - **L6.A PoC-MIN route-order ✅** (przed expiry monitora 10.07): golden harness parytetu KONSOLA==KANON — korpus `dispatch_v2/tests/golden/route_order_corpus.json` 13 cases / parytet 13/13 (silnik `4824d93`, panel `2b3ff12` branch coordinator-console); martwa 5. kopia panelsync USUNIĘTA (`0c914c4`); pin `PICKUP_MERGE_MIN` (silnik+fleet_state+tsx); fail-loud importu apki (`290dd09`, inert do restartu courier-api). Memory: `route-order-golden-l6a-2026-07-01`.
  - Higiena przy okazji: flag-doc stale baseline (`a4cb4ef`), 2 pre-existing czerwone testy panelu naprawione u źródła (`d7b68bd` — watcher atjob = stary kontrakt sprzed decyzji Adriana 15.06; heatmapa = bomba czasowa 28d).
- **Regresje na koniec sesji: silnik 3623/0 · panel 1067/0 · courier_api 130/130.**

## ⏳ DO ZROBIENIA NAJPIERW (rano 02.07)

1. **Weryfikacja LIVE L1.1** — od restartu (01.07 20:10 UTC) do końca sesji ZERO nowych decyzji (noc). Na pierwszych świeżych rekordach:
   `grep -c '"eta_source"' /root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl` (okno PO restarcie) — musi być >0 (przed L1.1: 0/858). Klucze warunkowe (`r6_paczka_exempt_oids`, V328 `fallback_*`) >0 tylko gdy warunek zaszedł. Wynik dopisz do memory `serializer-completeness-l11-2026-07-01`.
2. **Bramka O2 02.07** — review at-168/at-200 + bundle_calib próg (patrz memory `top10-progressive-potential-2026-06-29` + `shadow-jobs-registry`). L1.1 był warunkiem — SLA-detail w master-ledgerze zbiera się dopiero OD 01.07 20:10.

## ➡ NASTĘPNA FALA: L2.1 sentinel-ingest (P0 SILNIK — jedyny żywy pożar)

- **Co:** kurierzy z pozycją-sentinelem `(0,0)` wypadają z puli (V328 wyrzuca zajętego → pool_feasible=0 → geometria-ślepy best-effort pile-on). LIVE harm: 2046+14456 zdarzeń, **8 ofiar/dzień** (30.06).
- **Fix U ŹRÓDŁA:** wepnij ISTNIEJĄCY walidator `common.py:513` u INGEST pozycji (nie buduj nowego); **bliźniaki haversine↔osrm RAZEM**; sweep miejsc `if coords:` → `_valid()`. Detal: `FAZA1_01_mapa_antywzorcow` + `eod_drafts/2026-06-30/backing/B15/B16` + unified-audit K5. 6/12 żywych trucizn = `courier_resolver.py` (no_gps/pre_shift/synthetic BIALYSTOK_CENTER) — patrz memory `ziomek-architecture-skeleton-2026-06-30` (fix = typ Unknown, filar #3; ale L2.1 = najpierw chokepoint walidacji u ingest).
- **Rygor:** pełny protokół ETAP 0→7 z `memory/ziomek-change-protocol.md` (WKLEJ PROMPT na start!) + prosty polski „co/wpływ/jak bezpiecznie" PRZED kodem + **ACK Adriana na flip/restart** + deploy off-peak (>14:00 lub wieczór) + replay dowód POZYTYWNEGO wpływu + pełna regresja vs baseline **3623 passed** + strażnik/inwariant blokujący nawrót + `tools/entropy_dashboard.py` re-run (metryka sentinel MA MALEĆ).

## PO L2.1 (kolejność zatwierdzona przez Adriana)

`L0 strażnicy-shadow (F6, celowane w C-FEASIBILITY — sloty 🔴 w ZIOMEK_INVARIANTS.md) → L3 plan_recheck nie-cofa (F2) → L4 available_from 1 źródło (F1, Q1/Q2 już ACK) → L5 ETA load-aware (F4, ⛔HARD, bramka 04.07) → L6 reszta (P5 konsola-import / P1+P2 ekstrakcja rdzenia route_order / P3 — HARD, osobne pod-ACK; plan `backing/F_poc_plan.md`) → L7 → L8`. Roadmapa: `FAZA1_05_roadmapa_poc.md`.

## ⛔ BLOKADY / MINY (nie rusz bez spełnienia)

- **NIE flipuj `PENDING_RESWEEP_LIVE`** — `global_allocate` geometria VOID (certyfikuje ślepą liczbę).
- **C2 re-enable Telegrama** dopiero po L7.5 (fcntl `pending_proposals` 3-writer).
- **Monitor route-order wygasa 10.07 SAM — NIE przedłużać** (golden harness już go zastąpił; jak chcesz odświeżyć korpus: `nadajesz_clone/panel/backend/.venv/bin/python dispatch_v2/tools/route_order_golden_corpus_gen.py` PANEL venv-em).
- **Multi-sesja C1/C1-git:** `tmux ls` + cudze `.bak-*` przed dotknięciem; commit = add+commit JEDNYM ruchem po jawnych ścieżkach; po commicie `git show HEAD --stat`.
- Zmiana semantyki KOLEJNOŚCI tras → re-generuj golden korpus RAZEM ze zmianą (czerwony golden bez re-generacji = regres).

## READ ORDER (na start sesji)

1. `~/.claude/.../memory/MEMORY.md` ładuje się sam — sekcje AUDYT ZUNIFIKOWANY + SZKIELET + wpisy L1.1/L6.A.
2. `memory/ziomek-change-protocol.md` — WKLEJ PROMPT przed dotknięciem silnika.
3. `memory/ZIOMEK_REGULY_KANON.md` — hierarchia reguł przed zmianą feasibility/R6.
4. `dispatch_v2/ZIOMEK_ARCHITECTURE.md` + `ZIOMEK_INVARIANTS.md` + `ZIOMEK_DEFINITION_OF_DONE.md` — kanon (świeżo w repo).
5. `eod_drafts/2026-06-30/FAZA1_00_RAPORT_KONCOWY.md` §2/§4/§6 + `FAZA1_05_roadmapa_poc.md`.

**Rytm Adriana:** przed każdą falą prosty polski „CO + WPŁYW + JAK BEZPIECZNIE" → GO → ETAP 0 recon (linie dryfują, świeży grep!) → kod+testy → FLIP/restart TYLKO za ACK → po fali: entropy_dashboard + wpis do memory + aktualizacja relay w `ziomek-unified-audit-2026-06-30` + status w `todo_master.md`. Nie pytaj „czy warto naprawić" — napraw i domknij; ACK tylko dla ryzykownych/nieodwracalnych.
