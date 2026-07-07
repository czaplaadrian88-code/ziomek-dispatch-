# A1 — O2-K2 pick parity (warunek flipu `ENABLE_SLA_GATE_READY_ANCHOR`)

**Pas A / READ-ONLY · bieg 2026-07-07 17:59 UTC · narzędzie `tools/o2_k2_pick_parity.py`**
Uruchomienie (naprawiona ścieżka z planu): `cd /root/.openclaw/workspace/scripts && /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.o2_k2_pick_parity` → **exit 0**. (Z katalogu repo: `ModuleNotFoundError: dispatch_v2` — wymaga cwd=`scripts/` + venv dispatch. Narzędzie ma `sys.path.insert` + `__main__`, więc `-m` i skrypt-wprost oba działają z tego cwd.)

## Wynik (okno od 2026-07-03T13:19Z, próg n=10)
| metryka | wartość |
|---|---|
| **n_best_effort** (best_effort z ≥2 kandydatów) | **24** |
| próg **n≥10** | **✅ OSIĄGNIĘTY** (24) |
| pominięte bez pokrycia kotwic | 0 |
| **changed** (zmieniony pick ON↔OFF) | **3 (12,5%)** |
| **kierunek-K2-ok** (ON-pick ≤ ready-breachy) | **3/3** (0 w złą stronę) |
| **WERDYKT** | **MEASURED** |

Werdykt zapisany: `dispatch_state/o2_k2_pick_parity_verdict.txt` (mtime 17:59:22, nadpisał INCONCLUSIVE z 05.07).

## Rozkład n per doba UTC (korroboracja niezależna — ten sam kanoniczny `ledger_io.iter_shadow_decisions`)
| doba UTC | n (≥2 kand.) |
|---|---|
| 2026-07-03 | 3 |
| 2026-07-06 (Pn, peak) | 20 |
| 2026-07-07 (Wt) | 1 |
| **razem** | **24** |

⚠ Próg nabity **głównie poniedziałkiem (20/24)**. Wtorek dorzucił 1 (peak Wt 09-12 UTC minął przed biegiem o 17:59). Weekend 05.07 = 6 rekordów `reason=best_effort`, ale **0 z ≥2 kandydatów** → nie liczone (przy 1 kandydacie argmin trywialny, nie ma picku do zmiany). Poprzedni bieg 05.07 (n=3) = te 3 z 03.07.

## 3 flipy (wszystkie Pn 06.07 — kierunek K2 spójny)
| order | @UTC | pick OFF→ON | ready-breach OFF→ON |
|---|---|---|---|
| 485866 | 11:35:44 | 470 → 484 | 1 → 1 (neutralny — remis przełamany kotwicą ready) |
| 485870 | 11:53:42 | 531 → 484 | **3 → 2 (ściśle lepszy)** — ON wybiera mniej ready-breachy |
| 485911 | 14:36:50 | 531 → 536 | 1 → 1 (neutralny) |

**0 flipów w złą stronę** (żaden ON-pick nie ma WIĘCEJ ready-breachy niż OFF-pick). 1 ściśle lepszy, 2 neutralne (rozstrzygnięcie remisu inną kotwicą). Zmieniony pick = ZAMIERZONA zmiana K2 (kotwica now→ready), nie bug.

## Rekomendacja
**GOTOWE DO POMIARU** — warunek „parytet picku n≥10" dla flipu O2-K2 **SPEŁNIONY** (n=24, MEASURED, kierunek 3/3 K2-ok, 0 regresji ready-breach). Pomiar zamknięty, zasila Pas 0.

Pozostałe bramki flipu O2-K2 (plan §2.A.2) **niezmienione, poza tym pomiarem**: (1) O2-K1 `ENABLE_O2_CAPZ_RESEQ` ON najpierw — obecnie **WSTRZYMANY przez Adriana**; (2) L3 ≥2d obserwacji. Nie proponuję flipu — tylko mierzę.

Opcjonalnie (nie warunek — próg już nabity): jeśli Pas 0 chce korroboracji dwudniowej, powtórzyć po kolejnym peaku ze zdrowszym udziałem best_effort ≥2-kand. na dobę wtorkową (dziś Wt=1, ciężar na Pn).
