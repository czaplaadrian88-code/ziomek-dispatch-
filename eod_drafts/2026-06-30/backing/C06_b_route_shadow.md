# C06 — b_route_shadow (lane C, RUNTIME-ORACLE) — backing

**Agent:** C06-b-route-shadow · **Tryb:** READ-ONLY (zero edycji/restartów/flipów/notify) · **Data:** 2026-06-30 ~17:00 UTC · sesja tmux 2.
**Przyrząd:** `b_route_shadow` (kolektor `tools/b_route_shadow.py` → `dispatch_state/b_route_shadow.jsonl`) + werdykt `tools/b_route_shadow_review.py`.
**Pytanie zlecenia:** czy rekordy pod WIERNYMI flagami (post-fix `c8c5f86`) czy widmo? Zwalidować fix. Odczytać werdykt review (timer odpalił 30.06 07:00). validated/void/untested.

**Metoda (druga, niezależna):** (1) recompute z 736 świeżych rekordów jsonl; (2) `systemctl show -p Environment` full-diff plan-recheck↔b-route (parytet); (3) niezależny OSRM `/table` HTTP na localhost:5001 (własna pętla walk) vs `_walk_metrics`, ≥2 odpalenia; (4) dowód overlapu `order_ids` korpus↔sla_log (oba ścieżki); (5) odczyt logu werdyktu 07:00. Skrypty: `scratchpad/c06_oracle.py`, `scratchpad/c06_osrm_crosswalk.py`.

---

## WERDYKT: SPLIT — kolektor **VALIDATED**, werdykt-review **VOID**

| Część | Werdykt | Dlaczego |
|---|---|---|
| **KOLEKTOR** `b_route_shadow.jsonl` | **VALIDATED** | `c8c5f86` parytet flag EFEKTYWNY: 736/736 rekordów pod wiernym `route_env` (14/14 flag="1"); env-parytet kompletny; widmo zarchiwizowane; wszystkie inwarianty czyste; differs/delta spójne; drive=engine-OSRM (×traffic-mult), niezależnie potwierdzone, deterministyczne. |
| **WERDYKT** `b_route_shadow_review` (07:00) | **VOID** | Werdykt MIXED z **real_joined=0** — ramię outcome-join (decydujące wg docstringu) MARTWE: review czyta zamrożoną `dispatch_state/sla_log.jsonl` (max oid 481971, 09–19.06) zamiast żywej `scripts/logs/sla_log.jsonl` (świeża, oid 484xxx). Repoint → real_joined≈289 nie 0. |

---

## 1. KOLEKTOR — VALIDATED (fix `c8c5f86` potwierdzony drugą metodą)

### 1a. route_env provenance — WIERNY na każdym rekordzie
`tools/b_route_shadow.py:397` stempluje `route_env={k: os.environ.get(k,"0") for k in ROUTE_PARITY_FLAGS}` (14 flag, `:51-59`). Recompute (`c06_oracle.py`):
```
LIVE CORPUS: 736 records   ts: 2026-06-29T06:56:36 -> 2026-06-30T16:52:39   (CAŁY post-parity epoch)
route_env present:            736/736   (missing/pre-parity = 0)
route_env ALL 14 flags=="1":  736/736   (faithful)
route_env NOT all-1:          0         (zero widma-w-live)
```
Drop-in `route-flag-parity.conf` istnieje od **Jun 29 06:19**; pierwszy rekord live **06:56** = już po drop-inie. Widmo (pre-parytet) poprawnie odcięte: `b_route_shadow.jsonl.phantom-pre-parity-2026-06-29` = **2092 rekordy**, live plik startuje czysto 29.06.

### 1b. Env-parytet KOMPLETNY (B liczy geometrię serwowanego kanonu)
`diff <(systemctl show dispatch-plan-recheck -p Environment) <(systemctl show dispatch-b-route-shadow -p Environment)` — wszystkie 14 ENABLE_ route/canon flag identyczne="1" na obu serwisach (jedyna różnica: `ENABLE_B_ROUTE_SHADOW=1` = flaga aktywacji TEGO serwisu). Plan-recheck NIE ma dodatkowych route-flag poza 14 → lista parytetu kompletna. Zatem `_b_full_retsp` (`:254`, woła `P._gen_one_bag_plan`) + `_apply_canon_order_invariants` (`:318`, B-lite) gałęziują na TEJ SAMEJ geometrii co serwowany kanon w `courier_plans.json`.

### 1c. Inwarianty-tripwire — WSZYSTKIE czyste (736 rekordów)
```
INV-SET served covers order_ids:     736/736   (b: 719/719, blite: 736/736)
INV-NOFIC no carried-as-pickup:      736/736   (ZERO fikcyjnych odebranych pickupów)
INV-CARRIED-NOPICK served:           736/736
INV-PD-ORDER pickup<dropoff served:  736/736
INV-DIFFERS recompute match:         719/719   (zapisane differs_b == przeliczone z b/served)
INV-DELTA arithmetic match:          719/719   (delta_drive_b == m_served.drive - m_b.drive)
```
Kolejność z `plan_doc["stops"]` WPROST (`_served_order:213`), nie sort-ts → zgodne z wymogiem „kolejność z p.sequence". `served_synthetic` poprawnie znakuje 17/736 fallbacków carried-first (`:241 _served_is_synthetic`, mirror coverage-checku) — analiza MOŻE wykluczyć (C9/#17 Rećki guard).

### 1d. Drive metric — SECOND-METHOD OSRM (deterministyczne, wierne silnikowi)
`c06_osrm_crosswalk.py` (import modułu + niezależny direct OSRM `/table` HTTP, 6 żywych worków, ×2):
```
cid  n  module_drive  direct_raw_osrm  ratio
536  2     17.46         13.97          1.250
515  3     29.66         23.72          1.250
376  3     60.78         48.63          1.250
207  2     26.98         21.59          1.250
370  4     33.80         27.04          1.250
179  2     19.73         15.78          1.250
DETERMINISM RUN1==RUN2: True
```
Współczynnik **dokładnie 1.25 niezależnie od geometrii/rozmiaru** = `ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER` (potwierdzone osobno: `osrm_client.table` 244.0s vs raw 195.2s = 1.25, wtorek 18:56 Warsaw bucket). Moduł stosuje traffic-mult silnika; mój raw-OSRM go nie ma. **To NIE bug** — moduł jest WIERNY semantyce silnika; oba `m_served` i `m_b` przechodzą przez ten sam `osrm_client.table` → `delta_drive_b` liczona like-for-like (mnożnik się skraca w delcie). **proxy-certified** (drive = estymata silnikowa, NIE ground-truth).

**Wniosek:** rejestr A4 row6 „#4 NAPRAWIONE 29.06 (c8c5f86 parytet, widmo→.phantom); review czyta tylko wierne" — POTWIERDZONE drugą metodą dla KOLEKTORA.

---

## 2. WERDYKT-REVIEW — VOID (martwy outcome-join przez złą ścieżkę)

### 2a. Werdykt jaki padł (log 30.06 07:00 UTC)
```
Korpus: 432 / 432 multi / B 419   B≠served: 114 (27.2%)   B-lite≠served: 170
Estym. jazda Δ med 1.34 · świeżość Δ med 0.0 · odbiór-spóźn Δ med 0.0
B lepszy 39 / B gorszy 65
Realny outcome served (join sla_log, n=0): świeżość med None · on-time None%
WERDYKT: MIXED — "Sygnał niejednoznaczny — przedłużyć shadow lub zawęzić analizę."  [telegram] wysłano
```
**`real_joined=0`** → ramię realnych wyników (REAL on-time%, real food-age) = NULL. Werdykt zapadł na ESTYMATACH-only.

### 2b. ROOT CAUSE — zła ścieżka sla_log (CONFIRMED, dual-path)
- `b_route_shadow_review.py:18-20`: `STATE_DIR="…/dispatch_state"`, `SLA = f"{STATE_DIR}/sla_log.jsonl"`.
- `dispatch_state/sla_log.jsonl` = **ZAMROŻONA OSIEROCONA KOPIA** (mtime **Jun 20 09:23**, 1810 rec, daty 09–19.06, max oid **481971**).
- ŻYWY pisarz `sla_tracker.py:57 LOG_PATH = …/scripts/logs/sla_log.jsonl` (FRESH **16:53**, 13292 rec, 27.04→**30.06**). Tu czytają też eta_calibration_logger / courier_ranking / telegram_approver / reassignment_quality_replay (`scripts/logs`).
- Overlap dowodowy (`c06_oracle.py`):
```
corpus order_ids ∩ STALE dispatch_state/sla : 0  /394    -> real_joined=0
corpus order_ids ∩ LIVE  scripts/logs/sla   : 376/394    (95.4%)
differs_b order_ids ∩ LIVE scripts/logs/sla : 289/300    (96.3%)
```
**Repoint ścieżki → real_joined ≈ 289, NIE 0.** To ta sama pułapka „scripts/logs ≠ dispatch_state" co master-ledger `shadow_decisions.jsonl` (A4 handoff #1). `b_route_shadow.py:13` docstring nie pinuje ścieżki („join `sla_log.jsonl`/ground-truth"); review zahardkodował zły katalog.

### 2c. Dlaczego to VOID a nie tylko „nieprecyzyjne"
`_verdict()` (`:128-141`): gałąź GO-KANDYDAT (`:138`) WYMAGA `real_served_ontime_pct < 80` → osiągalna TYLKO przy `real_joined>0`. Przy stałym 0 werdykt może padać WYŁĄCZNIE na NO-GO (estym.) lub MIXED — **strukturalnie niezdolny do werdyktu ground-truth**. Decyzja którą bramkuje: Adrian 30.06 „re-TSP/B-lite warto czy zamknąć temat". Na estymatach sygnał realnie mieszany (b_better 39 < b_worse 65, jazda Δ +1.34) → nie dał fałszywego GO, ale „przedłużyć shadow" jest VOID: ślepe na ~289 dostępnych realnych wyników. **Naprawa = 1-linijkowy repoint na `scripts/logs/sla_log.jsonl` → re-run review.**

### 2d. Wtórnie: review ignoruje `served_synthetic`
`build_report` (`:75-93`) NIE filtruje `served_synthetic` (kolektor go emituje, `:405`). 12/192 (6.2%) bieżących differs_b liczone vs SYNTETYCZNY carried-first baseline (nie realny plan) → kontaminacja b_better/b_worse. Marker jest, konsument go nie honoruje.

---

## 3. INSTANCJE (plik:linia świeże)

| ID | Klasa | Plik:linia | src/obj | Opis | Sev | Patched | Open |
|---|---|---|---|---|---|---|---|
| C06-F1 | E (+H/J) | `tools/b_route_shadow_review.py:18-20` (join `:55-69`,`:96-106`) | source | review czyta zamrożoną `dispatch_state/sla_log.jsonl` (20.06) zamiast żywej `scripts/logs/sla_log.jsonl` → real_joined=0 → werdykt MIXED 07:00 VOID (ślepy na 289 realnych wyników) | P1 | nie | TAK |
| C06-F2 | F (display/guard) | `tools/b_route_shadow_review.py:75-93` | source | review nie honoruje `served_synthetic` markera kolektora → 6.2% differs_b vs syntetyczny baseline | P3 | nie | TAK |
| C06-F3 | M | `tools/b_route_shadow.py:336-337` | symptom | `_append_jsonl` łapie wyjątek zapisu → tylko `_log.warning` (cicha utrata danych przyrządu) | P3 | nie | TAK |

**Pozytywne walidacje (nie-defekty):** route_env 736/736 wierny; env-parytet kompletny; inwarianty 736/736; differs/delta 719/719; drive=engine-OSRM deterministyczny; phantom 2092 zarchiwizowane.

---

## 4. ORACLE_VERDICTS (C9/C11)

**(a) KOLEKTOR `b_route_shadow` → VALIDATED.**
- truth_second_method: recompute 736 rec (route_env all-14="1" 736/736); full env-diff plan-recheck↔b-route; niezależny OSRM /table HTTP cross-walk 6 worków ×2 (module=raw×1.25 traffic-mult, deterministyczne); differs recompute 719/719; delta arith 719/719.
- proxy_or_ground: **proxy-certified** (drive=estymata silnikowa z traffic-mult; delivered/picked = button-truth).
- invariant_checks: same-stop-set 736/719/736; ZERO fikcyjnych carried-pickupów 736/736; pickup<dropoff 736/736; carried-no-pickup 736/736; route_env all-1 736/736; widmo (2092) odcięte, live czysty od 29.06 06:56.
- gates_which_flip: differs_b/delta_drive_b → b_better/b_worse → werdykt GO/NO-GO B-lite.

**(b) WERDYKT `b_route_shadow_review` (07:00) → VOID.**
- truth_second_method: odczyt logu (MIXED, real_joined=0) + dowód overlapu (corpus∩stale=0; corpus∩live=376/394; differs_b∩live=289/300) → repoint→real_joined≈289.
- proxy_or_ground: **proxy-certified** (sla_log delivered/picked = button-truth; ground-truth `gps_delivery_truth.jsonl` NIE używany przez review).
- invariant_checks: real_joined=0 (powinno ~289); `_verdict()` nieosiągalny GO-KANDYDAT bez real_joined>0; served_synthetic nie honorowany (12/192).
- gates_which_flip: decyzja Adriana 30.06 „budować B-lite czy zamknąć temat" — VOID, bramka ślepa na realne wyniki.

---

## 5. POKRYCIE

**Zbadane:** `b_route_shadow.py` (pełny), `b_route_shadow_review.py` (pełny), drop-in `route-flag-parity.conf`, service+timer (kolektor+review), `b_route_shadow.jsonl` (736 live, pełny recompute), `b_route_shadow_state.json`, phantom (count), log werdyktu 07:00, OBA `sla_log.jsonl` (dispatch_state STALE + scripts/logs LIVE), env-parytet (full systemctl show diff), OSRM cross-walk (in-proc second-method ×2), potwierdzenie traffic-mult 1.25, pisarze sla_log (grep), freshness r6_breach/gps_delivery_truth.

**Luki (jawne):**
1. **Historyczny OSRM re-walk zapisanych rekordów NIEMOŻLIWY** — rekord nie trzyma coords/pos. Walk-logic zwalidowany na ŻYWYCH workach (ta sama ścieżka kodu `_walk_metrics`), nie na historycznych. Proxy.
2. **delivered_at/picked_up_at = button-truth** (A4 caveat ±~3min) — nawet po repoincie ścieżki, review „real on-time%" byłby button-truth, NIE GPS. Jedyny ground-truth = `gps_delivery_truth.jsonl`, którego review nie używa (potencjalny upgrade, osobny temat).
3. **panel-watcher.service env-parytet** (DRUGI pisarz kanonu) vs b-route NIE zdiffowany — tylko plan-recheck. A6 twierdzi że oba mają route drop-iny; parytet z panel-watcher spot-założony.
4. **Nie odpaliłem review-toola** (emituje Telegram bez `--no-telegram`; honoruję „zero notify") — werdykt wzięty z logu 07:00. Re-run z `--no-telegram` dałby świeższe liczby (differs_b 114→192), ale werdykt jakościowo ten sam (real_joined wciąż 0 przy obecnej ścieżce).
5. **B-lite (`_b_lite:276`) logika wewn.** zwalidowana na poziomie inwariantów (set-coverage 736/736), nie krok-po-kroku insert_stop_optimal — to realny GO-kandydat, gdyby review kiedyś dał GO.

**Nie-luki (świadomie):** Mailek/Papu (granica). Frozen `_lex_qual` shadow (grupa A6-1, inny agent). Sentinele (klasa M, inny agent).

---

## 6. DEDUP / HANDOFF

- **C06-F1 zwija się do rootu „scripts/logs vs dispatch_state source-of-truth"** (A4 handoff #1, master-ledger trap) — sla_log dual-path. NIE liczyć jako nowy chaos; to TA SAMA klasa co shadow_decisions.jsonl. Naprawa atomowa: repoint `b_route_shadow_review.py:20` na `/root/.openclaw/workspace/scripts/logs/sla_log.jsonl` (+ ewent. usunięcie osieroconej `dispatch_state/sla_log.jsonl` żeby nie myliła). Po repoincie review re-run → realny werdykt GO/NO-GO B-lite.
- **C06-F3** = instancja wspólnego M-rootu `_append_jsonl` silent-swallow (A4 §8: bundle_calib:524, reassignment_forward_shadow:414, carried_first_guard). Jeden fix wzorca.
- **Kolektor** nie wymaga nic — `c8c5f86` zwalidowany. Review po repoincie = jedyny ruch.
