# Sprint 4 — baseline metadata
- Baza: master c2bde58 (Sprint 2 zmergowany)
- Worktree: /root/sprint4_wt/integration/dispatch_v2 (branch sprint4/integration)
- Uruchomienie: ZIOMEK_SCRIPTS_ROOT=/root/sprint4_wt/pkgroot_integration /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q -rf -p no:cacheprovider
- pkgroot: symlink dispatch_v2 -> worktree, flags.json -> /root/sprint4_wt/flags.snapshot.json (snapshot żywego z 2026-07-10 ~08:1x UTC)
- WYNIK: 4710 passed, 24 skipped, 10 xfailed, 0 failed, 123.88s (EXIT=0)
- Log: /root/sprint4_wt/baseline_c2bde58.log
- Kryterium regresji per branch: identyczny wynik (0 failed; passed >= 4710 — nowe testy mogą podnieść liczbę)
