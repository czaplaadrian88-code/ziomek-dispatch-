# B2/B3 — Wdrożenie i obsługa narzędzia `eta_calibration`

> **Tryb:** SHADOW (zero wpięcia w żywe ETA Ziomka) · **Kod:** `dispatch_v2/tools/eta_calibration/`
> **Interpreter:** WYŁĄCZNIE `/root/.openclaw/venvs/dispatch/bin/python` (ortools/lightgbm/scipy/pyyaml/matplotlib).

## 1. Co robi
Buduje z logów Ziomka (READ-ONLY) feature-store, uczy kalibrator ETA per-leg (odbiór/dostawa) per-kurier z shrinkage, waliduje walk-forward i zapisuje **wyłącznie** obiekty `eta_calib_*`. Nie modyfikuje `common.py`, feasibility, scoringu ani map Ziomka.

## 2. Struktura
```
tools/eta_calibration/
├── calibrate.py    # główny job dzienny (idempotentny, deterministyczny)
├── features.py     # feature-store (join sla_log+Rutcom+ziomek_pred, OSRM, obciążenie, tempo)
├── models.py       # L1 empiryczny+shrinkage / L2 LightGBM-kwantyl (kurier×kontekst)
├── evaluate.py     # walk-forward + baseline'y + metryki + istotność
├── config.yaml     # okno, kwantyle (P75 oper.), budżet, progi akceptacji
├── requirements.txt
├── README.md
└── tests/          # pytest: cechy, split czasowy, shrinkage, pinball, pseudonimizacja (16/16)
```

## 3. Uruchomienie ręczne
```bash
cd /root/.openclaw/workspace/scripts
# pełny cykl (przebudowa cech + walidacja + mapy + shadow + metryki):
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.eta_calibration.calibrate --rebuild-features
# tylko re-walidacja + mapy (store już zbudowany):
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.eta_calibration.calibrate
# determinizm (np. replay): --now 2026-07-07T04:00:00+00:00
```
Wynik: `dispatch_state/eta_calib_{pickup,delivery}_map.json` (+ `_lgbm_p75.txt`), `eta_calib_shadow.jsonl`, `eta_calib_metrics.jsonl`.

## 4. Cień nocny — ZAINSTALOWANY (2026-07-07, ACK Adriana „postaw cień")
Jednostki systemd (źródło: `tools/eta_calibration/systemd/`, wdrożone w `/etc/systemd/system/`):
- **`dispatch-eta-calibration-tool.service`** — oneshot, `calibrate --rebuild-features`, `Nice=15`/`CPUWeight=20` (nie przeszkadza silnikowi), log → `logs/eta_calibration.log`.
- **`dispatch-eta-calibration-tool.timer`** — `OnCalendar=*-*-* 05:20:00` UTC (po loggerach Ziomka 04:15/04:30/04:35/05:00), `Persistent=true`. **Status: enabled+active.**
- Test pod systemd 2026-07-07 23:11: `Result=success, exit 0`.
- Źródło `czas_kuriera` = Rutcom CSV (historia) **+ żywy `ziomek_pred_calibration.jsonl`** (co 3 min) → cień widzi NOWE zlecenia.

**Wyłączenie:** `systemctl disable --now dispatch-eta-calibration-tool.timer`. **Monitoring:** realny on-time w `eta_calib_metrics.jsonl` (pole `ONTIME_operacyjna`/`spoznien_pct` per noga) — jeśli odbiega od celu 20% spóźnień, przytnij `drift_buffer_ontime` w `config.yaml`.

## 5. Zmienne środowiskowe / konfiguracja
Wszystko w `config.yaml`; override przez env `ETA_CALIB_*` (opcjonalnie). Kluczowe:
- `model.operational_quantile: 0.8` (Adrian 2026-07-07 — P80). Zmiana kosztu → zmiana kwantyla (τ=koszt/(koszt+1)). `model.conformal: true` (przedziały split-conformal, pokrycie nominalne).
- `osrm.external_budget_per_day: 100` — twardy limit zapytań ZEWNĘTRZNYCH (kill-switch); dziś 0 (geokod cache-only).
- `window.holdout_days: 14`, `train_days: 44`.
- `acceptance.*` — progi GO (używane przez champion/challenger).

## 6. Champion/challenger + rollback
- Nowa mapa promowana tylko gdy nie gorsza niż poprzedni champion (`eta_calib_metrics.jsonl`, pole `promoted`). Inaczej stara mapa zostaje.
- **Rollback (natychmiastowy):** narzędzie jest shadow — usunięcie/zignorowanie map `eta_calib_*` = powrót do stanu bez narzędzia. Zero wpływu na Ziomka.
- **Wyłączenie:** skasuj wpis cron/timer; opcjonalnie `rm dispatch_state/eta_calib_*` (tylko własne obiekty).

## 7. Checkpointy / idempotencja
- `features.build()` = `INSERT OR REPLACE` po `order_id` → re-run bezpieczny.
- OSRM cache w `eta_calib.db` (tabela `eta_calib_osrm_cache`) → kolejne runy nie wołają OSRM ponownie.
- Determinizm: bootstrap/seed stałe; brak `random`/`Date.now` wpływających na wynik (czas przez `--now`).

## 8. Testy (przed każdą zmianą)
```bash
cd /root/.openclaw/workspace/scripts
/root/.openclaw/venvs/dispatch/bin/python -m pytest dispatch_v2/tools/eta_calibration/tests/ -q   # 16/16
```

## 9. Bezpieczeństwo produkcji (potwierdzone)
- **READ-ONLY na danych Ziomka:** czyta sla_log/shadow/ziomek_pred/geocode_cache; geokod dostawy **cache-only** (0 zapisów — zweryfikowane: geocode_cache bez zmian).
- **Zapis wyłącznie do `eta_calib_*`** (+ `eta_calib.db`) w `dispatch_state`. Zero mutacji obiektów silnika.
- **GDPR:** raporty/shadow pseudonimizują kuriera (`KURIER_xxxx`); store trzyma tylko `courier_id` (identyfikator systemowy) + coords (bez tekstu adresu). Zero imion/telefonów/rejestracji.
- Zależności doinstalowane do **izolowanego venv** (pyyaml, matplotlib) — nie system, nie `--break-system-packages`.

## 10. Wpięcie w żywe ETA (PRZYSZŁOŚĆ, za ACK)
Nie objęte tym zadaniem. Wzór jak `ENABLE_ETA_CELL_RESIDUAL_CORRECTION`: konsument (np. `calib_maps`-podobny) czyta `eta_calib_*_map.json`, dodaje korektę do obietnicy/przedziału za osobną flagą, po 2 dniach cienia + karcie flip + ACK. Domknąć P90 (conformal) i pokrycie OSRM dostawy przed flipem (patrz `05_walidacja.md §8`).
