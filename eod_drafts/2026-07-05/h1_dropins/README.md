# H1 desync + CPUWeight (P-PERF, ACK Adrian 05.07 ~18:35 UTC) — mirror wdrożonych drop-inów

Wdrożone NA ŻYWO w /etc/systemd/system/ (daemon-reload 18:33 UTC, bez restartów serwisów):
- 5 timerów shadow: `RandomizedDelaySec` 60 s (3-minutowe: reassign-global-select, reassignment-shadow,
  ziomek-pred-calibration) / 90 s (5-minutowe: b-route-shadow, bundle-calib-shadow) + `AccuracySec=15s`
  (łamie koalescencję domyślnej AccuracySec=1min — dotąd wszystkie 5 odpalało się w tej samej sekundzie).
- 5 serwisów: `CPUWeight=20` (default 100; Nice=10 był już wcześniej) — joby measurement-only schodzą
  pod żywą ścieżkę decyzji przy wysyceniu CPU.

Efekt uboczny (świadomy): średni interwał tików rośnie o ~30/45 s (random 0..X per odpalenie) — dla jobów
shadow bez znaczenia; ich review'y liczą per-okno czasowe, nie per-liczbę tików.

Rollback (natychmiastowy, bez restartów):
  rm /etc/systemd/system/dispatch-{reassign-global-select,reassignment-shadow,ziomek-pred-calibration,b-route-shadow,bundle-calib-shadow}.timer.d/perf-h1-desync.conf
  rm /etc/systemd/system/dispatch-{reassign-global-select,reassignment-shadow,ziomek-pred-calibration,b-route-shadow,bundle-calib-shadow}.service.d/perf-h1-cpuweight.conf
  systemctl daemon-reload

Pomiar efektu (H1 = pierwsza z trzech dźwigni PERF_TAIL_DIAGNOSIS §3): po 1-2 dniach ruchu
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.perf_budget_report --since 2026-07-05T18:35
vs baseline post-flip 03-05.07 (raport §2.1: peak p95 1738 / ogon 11,0%; high-risk p95 1977 / 15,0%).
Cel: ogon peak/high w stronę poziomu off-peak (~5%). Decyzja o H2 (transit-matrix) po tym pomiarze.
