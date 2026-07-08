# eta_calibration — kalibrator ETA per-leg, per-kurier (SHADOW)

Narzędzie codziennej kalibracji ETA dla Ziomka. Kalibruje **osobno nogę ODBIORU i DOSTAWY**,
personalizuje **per-kurier** (LightGBM-kwantyl z kurierem × kontekst + hierarchiczny shrinkage),
emituje **kwantyle P50/P80/P90** (P80 operacyjny; przedziały split-conformal). Tryb **CIEŃ**: pisze
wyłącznie obiekty `eta_calib_*`, nie dotyka żywego ETA silnika.

## Szybki start
```bash
cd /root/.openclaw/workspace/scripts
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.eta_calibration.calibrate --rebuild-features
/root/.openclaw/venvs/dispatch/bin/python -m pytest dispatch_v2/tools/eta_calibration/tests/ -q
```

## Pliki
| Plik | Rola |
|---|---|
| `calibrate.py` | job dzienny: build → walidacja → champion/challenger → mapy + shadow + metryki |
| `features.py` | feature-store (`eta_calib.db`): join sla_log+Rutcom+ziomek_pred, OSRM per noga, obciążenie z interwałów, tempo=czas/OSRM |
| `models.py` | L1 empiryczny+EB-shrinkage / L2 LightGBM-kwantyl (pinball), historia kuriera leakage-safe |
| `evaluate.py` | walk-forward, baseline'y (silnik/koordynator/naiwny), MAE/RMSE/MAPE/pokrycie/pinball, bootstrap CI + Wilcoxon |
| `config.yaml` | okno, kwantyle, budżet, progi akceptacji |

## Wyniki walidacji (holdout 14 dni)
- ODBIÓR: MAE **5.53** (vs silnik 11.65 / koordynator 6.75 / naiwny 6.11), −18…−52%, wszystkie istotne.
- DOSTAWA: MAE **7.53** (vs silnik 9.36 / naiwny 9.01), −20%.
- Przewaga rośnie z obciążeniem (worek 3-4: +29-32% vs koordynator). Cecha kuriera istotna na obu nogach.
- Pokrycie conformal: P80 78%/75%, P90 89%/86% (OSRM dostawy 82.5%).
- Pełny raport: `docs/eta/05_walidacja.md`. Projekt: `docs/eta/03_projekt_kalibracji.md`.

## Bezpieczeństwo
READ-ONLY na danych Ziomka (geokod cache-only, 0 zapisów). Zapis tylko `eta_calib_*`.
Pseudonimizacja kuriera w raportach/shadow. Rollback = zignorować/usunąć mapy `eta_calib_*`.
Wpięcie w żywe ETA = osobna decyzja właściciela (ACK), po domknięciu P90 + pokrycia OSRM.
