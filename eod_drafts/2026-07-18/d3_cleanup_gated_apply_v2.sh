#!/bin/bash
# D3-gold CLEANUP — bramkowany aplikator v2 (pon. 2026-07-20 po peaku).
# Następca v1 (at#218 07:20Z), który ZAABORTOWAŁ: bramki GATE1/2/3 przeszły,
# padł cherry-pick 0f8452b bo master odjechał nocną falą (A8-2, no-GPS v3,
# PKG-2 reassign-release + operator-route-order, bless v13).
#
# ZMIANY vs v1:
#  1. cherry-pick REBASOWANEGO f315f9b (gałąź d3-gold-cleanup po rebase na
#     b3861d3; konflikty rozwiązane ręcznie 20.07: common.py + LOGIC_REFERENCE
#     = obie strony [sąsiedztwo z nocnymi flagami], registry pole "rollback"
#     = strona cleanupu [po wycięciu kodu rollback flagą nie działa]).
#  2. GATE4 NOWA: master musi być przodkiem f315f9b (inaczej rebase nieaktualny
#     — ktoś commitował po 07:30; ABORT zamiast ślepego cherry-picka).
#  3. KROK 7 NOWY: REBLESS manifestu night-guard v13→v14. LUKA v1: cleanup
#     wymienia 4 nodeidy w test_d3_gold_quantile_flip.py, a night_guard
#     porównuje DOKŁADNY zbiór → bez reblessu nocny bieg 01:15 dałby FAŁSZYWY
#     FAIL. Zmierzony dryf (worktree vs manifest v13): 5336→5336, dokładnie
#     4 usunięte / 4 dodane, wszystkie w tym jednym pliku.
#
# Dowody kandydata (worktree wt-d3cleanup-pkgroot, harness ZIOMEK_SCRIPTS_ROOT):
#   pełna suita 5308 passed / 0 failed / 24 skipped / 8 xfailed — identycznie
#   jak bless v13 na masterze. Sanity importu z worktree OK.
# ZERO restartów (flaga OFF od 18.07 → usuwany kod martwy w runtime).
#
# CZAS WYKONANIA ~11 min: pytest leci DWA RAZY i tak ma być —
# VERIFY 2 to nasza bramka z rollbackiem (czerwono → revert + restore klucza),
# a night_guard --update-manifest wewnętrznie robi własny collect+run i jest
# fail-closed (odmawia reblessu przy jakimkolwiek failed/error/xpassed).
# Dlatego uruchamiać PO peaku, nie w oknie 09-12 UTC.
set -u
REPO=/root/.openclaw/workspace/scripts/dispatch_v2
EOD=$REPO/eod_drafts/2026-07-18
REPORT=$EOD/D3_CLEANUP_APPLY_REPORT_V2.txt
VERDICT=$EOD/B2_WINDOW_VERDICT.txt
PY=/root/.openclaw/venvs/dispatch/bin/python
CHERRY_SHA="f315f9b"

cd "$REPO" || exit 1
{
echo "D3 CLEANUP GATED APPLY v2 — $(date -u +%FT%TZ)"
echo "kandydat: $CHERRY_SHA (d3-gold-cleanup po rebase na master; backup ref d3-gold-cleanup-pre-rebase-20260720=0f8452b)"
echo ""

# GATE 0: werdykt okna at#217 istnieje
if [ ! -f "$VERDICT" ]; then echo "ABORT: brak $VERDICT"; exit 10; fi
echo "GATE0 werdykt okna obecny"

# GATE 1: D3 w oknie zielone = 0 nowych odzyskow po flipie
NEW_REC=$(awk '/nowe odzyski PO flipie/{getline; print; exit}' "$VERDICT" | tr -dc '0-9')
echo "GATE1 nowe odzyski po flipie: '${NEW_REC:-brak}' (wymagane: 0)"
if [ "${NEW_REC:-x}" != "0" ]; then echo "ABORT: okno NIEzielone"; exit 11; fi

# GATE 2: flaga nadal false w flags.json (rollback flipa = ABORT)
FLAGVAL=$($PY -c "import json; print(json.load(open('/root/.openclaw/workspace/scripts/flags.json')).get('ENABLE_ETA_QUANTILE_R6_BAGCAP','ABSENT'))")
echo "GATE2 flags.json: $FLAGVAL (wymagane: False/ABSENT)"
if [ "$FLAGVAL" != "False" ] && [ "$FLAGVAL" != "ABSENT" ]; then echo "ABORT: flaga nie jest false — rollback flipa wykryty"; exit 12; fi

# GATE 3: czyste drzewo na plikach celu (kolizja z inna sesja = ABORT)
DIRTY=$(git status --porcelain feasibility_v2.py common.py tests/test_d3_gold_quantile_flip.py tests/test_o2_capz_reseq_2026_07_02.py tools/flag_lifecycle_registry.json tools/night_guard_suite_manifest.json ZIOMEK_LOGIC_REFERENCE.md | grep -v '^??' || true)
if [ -n "$DIRTY" ]; then echo "ABORT: brudne pliki celu:"; echo "$DIRTY"; exit 13; fi
echo "GATE3 drzewo celow czyste"

# GATE 4 (NOWA): rebase aktualny — master MUSI byc przodkiem kandydata
if ! git merge-base --is-ancestor master "$CHERRY_SHA"; then
  echo "ABORT: master NIE jest przodkiem $CHERRY_SHA — ktos commitowal po rebase."
  echo "       Powtorz rebase w wt-d3cleanup-pkgroot i zaktualizuj CHERRY_SHA."; exit 18
fi
echo "GATE4 rebase aktualny (master jest przodkiem kandydata)"
echo ""; echo "git log -3 (kontekst kolizji):"; git log --oneline -3
BASE_SHA=$(git rev-parse --short HEAD)

# APPLY
if ! git cherry-pick "$CHERRY_SHA"; then
  git cherry-pick --abort || true
  echo "ABORT: cherry-pick konflikt — do reki czlowieka"; exit 14
fi
echo "cherry-pick OK: $(git log --oneline -1)"

# VERIFY 1: compile
if ! $PY -m py_compile feasibility_v2.py common.py; then
  git revert --no-edit HEAD; echo "ABORT: py_compile fail — revert wykonany"; exit 15
fi

# FLAGS.JSON: zdejmij klucz PRZED pytestem (ratchet strip-guard czyta zywy plik)
$PY "$EOD/d3_cleanup_flags_key_remove.py"; RC=$?
if [ "$RC" = "3" ]; then git revert --no-edit HEAD; echo "ABORT: flaga=true w miedzyczasie — revert"; exit 17; fi
FLAGS_BAK=/root/.openclaw/workspace/scripts/flags.json.bak-pre-d3-cleanup-2026-07-20

# VERIFY 2: PELNA regresja na kanonie (fail -> przywroc klucz + revert)
$PY -m pytest tests/ -q 2>&1 | tail -2 > "$EOD/d3_cleanup_apply_v2_pytest.log"
echo "regresja: $(head -1 "$EOD/d3_cleanup_apply_v2_pytest.log")"
if grep -qE "[0-9]+ (failed|error)" "$EOD/d3_cleanup_apply_v2_pytest.log" || ! grep -q "passed" "$EOD/d3_cleanup_apply_v2_pytest.log"; then
  [ -f "$FLAGS_BAK" ] && cp "$FLAGS_BAK" /root/.openclaw/workspace/scripts/flags.json
  git revert --no-edit HEAD
  echo "ABORT: regresja czerwona — klucz przywrocony + revert commitu (forward-safe)"; exit 16
fi

# KROK 7 (NOWY): REBLESS manifestu night-guard v13 -> v14
cp tools/night_guard_suite_manifest.json "tools/night_guard_suite_manifest.json.bak-pre-d3cleanup-$(date -u +%Y%m%dT%H%M%S)"
$PY -m dispatch_v2.tools.night_guard --update-manifest \
    --owner "CTO (d3-gold cleanup apply v2)" \
    --reason "cleanup D3-gold: 4 nodeidy test_d3_gold_quantile_flip przepisane na post-removal (parytet gold=std + flaga-widmo inert)" \
    --base-sha "$(git rev-parse HEAD)" 2>&1 | tail -3
NEWVER=$($PY -c "import json; print(json.load(open('tools/night_guard_suite_manifest.json'))['manifest_version'])")
NEWCNT=$($PY -c "import json; print(len(json.load(open('tools/night_guard_suite_manifest.json'))['nodeids']))")
echo "manifest po reblessie: v$NEWVER, nodeidow=$NEWCNT (oczekiwane: v14, 5336)"
if [ "$NEWVER" = "13" ]; then echo "UWAGA: manifest NIE zostal podbity — night-guard 01:15 da FALSZYWY FAIL, popraw recznie!"; fi

# CHECKERY
cd /root/.openclaw/workspace/scripts
$PY -m dispatch_v2.tools.flag_lifecycle_check --live 2>&1 | tail -2
python3 dispatch_v2/tools/flag_hygiene_check.py 2>&1 | head -1
$PY -m dispatch_v2.tools.flag_doc_coverage_check 2>&1 | tail -1
cd "$REPO"

echo ""
echo "SUKCES: kod galezi gold<=4 usuniety z mastera + manifest zreblessowany."
echo "Restart NIEpotrzebny (kod martwy przy fladze OFF); serwisy wezma nowy kod"
echo "przy naturalnych restartach."
echo "ROLLBACK: git revert HEAD (kod) + cp $FLAGS_BAK flags.json (klucz) +"
echo "          cp tools/night_guard_suite_manifest.json.bak-pre-d3cleanup-* (manifest)."
echo "Baza przed apply: $BASE_SHA"
} > "$REPORT" 2>&1
echo "report -> $REPORT"
