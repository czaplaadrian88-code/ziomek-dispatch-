# L01 — JEDEN REJESTR FINDINGÓW (PAS 0.A audytu 2.0)

**Data:** 2026-07-02 · **Tryb:** READ-ONLY · **Pas:** L01-rejestr-findingow
**Produkt główny:** `../ZIOMEK_FINDINGS_LEDGER.md` (pełna master-tabela + dowód grep + statystyka).
**Ten plik:** streszczenie + lista SIEROT (open, właściciel=NIKT) — najważniejszy produkt pasa.

---

## Problem, który pas rozwiązuje (anty-K1 dla samych audytów)

Findingi Ziomka żyły w **≥4 nieskorelowanych rejestrach** bez wspólnego statusu → sieroty. Design 2.0 §0.2a podał flagowy dowód: potwierdzony P2 `osrm-fallback-double-traffic` (27.06) **nie istnieje w korpusie 30.06 ani w roadmapie L0-L8**. Zadanie: scalić 4 audyty w JEDEN rejestr ze statusem + właścicielem i wyłapać KAŻDĄ sierotę.

**Metoda dowodowa (nie deklaracja):** dla każdego kandydata-sieroty grep CAŁEGO korpusu 30.06 (71+ plików `FAZA1_*`+`ZIOMEK_*`+`AUDYT_preshift*`+`backing/*`) po dystynktywnym terminie + kontrola pozytywna (`carried`=57, `sentinel`=53, `serializer`=27 — grep działa). 0 trafień = nieskonsumowany. Owned potwierdzone przez mapę L0-L8 (`FAZA1_05`) + stan fal z memory (L1.1/L6.A LIVE 01.07, L2.1 kod-ready).

## Scalone źródła

| Audyt | Wkład | Los w rejestrze |
|---|---|---|
| **27.06** deep audit | **81 findingów** (2 P1, 31 P2, 48 P3) | 65 owned/closed (→ L0-L8/bramki), **16 ORPHAN** |
| **30.06** Faza 1 | 53 rooty→26 przetrwałych + 13 klastrów konfliktu + 49 przyrządów (19 VOID) + allocation(11) + K1-K7 | 26/26 rootów **owned** przez L0-L8; refuted/deferred też mają warstwę |
| **05.07** architektura | 20 R + F1-F20 + RC1-RC7 + M1-M5 | quick-wins partial; **oś strukturalna open, proposed-2.0-nie-ACK**; 1 żywa sierota |
| **(d) memory** preshift/allocation | pion-audyty | scalone w K1-K7 = fundament F1-F7 |

## Statystyka (nagłówkowa)

- **Raw findingi (4 audyty, przed dedup): ~247.** Distinct rooty po cross-dedup: ~26 koherencji (30.06) + 7 RC (05.07, RC1≈K1) + sieroty.
- **OPEN BEZ WŁAŚCICIELA: ~27** = **16 cichych sierot 27.06** (nikt nie śledzi) + **1 żywa** (cod-weekly failed+silent) + **~10 strukturalnych 05.07** (znane, czekają na ACK 2.0). „Cichych z fix-właścicielem=NIKT" = **17**.
- **27.06 (81): 100% zmapowane** — 12 fixed-live (L1.1 serializer), 8 fixed-partial (L6.A route-order), 22 open-z-właścicielem, 2 refuted/closed, 4 LGBM-deferred-weak, 3 test-oracle, 5 dead/clutter... **16 ORPHAN**.

## ⭐ SIEROTY (open, właściciel=NIKT) — 16 z 27.06 + 1 żywa

**P2 (najważniejsze):**
1. **`osrm-fallback-double-traffic`** — `osrm_client.py` fallback liczy traffic ×2 → czasy ×1.5 → sztuczne breache R6 **gdy OSRM już kuleje**. grep double/fallback-traffic=0.
2. **`crg-ranking-bundle-skew-live`** — `courier_ranking.py` leaderboard na bundle-contaminated metryce → tier promote/retire na skażonych danych (= klasa 05.07 RC4). grep leaderboard=0.
3. **`pipe-postshift-gate-exclusion-gap`** — `post_shift_overrun_penalty` obniża score, ale brak w krotce wykluczeń bramki MIN_PROPOSE → cicho wpycha decyzję w KOORD-ciszę. grep gate-exclusion=0.
4. **`pis-closed-vs-orderids-source-divergence`** — `closed_ids`(DOM) vs `order_ids`(JS) niezależne → misclass cancel/return. grep=0 (closed_ids tylko o lag reconcile).
5. **`czas-postpone` klaster ×5** — `postpone_sweeper` cała ścieżka resolution-detection MARTWA (zły klucz `cid`, brak `raw`, verdict-dead-value, schema pending, zły nesting). Umbrella „martwy schema" = C2-mina (arms-on-re-enable) ale BRAK fix-właściciela.

**P3:**
6. `osrm-v2-shadow-aggregate-full-matrix` — sumuje całą macierz NxN nie legi planu.
7. `tk-shadow-entry-msgid-null` — shadow pending `message_id=None` → crash przy re-enable.
8. `tk-watchdog-keyerror-twin` — watchdog expired-loop bez guardu (bliźniak startup MA).
9. `crg-lastpos-ttl-savetime-staleness` — TTL last-pos od save-time nie observation-time.
10. `crg-gpsquality-anchor-ticktime` — teleport-anchor z tick-time nie GPS fix-time.
11. `pis-closedids-raw-html-input` — closed_ids skanuje raw html, order_ids clean (asymetria).
12. `crg-dedup-byname-bag-loss` (weak) — dedup usuwa same-name cid z bagiem (R5 badał seeding nie deletion).

**Żywa (05.07 F2/RC3, ground-truth 07-01):**
13. **`dispatch-cod-weekly.service`** = FAILED od `Mon 2026-06-29 06:00` (exit 1), **`OnFailure=` PUSTE → alert nie poszedł**. ANEKS zakładał „OnFailure jak inne 11 svc" — grunt-prawda OBALA. NIKT nie naprawia.

## Oś strukturalna 05.07 (open, „owner=2.0-proposed-nie-ACK" — efektywnie un-owned 2 mies.)

RC1 filesystem-as-IPC (brak Postgres/Redis — ground-truth) · RC4 JSONL/state unbounded (dispatch_state **1.2G**, logs **729M**) · single-server SPOF · RC3 brak alertów DANOWYCH/SLO (tylko latency_alarm) · RC5 brak state_io · RC6 replay-current-code · **bezpieczeństwo NIGDY nie audytowane** · systemd rozrost (**68 svc+61 timerów** vs 16+12) · 330 .bak. Design 2.0 §0.3: „RC1 = ten sam korzeń co K1 znaleziony 2× w 2 mies. = dowód, że strukturalny".

## Materialność (uczciwie)

Dla większości sierot 27.06 **NIE policzona** (audyt 27.06 deklarował ISTNIENIE ścieżki z lektury, nie „ile worków/dzień"). Ground-truth policzone tylko dla żywych: cod-weekly failed ~2 dni silent; state 1.2G+729M; 68+61 units; 330 .bak. Severity opiera się na prawdzie PRZYCISKOWEJ (±3 min, 0/377 GT) — proxy.

## Rekomendacja kolejności naprawy sierot (osobne mini-sprinty ETAP 0→7 + ACK)

1. **Tanie+żywe:** `osrm-fallback-double-traffic` (u źródła fallbacku, bliźniak traffic_v2) + diagnoza `cod-weekly` (+ dodać OnFailure — bo go brak).
2. **PRZED re-enable Telegrama:** klaster postpone ×5 + `tk-shadow-entry-msgid-null` + `tk-watchdog-keyerror-twin` (wszystkie arms-on-re-enable; gate C2 tylko ostrzega).
3. **P2 do fal:** `pipe-postshift-gate-exclusion`→L-gate/L7; `crg-ranking-bundle-skew`→L8/tier; `pis-closed-vs-orderids`→L3/panel-parser.
4. **P3 higiena:** crg-lastpos/gpsquality/pis-closedids/dedup + osrm-aggregate → L1/L8.
5. **Oś 05.07:** decyzja o ACK 2.0 Pion 2/3 (perf-SLO, alerty danowe, DR, security, systemd-lifecycle).

**Reguła trwała (do egzekwowania):** każdy przyszły audyt DOPISUJE do `ZIOMEK_FINDINGS_LEDGER.md`, nie tworzy nowego rejestru. Metryka bramkowa: `findingi-bez-właściciela = 0` (dziś ~27).
