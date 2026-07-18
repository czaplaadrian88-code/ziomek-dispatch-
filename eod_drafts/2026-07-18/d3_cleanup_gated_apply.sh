#!/bin/bash
# D3-gold CLEANUP — bramkowany aplikator (pon. 2026-07-20 09:20 Warsaw, at-job).
# Pre-ACK Adriana 18.07: "Dawaj usuniecie kodu galezi po oknie".
# Nakłada commit z gałęzi d3-gold-cleanup na master TYLKO gdy okno zielone.
# Fail-loud: każdy ABORT zostawia raport i NIE zmienia mastera (forward-safe).
# ZERO restartów (flaga OFF od 18.07 → usuwany kod był martwy w runtime).
set -u
REPO=/root/.openclaw/workspace/scripts/dispatch_v2
EOD=$REPO/eod_drafts/2026-07-18
REPORT=$EOD/D3_CLEANUP_APPLY_REPORT.txt
VERDICT=$EOD/B2_WINDOW_VERDICT.txt
PY=/root/.openclaw/venvs/dispatch/bin/python
CHERRY_SHA="0f8452b1c6c88125a968f21c9fc6f012d1088d5b"

cd "$REPO" || exit 1
{
echo "D3 CLEANUP GATED APPLY — $(date -u +%FT%TZ)"
echo "cherry-pick kandydat: $CHERRY_SHA (galaz d3-gold-cleanup, przetestowana 18.07 w worktree)"
echo ""

# GATE 0: werdykt okna istnieje (at#217 07:00; jesli brak — wygeneruj teraz)
[ -f "$VERDICT" ] || bash "$EOD/b2_window_verdict.sh"
if [ ! -f "$VERDICT" ]; then echo "ABORT: brak $VERDICT"; exit 10; fi

# GATE 1: D3 w oknie zielone = 0 nowych odzyskow po flipie
NEW_REC=$(awk '/nowe odzyski PO flipie/{getline; print; exit}' "$VERDICT" | tr -dc '0-9')
echo "GATE1 nowe odzyski po flipie: '${NEW_REC:-brak}' (wymagane: 0)"
if [ "${NEW_REC:-x}" != "0" ]; then echo "ABORT: okno NIEzielone (odzyski po flipie != 0 — flaga nie byla OFF caly czas?)"; exit 11; fi

# GATE 2: flaga nadal false w flags.json (rollback flipa = ABORT)
FLAGVAL=$($PY -c "import json; print(json.load(open('/root/.openclaw/workspace/scripts/flags.json')).get('ENABLE_ETA_QUANTILE_R6_BAGCAP','ABSENT'))")
echo "GATE2 flags.json wartosc: $FLAGVAL (wymagane: False)"
if [ "$FLAGVAL" != "False" ] && [ "$FLAGVAL" != "ABSENT" ]; then echo "ABORT: flaga nie jest false — rollback flipa wykryty"; exit 12; fi

# GATE 3: czyste drzewo na plikach celu (kolizja z inna sesja = ABORT)
DIRTY=$(git status --porcelain feasibility_v2.py common.py tests/test_d3_gold_quantile_flip.py tests/test_o2_capz_reseq_2026_07_02.py tools/flag_lifecycle_registry.json ZIOMEK_LOGIC_REFERENCE.md | grep -v '^??' || true)
if [ -n "$DIRTY" ]; then echo "ABORT: brudne pliki celu:"; echo "$DIRTY"; exit 13; fi
echo "GATE3 drzewo celow czyste"
echo ""; echo "git log -3 (kontekst kolizji):"; git log --oneline -3

# APPLY
if ! git cherry-pick "$CHERRY_SHA"; then
  git cherry-pick --abort || true
  echo "ABORT: cherry-pick konflikt (master odjechal) — do reki czlowieka"; exit 14
fi
echo "cherry-pick OK: $(git log --oneline -1)"

# VERIFY 1: compile
if ! $PY -m py_compile feasibility_v2.py common.py; then
  git revert --no-edit HEAD; echo "ABORT: py_compile fail — revert wykonany"; exit 15
fi

# FLAGS.JSON: zdejmij klucz PRZED pytestem (ratchet strip-guard czyta zywy flags.json
# i wymaga spojnosci klucz<->ETAP4; exit 3 = rollback flipa wykryty -> revert)
$PY "$EOD/d3_cleanup_flags_key_remove.py"; RC=$?
if [ "$RC" = "3" ]; then git revert --no-edit HEAD; echo "ABORT: flaga=true w miedzyczasie — revert"; exit 17; fi
FLAGS_BAK=/root/.openclaw/workspace/scripts/flags.json.bak-pre-d3-cleanup-2026-07-20

# VERIFY 2: PELNA regresja (fail -> przywroc klucz z backupu + revert commitu)
$PY -m pytest tests/ -q 2>&1 | tail -2 > "$EOD/d3_cleanup_apply_pytest.log"
echo "regresja: $(head -1 "$EOD/d3_cleanup_apply_pytest.log")"
if grep -qE "[0-9]+ (failed|error)" "$EOD/d3_cleanup_apply_pytest.log" || ! grep -q "passed" "$EOD/d3_cleanup_apply_pytest.log"; then
  [ -f "$FLAGS_BAK" ] && cp "$FLAGS_BAK" /root/.openclaw/workspace/scripts/flags.json
  git revert --no-edit HEAD
  echo "ABORT: regresja czerwona — klucz przywrocony z backupu + revert commitu (forward-safe)"; exit 16
fi

# CHECKERY
cd /root/.openclaw/workspace/scripts
$PY -m dispatch_v2.tools.flag_lifecycle_check --live 2>&1 | tail -2
python3 dispatch_v2/tools/flag_hygiene_check.py 2>&1 | head -1
$PY -m dispatch_v2.tools.flag_doc_coverage_check 2>&1 | tail -1
cd "$REPO"

echo ""
echo "SUKCES: kod galezi gold<=4 usuniety z mastera. Restart NIEpotrzebny (kod byl"
echo "martwy przy fladze OFF); serwisy wezma nowy kod przy naturalnych restartach."
echo "Nastepna sesja: odczytaj ten raport + zaktualizuj memory (parity-audit #3 cleanup done)."
} > "$REPORT" 2>&1
echo "report -> $REPORT"
