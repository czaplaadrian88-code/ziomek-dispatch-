# ETA trusted dla best-effort Tier 2 — raport Codex 21.07

Base `00be30ba`; tylko klon, bez zmian live/`flags.json`, deployu i restartu.

Definicja: trusted = pozycja Known i niesyntetyczna; integralny champion
delivery v2 z `n_pace>=30` dla kuriera; incumbent zweryfikowany exact-support;
świeży (<=36 h) rolling OOT tej samej rodziny z MAE<=8 i n>=200. Brak/dryf =
fail-closed. OFF nie czyta artefaktów i zachowuje 90; ON: trusted->90,
untrusted->30.

Mapa: `eta_trust.py` producer/pure+cache TAK; `dispatch_pipeline.py` konsument
LOG-ONLY TAK; serializer A+B N-D kodowo (wspólny deny-list helper), test obu
lokacji TAK; reader `best_effort_escalation_report.py` TAK (nie filtruje 30);
`common.py` ETAP4+const OFF, lifecycle=shadow i LOGIC_REFERENCE TAK;
`flags.json` N-D — owner zlecił kod default OFF, bez flipu.

Kontrole: AST 5 plików OK; JSON rejestru OK; `diff --check` OK; lifecycle
repo-hermetic 515/515, 0 błędów; effect-coverage 124/135, 0 nowych luk.
Dodano testy: pure, integralność, ON!=OFF, zły sygnał->30, synthetic
`post_wave`+synthetic->30, serializer A+B oraz filtr readera.

regresja: NIEURUCHOMIONA — kanoniczny venv zwraca Permission denied; pełną suitę i werdykt wykonuje CTO zgodnie ze zleceniem
e2e: test funkcjonalny ścieżki shadow->metryki->serializer A+B dodany; wykonanie delegowane CTO
pozytywny-wplyw: test ON/OFF przypina 90 vs 30, mutation kierunku zły sygnał->30
rollback: flaga false/brak klucza (hot, bez restartu) oraz git revert jednego commitu
N-D: core/selection.py, feasibility_v2.py, route_simulator_v2.py, plan_recheck.py — eskalacja pozostaje telemetrią, brak konsumenta decyzyjnego
N-D: courier_resolver.py, core/candidates.py, auto_assign_gate.py — istniejący kanon pozycji tylko odczytany, ranking/score bez zmian
N-D: objm_lexr6.py — klucz/tie-break bez zmian; drive_min_calibration.py i tools/reassignment_forward_shadow.py — pozycja nie zmienia ich kalibracji ani forward-shadow
N-D: flags.json — brak flipu i brak zapisu poza klonem
