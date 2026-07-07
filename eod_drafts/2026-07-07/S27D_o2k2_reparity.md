# S27-D · O2-K2 — re-pomiar parytetu picku (READ-ONLY)

**Pas D / READ-ONLY · bieg 2026-07-07 ~19:31 UTC · narzędzie `tools/o2_k2_pick_parity.py`**
Uruchomienie: `cd /root/.openclaw/workspace/scripts && /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.o2_k2_pick_parity --dry` → **exit 0**.
⚠ Świadomie **`--dry`**: narzędzie domyślnie NADPISUJE `dispatch_state/o2_k2_pick_parity_verdict.txt`. W trybie READ-ONLY nie piszę do stanu i **nie kasuję werdyktu A1 z 17:59** — liczby biorę ze stdout.

## Wynik (okno domyślne od 2026-07-03T13:19Z, próg n=10) — identyczny z A1
| metryka | A1 (17:59 UTC) | S27-D (19:31 UTC) | Δ |
|---|---|---|---|
| **n_best_effort** (≥2 kandydatów) | 24 | **24** | 0 |
| pominięte bez pokrycia kotwic | 0 | 0 | 0 |
| **changed** (pick ON↔OFF) | 3 (12,5%) | **3 (12,5%)** | 0 |
| **kierunek-K2-ok** (ON-pick ≤ ready-breachy) | 3/3 | **3/3** | 0 |
| WERDYKT | MEASURED | **MEASURED** | — |

Te same 3 flipy, te same kierunki:
| order | @UTC | pick OFF→ON | ready-breach OFF→ON |
|---|---|---|---|
| 485866 | 06.07 11:35:44 | 470 → 484 | 1 → 1 (neutralny) |
| 485870 | 06.07 11:53:42 | 531 → 484 | **3 → 2 (ściśle lepszy)** |
| 485911 | 06.07 14:36:50 | 531 → 536 | 1 → 1 (neutralny) |

## Interpretacja — dlaczego identyczny
Okno domyślne startuje 2026-07-03T13:19Z i sięga „teraz". Między biegiem A1 (17:59 UTC) a tym biegiem (19:31 UTC) upłynęło ~1,5 h **wieczoru bez nowego peaku** → zero nowych decyzji `best_effort` z ≥2 kandydatami (wieczór = pełne pule, argmin trywialny). Zbiór jest więc **ten sam** co u A1.

To jest **stabilnościowa re-konfirmacja, NIE świeża korroboracja przez peak**:
- ✅ Kierunek K2 potwierdzony powtórnie: **3/3 K2-ok, 0 flipów w złą stronę**, 1 ściśle lepszy. Wynik jest deterministyczny na tych danych i się nie „rozjechał".
- ⚠ Ciężar próby wciąż **20/24 z poniedziałku 06.07**. Wtorek 07.07 = 1. Prawdziwa niezależna korroboracja „po kolejnym peaku" wymaga **środy 08.07** (peak 09–14 Warsaw) — dziś peak wtorkowy minął przed biegiem A1, a wieczór nic nie dorzucił.

## Werdykt
🟢 **MEASURED, kierunek stabilny (3/3), n=24 niezmienne.** Warunek „parytet picku n≥10" dla flipu O2-K2 pozostaje **SPEŁNIONY**, bez regresji ready-breach. To potwierdza, nie rozszerza, dowodu A1.

**Rekomendacja pomiarowa (nie flip):** powtórzyć po peaku **środy 08.07** dla świeżego, wtorkowo/środowo zasilonego n (obecnie ciężar na Pn) — wtedy korroboracja będzie z niezależnej doby, nie z tego samego okna. Do tego czasu kierunek uznaję za potwierdzony, ale próba wciąż poniedziałko-centryczna.

## Uwagi metodyczne
- Read-only: `--dry`, brak zapisu do `dispatch_state/`; werdykt A1 (mtime 17:59:22) nietknięty.
- Ten sam kanon co A1 (`ledger_io.iter_shadow_decisions`, realny `DP._best_effort_sort_key`, podmiana tylko termu SLA idx=2).
