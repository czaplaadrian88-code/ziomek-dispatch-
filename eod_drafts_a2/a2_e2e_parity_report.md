# A2 E2E PARYTET DECYZJI — ENABLE_ORTOOLS_DET_TIME_LIMIT OFF vs ON

Data: 2026-07-08 18:53:22 UTC
Ścieżka decyzji: dispatch_v2.core.decide.decide() (pełna fasada) na REALNYCH NEW_ORDER z events.db; serializacja realnym shadow_dispatcher._serialize_result; _strip usuwa pola czysto-czasowe.
READ-ONLY: flags.json nietknięty (klucz A2 tam nieobecny → stała common steruje), zero zapisu do dispatch_state (writer last-pos no-op).

## SELF-CHECK importu
- has_flag_attr: `True`
- common_file: `/tmp/claude-0/-root/2912fb4d-119f-49ae-8211-48bc0ea6976c/scratchpad/a2_pkgroot/dispatch_v2/common.py`
- common_realpath: `/root/.openclaw/workspace/scripts/wt-perf-p95/common.py`
- points_to_worktree: `True`
- flags_json_has_a2_key: `False`
- flag_wiring_probe (budżet solvera): `{'off_budget': None, 'on_budget': (120, 0), 'flag_changes_budget': True}`

## Wynik
- n_cases: **756**  (rozkład floty: {0: 126, 3: 126, 5: 126, 8: 126, 10: 126, 12: 126})
- KONTROLA A vs A' (OFF↔OFF): bajt-różnic **512**, materialnych **0**, primary-różnych case'ów **0**
- TEST A vs B (OFF↔ON): bajt-różnic **370**, materialnych **0**, primary (wybór/trasa/werdykt) **0**
- parytet MATERIALNY (primary): **100.0%**
- parytet BAJTOWY (cała serializacja): **51.058%**
- RED case'ów (primary flip przy czystej kontroli): **0**
- NOISE case'ów (primary flip, kontrola też migoce): **0**
- writer last-known-pos zdywertowany no-opem, prób zapisu MOJEGO procesu: **{'save_last_known_pos': 0}** (0 = zero zapisu do dispatch_state)
- LIŚCIE serializacji różniące się OFF↔ON (pochodne, niematerialne): `{'alternatives': 370, 'best.loadgov_active_orders': 370, 'best.loadgov_load_now': 370}`
- LIŚCIE różniące się w KONTROLI OFF↔OFF (ambient live-state noise): `{'alternatives': 512, 'best.loadgov_active_orders': 512, 'best.loadgov_load_now': 512}`
- pola OFF↔ON, których KONTROLA nigdy nie rusza = przypisywalne A2: **`[]`** (puste = A2 nie wprowadza żadnej dywergencji ponad ambient)

## Niematerialne różnice bajtowe OFF↔ON (charakterystyka)
- idx=5 order_id=EV5 fleet=12 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=7 order_id=EV7 fleet=3 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=8 order_id=EV8 fleet=5 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=9 order_id=EV9 fleet=8 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=10 order_id=EV10 fleet=10 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=11 order_id=EV11 fleet=12 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=13 order_id=EV13 fleet=3 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=14 order_id=EV14 fleet=5 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=15 order_id=EV15 fleet=8 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=16 order_id=EV16 fleet=10 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=17 order_id=EV17 fleet=12 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=19 order_id=EV19 fleet=3 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=20 order_id=EV20 fleet=5 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=21 order_id=EV21 fleet=8 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']
- idx=22 order_id=EV22 fleet=10 kontrola_czysta=False paths=['alternatives', 'best.loadgov_active_orders', 'best.loadgov_load_now']

## WERDYKT
**GREEN-Z-SZUMEM (0 różnic materialnych decyzji OFF↔ON; różnice bajtowe WYŁĄCZNIE w polach, które migoczą też w kontroli OFF↔OFF = ambient live-state/wall-clock, nie A2)**

## Artefakty
- surowe JSONL: `a2_pass_A_off.jsonl`, `a2_pass_Aprime_off.jsonl`, `a2_pass_B_on.jsonl`
- pełne dane: `a2_e2e_parity_summary.json`
