# C05 — objm_lexr6_canary RUNTIME-ORACLE (lane C, READ-ONLY)

**Sesja tmux 2 · 2026-06-30 ~16:50 UTC · agent C05-objm-lexr6-canary.** Zero edycji/restartów/flipów.
**Metoda:** druga, niezależna rekomputacja prawdy z surowych źródeł (`scripts/logs/shadow_decisions.jsonl` + `dispatch.log` + `watcher.log`) **bez importu narzędzia** (`scratchpad/oracle_objm_c05.py`), + odpalenie monitora READ-ONLY (bez `--notify/--save` → zero zapisu do dispatch_state), 2× determinizm. Numery linii z świeżego grepu (HEAD `8024705`).

---

## 0. CO MIERZY PRZYRZĄD I SKĄD CZYTA (ground facts)
- `objm_lexr6_canary_monitor.py` (timer `dispatch-objm-lexr6-canary-monitor` **10min LIVE, active+enabled**, ExecStart `--window-min 180 --notify`, edge-triggered) — liczy G1 (błędy/latencja), G2a-KOORD (excl early_bird, TOD-aware), G2b-auto-route (ACK+ALERT vs baseline), G2c-reorder (PER-DECYZJA ±5s).
- **Pole `objm_lexr6` NIE istnieje w ledgerze** (A4 potwierdzone). Canary liczy: KOORD/auto_route/latency z `shadow_decisions.jsonl` (pole `verdict`/`auto_route`/`latency_ms`); reorder z `dispatch.log`/`watcher.log` regex `OBJM_LEXR6_SELECT order=(\d+) reorder` (emit `dispatch_pipeline.py:6003`, LIVE-select branch pod `ENABLE_OBJM_LEXR6_SELECT` `:5995`).
- `objm_lexr6_peak_verdict.py` (at-**200** **Fri 03.07 18:10 PENDING**, exec `objm_lexr6_peak_verdict.py` **bez `--dry-run` → ZAPISUJE durable txt + Telegram**) — reużywa `M.shadow_metrics/log_signals/gates`, ALE headline `_g2c_note` liczy **własny** g2c.
- Stan flag (A3): `ENABLE_OBJM_LEXR6_SELECT=true` (live canary), `_SELECT_SHADOW=false`, `ENABLE_POST_SHIFT_OVERRUN_PENALTY=false`. notify_state = `GO` (last_sent 16:32).

---

## 1. ORACLE — G2c per-decyzja vs all-tick (RDZEŃ zadania)

### 1a. Determinizm + niezależna rekomputacja (okno FIXED 12:00–16:00 UTC dziś, past)
2× identyczny wynik. `oracle_objm_c05.py` (własny parser, NIE importuje narzędzia):

| Metryka | Oracle (niezależny) |
|---|---|
| n decyzji = n_orders | 84 = 84 (shadow loguje **1 linię/order**, NIE per-tick) |
| reorder: linii / distinct oids | 110 / 37 |
| **G2c per-decyzja (±5s)** | **5/84 = 5.95%** |
| **G2c all-tick (reorder∩shadow / orders)** | **35/84 = 41.67%** |
| ratio all-tick/per-dec | **7.0×** (12-16 okno); **11.33×** (12:47-16:47 okno) |

### 1b. Parytet z NARZĘDZIEM (okno = dokładnie monitora 12:47:39–16:47:39, run read-only)
Monitor (live binarka) vs mój oracle na IDENTYCZNYM oknie — **zgodność CO DO LINII**:

| | n | n_sel | per-dec | all-tick | KOORD raw | eb | ACK+ALERT | AUTO |
|---|---|---|---|---|---|---|---|---|
| MONITOR | 86 | 84 | 3/86=3.5% | 34/86=39.5% | 2.33% | 2 | 84.88% | 15.12% |
| ORACLE | 86 | 84 | 3/86=3.5% | 34/86=39.5% | 2.33% | 2 | 84.88% | 15.12% |

→ **Formuła monitora WIERNA.** (Oracle widzi 36 distinct reorder-oids vs „34" w nagłówku monitora — bo „34 ord" = **intersekcja** reorder∩shadow `_ro` monitor.py:504, NIE raw distinct; intersekcja = 34 w OBU. Brak rozbieżności.)

### 1c. DLACZEGO all-tick kłamie (dowód mechanizmu — delty czasowe)
5 dopasowań per-decyzja = **sub-sekundowe** (0.09 / 0.14 / 0.33 / 0.52 / 0.9 s) → realny reorder W MOMENCIE proposala (reorder logowany w tym samym `assess_order` co proposal; latency p95 ~1.7s < 5s → ±5s właściwie dobrane, mógłby być ±2s).
all-tick intersekcja=35, z tego **30 orderów ma najbliższy reorder DALEKO od proposala**: 6.4s, 18s, 27s, 33s, 124s, 207s, 254s, … **664s (11 min)**. To re-ewaluacje **plan_recheck/sweepera** (timer 3-5min) na JUŻ przypisanych orderach (np. order=484551 reorder co ~3min: 16:17/16:20/16:23/16:26…), NIE flipy proposala. all-tick miesza populacje: 5 flipów-proposala + 30 re-sweepów assigned. **per-decyzja = uczciwa stopa flipu selektora na proposalach.**

### 1d. „Naprawione 397a665" — WERDYKT: **CZĘŚCIOWO (połowa)**
- **Monitor** (`objm_lexr6_canary_monitor.py`, mtime 29.06 07:02) — fix per-decyzja **VALIDATED** (1b/1c). Gate G2c bramkuje per-decyzja, all-tick zdegradowany do diagnostyki. ✅
- **TWIN `objm_lexr6_peak_verdict.py`** (mtime **26.06** 12:02 — NIE tknięty fixem) — headline `_g2c_note` (`:37-46`) liczy `g2c` z **all-tick** (`:71-72` `ro=len(reorder_oids&shadow_oids); g2c=100*ro/n_orders`). Fix NIE objął bliźniaka. **B-class twin asymmetry: jeden bliźniak naprawiony, drugi kłamie.**

**Dowód z durable txt 29.06** (post-fix): gate-detail mówi `per-decyzja 3.7%`, a headline tego samego pliku: `≈ POŚREDNIO: peak 25.2%` (all-tick). Plik SAM SOBIE PRZECZY. 26.06: headline `⚠ NADAL WYSOKO 54.7%`; 28.06: `62.1%` — wszystko all-tick (×~7-11 zawyżka).

**at-200 (03.07) odpali ten sam buggy peak_verdict** → headline znów all-tick (~40% typowy peak) → fałszywy narratyw „NADAL WYSOKO… selektor over-reorderuje" sprzeczny z per-decyzja ~3-5%. To instrument DECYZJI Fazy 4 (ON-na-stałe vs rollback **żywej flagi selekcji** `ENABLE_OBJM_LEXR6_SELECT`) → fałszywy headline może pchnąć ku nieuzasadnionemu rollbackowi działającego selektora.

---

## 2. G2b-auto-route — WERDYKT: obliczenie VALIDATED, sygnał VOID (zła oś)
- ACK+ALERT% obliczane **poprawnie** (oracle-match 84.88%, 1b).
- ALE baseline `ack_alert_pct=89.13%` zapisany **2026-06-25** (POJEDYNCZY blok 12-18 UTC, n=138, mtime 26.06 10:34) = **5 dni nieświeży**. Próg ±8pp porównuje DZISIEJSZE ACK+ALERT do 5-dniowego single-day OFF-baseline.
- Między 25.06 a dziś: AUTON-02, force-recheck, equal-treatment, carried-first-relax, + ~16 commitów audyt-fix → ACK+ALERT dryfuje **niezależnie od selektora objm**. auto_route (AUTO/ACK/ALERT) ustala `auto_proximity_classifier` (pool/margin/tier), NIE selektor objm.
- STOP 26.06 (98.9%) i 29.06 (100.0%) = **fałszywa atrybucja** dryfu systemowego do flipu objm. (Zgodne z notą MEMORY „G2b-auto STOP osobny, NIE bug objm" — częściowo znane.)
- **Klasa G** (kalibracja-zła-oś) + E (misattribution). Gate mechanicznie liczy dobrze; jako miernik JAKOŚCI SELEKTORA = void.

---

## 3. G2a-KOORD — WERDYKT: VALIDATED (niska moc rozdzielcza)
- sel% excl early_bird + krzywa TOD — obliczenie poprawne (oracle: koord_sel=0.0%, eb=2, raw 2.33%).
- Wykluczenie early_bird = SŁUSZNE (wynik niezależny od wyboru kuriera).
- ⚠ baseline `koord_by_hour` dla godzin 7-19 = **same 0.0%** → exp_tod≈0% w peaku; dziś też ~0% → Δ0 → GO. Gate poprawny, ale **niskoinformacyjny** w peaku (KOORD rzadki w obu). Nie bug.

---

## 4. `_objm_lexr6_shadow` (void mina) — WERDYKT: UNTESTED (inertny) + latentna mina M
- `dispatch_pipeline.py:1097` `_objm_lexr6_shadow` gated `ENABLE_OBJM_LEXR6_SELECT_SHADOW` (`:6249`) = **effective OFF (A3)** → **NIE wykonuje się** (produkuje 0 sygnału live). Jako instrument: **VOID/UNTESTED** dziś.
- **Bucket JUŻ zunifikowany** (B2 28.06, `:1115-1116` `_selection_bucket`) → framing zlecenia „pre-equal-treatment bucket" jest **NIEAKTUALNY**. Frozen jest TYLKO inline `_lex_qual` (`:~1122-1126`, 3-krotka `(r6, late_pickup_committed_max, new_pickup_late_min)`, NIE post-shift-aware).
- Mina podwójnie zabezpieczona OFF: (a) SHADOW flag OFF (cień nie biega), (b) nawet ON — `_lex_qual` rozjeżdża się z kanonem `objm_lexr6.lex_qual` (`:29`, 4-krotka) TYLKO pod `ENABLE_POST_SHIFT_OVERRUN_PENALTY` (też OFF). Dziś bajt-identyczne.
- Gate `hygiena-shadow` (`:346-347`) łapie SELECT∧SHADOW oba ON (double-compute WARN), ale **NIE** cichą dywergencję `_lex_qual` pod POST_SHIFT.
- **Dedup: to TEN SAM frozen `_lex_qual` co A6 grupa-1 / root R1.** NIE liczyć podwójnie.

---

## 5. INSTANCJE (plik:linia świeże, klasa A-O)

| # | plik:linia | klasa | źródło/objaw | latane? | otwarte? | sev | dedup_hint |
|---|---|---|---|---|---|---|---|
| F1 | `tools/objm_lexr6_peak_verdict.py:71-72,81` (+`:37-46` `_g2c_note`) | **B** (twin) + E (lying) | source | **NIE** (twin nietknięty fixem 397a665) | **TAK** | **P1** | objm-canary↔peakverdict-twin (≠ R1 frozen lex) |
| F2 | `tools/objm_lexr6_canary_monitor.py:280-285,334-341` | E→resolved | source | TAK (397a665, VALIDATED) | NIE | P2 | objm-G2c-per-decyzja (fix OK) |
| F3 | `tools/objm_lexr6_canary_monitor.py:327-330` + baseline 25.06 | **G** + E | source | NIE | TAK | P2 | G2b-stale-baseline-zła-oś (część znana MEMORY) |
| F4 | `tools/objm_lexr6_canary_monitor.py:339` literał „×~3,5" | L/F | source | NIE | TAK | P3 | diagnostyczny literał stale (real 7-11×) |
| F5 | `dispatch_pipeline.py:1097` (+frozen `_lex_qual :~1122`, gate `:6249`) | **M**/K | source | NIE (zamrożony pod at#152) | TAK | P3 | **R1 frozen _lex_qual (A6 gr.1)** — NIE double-count |
| F6 | `tools/objm_lexr6_canary_monitor.py:40-41` (REORDER_LO/HI) | N/G | source | NIE | TAK | P3 | G2c band „~12%/[5,25]" nie re-derywowany pod per-decyzja |

**F6 detal:** po zmianie metryki all-tick→per-decyzja pasmo `[REORDER_LO=5, REORDER_HI=25]%` / „oczek.~12%" NIE re-derywowano dla rozkładu per-decyzja. Dziś per-decyzja 3.5-6% **straddluje dolną krawędź** → migotanie GO/WARN co tick (16:22 WARN 4.5%, 16:32 GO 5.0%, 16:42 GO 5.4%). „~12%" pochodzi z walidacji §6 (możliwy inny mianownik). Możliwy fałszywy-WARN „under-reorder".

---

## 6. INWARIANTY-TRIPWIRE (oracle)
- ✅ per-decyzja ≤ all-tick (5.95 ≤ 41.67; 3.5 ≤ 39.5) — strukturalnie i empirycznie.
- ✅ ten sam zbiór decyzji: oracle n=86 == monitor n=86 (identyczne okno).
- ✅ n == n_orders (shadow loguje 1 linię/order — NIE per-tick; to czyni all-tick podwójnie mylącym: reorder z dispatch.log JEST per-tick).
- ✅ ZERO pick-failed (G1-błędy=0) w oknie — oracle i monitor zgodne.
- ✅ early_bird wykluczenie spójne num∧denom (eb=2 oba).
- ⚠ CAVEAT: KOORD/auto_route/delivered = **prawda-PRZYCISKOWA (proxy-certified)**, NIE fizyczna. reorder = **log-truth** (deterministyczny, ground dla „selektor flipnął"). per-decyzja/all-tick = **ground-truth na osi „czy selektor zmienił pick"** (nie zależy od button-truth).

---

## 7. TABELA POKRYCIA

**Zbadane (coverage_declared):**
- `objm_lexr6_canary_monitor.py` — pełny: shadow_metrics, log_signals, gates (G1/G2a/G2b/G2c), _notify_decision, compute_tod_curve, MIN_N guard. Odpalony read-only 2× (determinizm), parytet z oracle.
- `objm_lexr6_peak_verdict.py` — pełny: build_report, `_g2c_note`, źródło g2c (all-tick). at-200 body zweryfikowany (`at -c 200`).
- `objm_lexr6.py` (kanon `lex_qual/bucket/pick`) — pełny.
- `dispatch_pipeline.py` `_objm_lexr6_shadow` (`:1097`) + `_objm_lexr6_d2_pick` (`:1355`) + wiring (`:5995/6003/6249`) — przeczytane.
- shadow_decisions.jsonl (struktura + 3 okna), dispatch.log/watcher.log reorder (110 linii/37 oids), baseline.json, notify_state.json, 3× durable peak_verdict txt (26/28/29.06).
- atq (168/193/198/200), timery (canary active+enabled; 3 smoke spent 26.06).

**NIE zbadane (coverage_gaps + powód):**
- **G1-latencja gate** — obliczenie p50/p95 zweryfikowane (oracle-match 1660ms), ale baseline lat 1892ms (25.06) ma TĘ SAMĄ stałość co G2b; NIE policzyłem osobno czy STOP-y latencji 26.06 (2420ms) to objm czy dryf infra. Prawdopodobnie infra (peak load), nie selektor — analogicznie do G2b. Sygnalizowane, nie udowodnione drugą metodą.
- **Walidacja §6 „~12%"** — nie odtworzyłem oryginalnej liczby flip-rate z walidacji (źródło `eod_drafts` replay) → nie wiem czy „12%" było per-decyzja czy all-tick. To bramkuje czy F6 (band) to realny fałszywy-WARN czy poprawna kalibracja. Faza E/at-200.
- **at#152 peak verdict** — frozen `_lex_qual` czeka na PASS at#152 (03.07 at-200); nie weryfikowałem statusu at#152 (to inny przyrząd; A6 grupa-1 R1).
- **Telegram tor** — nie wywołałem `send_admin_alert` (read-only, bez --notify). edge-triggered `_notify_decision` przeczytany, nie odpalony z mutacją.
- **Cross-proces fingerprint** objm flag (A3 §7: ENABLE_OBJM_LEXR6_SELECT w fingerprincie, _SHADOW poza) — domena A3/Faza D, nie powtarzam.

---

## 8. WERDYKTY ORACLE (per komponent)

| Instrument | Werdykt | Prawda-2-metoda | proxy/ground | Co flipuje |
|---|---|---|---|---|
| `canary_monitor` G2c per-decyzja | **VALIDATED** | własny parser ±5s + delty sub-sekundowe vs sweep 6-664s | ground (oś „selektor flipnął") | WARN/GO G2c → narratyw over/under-reorder |
| `canary_monitor` G2a-KOORD | **VALIDATED** (niskoinform.) | recompute koord_sel excl eb | proxy (button verdict) | STOP/GO G2a |
| `canary_monitor` G2b-auto-route | **VOID jako sygnał objm** (obliczenie OK) | recompute ACK+ALERT + analiza stale baseline 25.06 | proxy | STOP/GO G2b → fałszywa atrybucja |
| `peak_verdict._g2c_note` (at-200) | **VOID** (headline all-tick, ×7-11 zawyżka) | oracle all-tick vs per-decyzja | log-truth | headline Fazy-4 ON/rollback `ENABLE_OBJM_LEXR6_SELECT` |
| `_objm_lexr6_shadow` | **UNTESTED** (inertny, SHADOW OFF) | grep gate :6249 + flag A3 | — | nic dziś; latentna mina M pod POST_SHIFT∧SHADOW |

---

## 9. RECONCILE REJESTRU (dla shadow-jobs-registry, NIE zapisuję)
- at-**200** (03.07 18:10) PENDING — odpali `objm_lexr6_peak_verdict.py` (buggy headline). **Rekomendacja-DRAFT (NIE wykonana):** przed 03.07 bliźniak `_g2c_note` powinien czytać per-decyzja (jak monitor) — inaczej werdykt peak znów skłamie. Protokół+ACK.
- Live canary (10min, --notify) = FIXED monitor, werdykt dziś GO/WARN (per-decyzja ~3.5-5.4%, band straddle). notify_state=GO.
- 3 smoke timery objm (flip/verdict/morning) SPENT 26.06 (NEXT=-), nieaktywne.
