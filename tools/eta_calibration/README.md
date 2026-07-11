# eta_calibration — kalibrator ETA per-leg, per-kurier (SHADOW)

Narzędzie codziennej kalibracji ETA dla Ziomka. Kalibruje osobno nogę ODBIORU i
DOSTAWY, emituje kwantyle P50/P80/P90 i działa wyłącznie w cieniu. Nie jest
wpięte w żywe ETA silnika.

**Stan prawdy po A360-A0:** `HOLD/UNBOUND`. Historyczne wyniki `-52%/-20%` zostają
wycofane: model używał godziny i obciążenia odtworzonych z faktycznego odbioru,
a champion/challenger porównywał agregaty z różnych holdoutów. Nie wolno ich
cytować jako dowodu jakości ani podstawy do wpięcia ETA.

## Szybki start
```bash
cd /root/.openclaw/workspace/scripts
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.eta_calibration.calibrate --rebuild-features
/root/.openclaw/venvs/dispatch/bin/python -m pytest dispatch_v2/tools/eta_calibration/tests/ -q
```

## Pliki
| Plik | Rola |
|---|---|
| `calibrate.py` | job dzienny: build → walidacja → exact-support champion/challenger → mapy + shadow + metryki |
| `features.py` | feature-store (`eta_calib.db`): join sla_log+Rutcom+ziomek_pred; pola po fakcie są jawnie outcome-only |
| `models.py` | L1/L2 na jawnej allowliście decision-time; historia kuriera wyłącznie z train; pełny artifact round-trip |
| `promotion.py` | zamrożony support, odtwarzalny artifact, paired CI/Wilcoxon i fail-closed HOLD |
| `evaluate.py` | walk-forward, baseline'y (silnik/koordynator/naiwny), MAE/RMSE/MAPE/pokrycie/pinball, bootstrap CI + Wilcoxon |
| `config.yaml` | okno, kwantyle, budżet, progi akceptacji |

## Kontrakt promocji

- `hour`, `slot`, `weekday`, zrekonstruowane `load/is_bundle` i niewersjonowane
  `prep_var_med` nie są cechami serwowanego modelu.
- Poprzedni champion musi być artefaktem schematu v2, odtwarzalnym bajtowo na
  zamrożonym supporcie. Brak/legacy/uszkodzenie = `HOLD`, nigdy bootstrap GO.
- Challenger i champion dostają dokładnie te same rekordy. Różny support,
  target drift lub różnica reprodukcji = `HOLD`.
- Promocja wymaga jednocześnie configowego progu realnej poprawy, paired CI
  poniżej zera, Wilcoxona `p < alpha`, non-inferiority i minimalnego supportu.
- Każdy challenger jest zapisywany do `eta_calib_*_candidate.json`; mapa championa
  zmienia się tylko po pełnym gate. Stare dokumenty `docs/eta/03..05` opisują
  historyczną, skażoną walidację i nie są aktualnym werdyktem.
- Ponieważ obecne mapy v1 nie są odtwarzalne, pierwszy seed championa v2 wymaga
  osobnego przeglądu kandydata i jawnej decyzji właściciela; job nie zrobi go
  automatycznie.

## Bezpieczeństwo
READ-ONLY na wejściach Ziomka. Testy i oracle muszą kierować DB, mapy oraz logi
do `tmp_path`; HERMETIC-GUARD pozostaje aktywny. Raporty/replay są aggregate-only.
Rollback kodu A0 = `git revert <commit>`. Wpięcie w żywe ETA nadal wymaga osobnego
sprintu, dowodu na uczciwym korpusie i ACK właściciela.
