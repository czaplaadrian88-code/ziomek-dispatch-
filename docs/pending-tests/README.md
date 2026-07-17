# Testy gotowe, czekające na czyste okno re-seedu manifestu

Pliki `*.py.pending` to **zweryfikowane** testy, których NIE dopisano jeszcze do
`tests/`, bo dodanie zmienia nodeidy → wymusza re-seed `night_guard_suite_manifest.json`.
Re-seed to operacja FLIPMASTER (jeden właściciel na okno). Gdy master jest gorący
(równoległe sesje), re-seed odkłada się do czystego okna.

## hermetic_guard_loud.py.pending
Test fixu `6a27516` (guard subprocesów pada GŁOŚNO, nie cicho). Zweryfikowany:
3/3 na kodzie z fixem; mutation probe (conftest sprzed fixu) → FAILED (ma zęby).

**Aby wprowadzić w czystym oknie:**
1. `git mv docs/pending-tests/hermetic_guard_loud.py.pending tests/test_hermetic_guard_loud.py`
   (albo wklej 3 testy na koniec `tests/test_hermetic_guard_zp207.py`);
2. commit;
3. re-seed: `cd /root/.openclaw/workspace/scripts && venv/bin/python -m dispatch_v2.tools.night_guard --update-manifest --owner <...> --reason adopt-guard-loud-test --base-sha <HEAD>`;
4. `night_guard` → verdict=OK; commit manifestu.
