# S28-D — Perf peak p50–p99 (READ-ONLY, diagnoza przed skalą/multi-city)

**Data:** 2026-07-07 · **Wykonawca:** tmux 28 · **Tryb:** READ-ONLY (ZERO zmian kodu/flag/serwisów). **Nie do merge — raport + rekomendacja.**
**Źródło:** `logs/shadow_decisions.jsonl` pole `latency_ms` (pełny cykl decyzji, `shadow_dispatcher:1338`), n=**712** decyzji 2026-07-05…07 (+ rotacja `.1`). Serwer: Hetzner **CPX32, nproc=4**, load ~1.4 (off-peak). Warsaw=UTC+2 (CEST).

## 1. Ogon latencji — peak p50–p99 (Warsaw)
| Okno | n | p50 | p75 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| LUNCH 11–14 | 160 | 729 | 1086 | 1364 | **1617** | 1862 | 1869 |
| EVENING 17–20 | 220 | 864 | 1302 | 1842 | **2010** | 2295 | 2718 |
| off-peak | 332 | 838 | 1161 | 1636 | 1952 | 2739 | 4639 |
| **CAŁOŚĆ** | 712 | **825** | — | 1651 | **1947** | 2449 | 4639 |

**⚠ Kluczowy fakt:** p50 jest WYSOKI (~730–860 ms) w KAŻDYM oknie, nie tylko w peaku. Peak dokłada tylko OGON (evening p95 2010 vs lunch 1617). To znaczy: dominującym problemem NIE jest peak, tylko **strukturalny koszt bazowy każdej decyzji** („compute-zawsze", L0.1 z listy).

**Regres vs snapshot CLAUDE.md (kwiecień, pre-Hetzner):** p50 ~375→**825 ms** (2,2×), p95 ~624→**1947 ms** (3,1×) — MIMO upgrade 2→4 vCPU. Pipeline spuchł od kwietnia (conditional-ETA calib_maps, sla_anchor, geometria K2, loadgov, world_record v1, …) — każdy dokłada compute per decyzja.

## 2. Atrybucja składników (co ile waży)
**A. STRUKTURALNY baseline (~800 ms p50, dominant) — skaluje z liczbą kandydatów:**
| pool_total (kandydaci do oceny) | n | p50 | p95 |
|---|---|---|---|
| 0–5 | 97 | 584 | 1333 |
| 6–10 | 373 | 838 | 1916 |
| 11–15 | 242 | 906 | 2010 |

Każdy kandydat = pełna symulacja trasy (OSRM `table` + OR-Tools/greedy). OR-Tools cap **200 ms/wywołanie** (`V326_OR_TOOLS_TIME_LIMIT_MS`), pali się dla bag≥2 (`V327_MIN_OR_TOOLS_BAG_AFTER=2`), 10 workerów ThreadPool na 4 vCPU (oversubskrypcja ~2,5×). → **to jest sufit p50 i mnożnik p95**.

**B. Ogon outlierów >2 s = 24/712 (3,4%)** — prawie WSZYSTKIE `pool≥10` → strukturalne (koszt OR-Tools × kandydaci), **NIE cykliczny login-CSRF** (odstępy 5–125 min, nie równe ~22 min). Tylko 1 outlier `pool=0` (2739 ms KOORD) = niezależny od puli → login-refresh / OSRM cold / GC.

**C. Nie-czynniki (wykluczone pomiarem):**
- **world_record v1** (deploy 07-07 ~01:00): per-dzień p50 STABILNY 855/715/851 → narzut nagrywania **pomijalny**.
- **Backlog event_bus:** `NEW_ORDER:0` w 100% heartbeatów (pending:14–16 = inne typy) → **shadow NADĄŻA** przy obecnym wolumenie; latencja NIE tworzy zaległości NEW_ORDER (dziś).
- **OSRM raw** historycznie p50 11 ms/p95 26 ms — nie bottleneck; ale `table` rośnie O(n²) ze stopami (istotne dla większych worków/multi-city).

## 3. Klasyfikacja: silnik vs infra vs config
| Dźwignia | Klasa | Kto | Opis |
|---|---|---|---|
| **compute-zawsze** (pełna sym. każdego kandydata) | 🔧 SILNIK | **tmux 27** | Największa dźwignia p50. Pruning/lazy-compute: nie symuluj do końca oczywiście gorszych kandydatów (filtr feasibility PRZED pełną trasą). |
| Liczba wywołań OR-Tools (bag≥2 × pool) | 🔧 SILNIK | tmux 27 | Węższy filtr feasible → mniej wywołań 200 ms. |
| 4 vCPU / oversubskrypcja 10 workerów | 🖥️ INFRA | Adrian | Efektywność parallel ograniczona (Lekcja #27). Multi-city → vCPU ∝ liczbie równoległych decyzji. |
| Rzadki spike pool-niezależny (login CSRF) | ⚙️ CONFIG/INFRA | Adrian | Wątek tła odświeżania loginu eliminuje pojedyncze 2,7–4,6 s. |

## 4. Rekomendacja PRZED skalą / multi-city
1. **Priorytet #1 (SILNIK, tmux 27): zaadresować „compute-zawsze".** To JEDYNA dźwignia ruszająca strukturalny p50 ~800 ms. Multi-city = więcej kurierów → większe pule → koszt rośnie NADLINIOWO (pool 5→15 to już +55% p50). Bez prunowania kandydatów latencja peak przekroczy próg, przy którym shadow przestanie nadążać (dziś margines jest, ale wąski).
2. **Nie polegać na samym vCPU** — p50 URÓSŁ mimo 2→4 vCPU; software (compute per decyzja) to teraz bottleneck, nie hardware (odwrócenie diagnozy kwietniowej).
3. **Multi-city gating:** przed włączeniem 2. miasta zmierzyć p95 przy realnym większym pool i sprawdzić `NEW_ORDER` backlog pod obciążeniem — dziś=0, ale to funkcja latencja×wolumen.
4. **Tanie „quick-win" (CONFIG/INFRA):** wątek tła login-refresh — zdejmuje ~1 outlier/tydzień, nie rusza p50.

**ZERO zmian wykonanych.** Wszystkie dźwignie #1/#2 = SILNIK → poza zakresem tmux 28 (do fali tmux 27 / backlog perf P4).
