#!/usr/bin/env bash
# Selftest blind-review — MECHANICZNA część oracle, uruchamialna na żądanie.
# Testuje to, co da się sprawdzić bez modelu: blindowanie wycina werdykty, pin
# jest fail-closed, check odrzuca mętne werdykty, korpus jest spójny.
# CZEGO NIE testuje: czy recenzent-model łapie wady — to dowodzą żywi ślepi
# recenzenci (fixtures/EVAL_RESULT.md), nie ten skrypt. Exit != 0 = regresja.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=$(command -v python3)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fail=0
ok()   { echo "  PASS $1"; }
bad()  { echo "  FAIL $1"; fail=1; }
want() { # want <opis> <oczekiwany_rc> <faktyczny_rc>
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (rc oczekiwane=$2 faktyczne=$3)"; fi
}

echo "# selftest ziomek-blind-review"

# 1. eval korpusu
"$PY" "$HERE/driver.py" eval >/dev/null 2>&1; want "eval korpus exit 0" 0 $?

# 2. blind wycina AUTHOR_REPORT, zostawia SKILL.md
"$PY" "$HERE/driver.py" blind "$HERE/fixtures/case-critical-policy-inversion" --out "$T/b1" >/dev/null 2>&1
if [ -f "$T/b1/SKILL.md" ] && [ ! -f "$T/b1/AUTHOR_REPORT.md" ]; then
  ok "blind: SKILL.md jest, AUTHOR_REPORT.md wyciety"
else bad "blind: bundle niepoprawny [$(ls "$T/b1" 2>/dev/null | tr '\n' ' ')]"; fi

# 3. manifest NIE w bundlu (leci obok)
[ ! -f "$T/b1/_BLIND_MANIFEST.json" ] && ok "manifest poza bundlem" || bad "manifest wyciekl do bundla"

# 4. pin fail-closed: podmiana bajtu → HOLD (rc 1)
cp -r "$HERE/fixtures/case-clean-baseline" "$T/pin"
"$PY" - "$T/pin/SKILL.md" "$T/pin.json" <<'PYEOF'
import hashlib, json, sys
p = sys.argv[1]
json.dump({"SKILL.md": hashlib.sha256(open(p, "rb").read()).hexdigest()}, open(sys.argv[2], "w"))
PYEOF
printf "\nmutacja\n" >> "$T/pin/SKILL.md"
"$PY" "$HERE/driver.py" blind "$T/pin" --pin "$T/pin.json" --out "$T/b2" >/dev/null 2>&1; want "pin mismatch → HOLD" 1 $?

# 5. check: dobry werdykt → 0
echo '{"disposition":"CONFIRMED_DEFECT","findings":[{"file":"SKILL.md","line":20,"claim":"x","reproduction":"y"}]}' > "$T/good.json"
"$PY" "$HERE/driver.py" check "$T/good.json" >/dev/null 2>&1; want "check dobry werdykt → 0" 0 $?

# 6. check: brak file/line/reproduction → 1
echo '{"disposition":"CONFIRMED_DEFECT","findings":[{"claim":"wyglada ok"}]}' > "$T/bad.json"
"$PY" "$HERE/driver.py" check "$T/bad.json" >/dev/null 2>&1; want "check werdykt bez file:line → 1" 1 $?

# 7. check: disposition spoza zbioru → 1
echo '{"disposition":"MAYBE"}' > "$T/bad2.json"
"$PY" "$HERE/driver.py" check "$T/bad2.json" >/dev/null 2>&1; want "check disposition spoza zbioru → 1" 1 $?

# 8. check: CLEAN bez findings → 0
echo '{"disposition":"CLEAN","findings":[]}' > "$T/clean.json"
"$PY" "$HERE/driver.py" check "$T/clean.json" >/dev/null 2>&1; want "check CLEAN → 0" 0 $?

echo ""
[ "$fail" = "0" ] && { echo "SELFTEST OK"; exit 0; } || { echo "SELFTEST FAILED"; exit 1; }
