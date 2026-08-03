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

# 2b. skill może recenzować własny kod, ale wyjątek nie przepuszcza wniosków
mkdir -p "$T/self-review/.claude/skills/ziomek-blind-review/author-review"
printf 'neutral driver\n' > "$T/self-review/.claude/skills/ziomek-blind-review/driver.py"
printf 'cudzy wniosek\n' > "$T/self-review/.claude/skills/ziomek-blind-review/AUTHOR_REPORT.md"
printf 'cudzy wniosek w katalogu\n' > "$T/self-review/.claude/skills/ziomek-blind-review/author-review/x.py"
"$PY" "$HERE/driver.py" blind "$T/self-review" --out "$T/b-self-review" >/dev/null 2>&1
if [ -f "$T/b-self-review/.claude/skills/ziomek-blind-review/driver.py" ] \
   && [ ! -e "$T/b-self-review/.claude/skills/ziomek-blind-review/AUTHOR_REPORT.md" ] \
   && [ ! -e "$T/b-self-review/.claude/skills/ziomek-blind-review/author-review" ]; then
  ok "self-review: kod kanonicznego skilla jest, wnioski nadal wyciete"
else bad "self-review: wyjatek sciezki rozszerzyl lub wycial zly zakres"; fi

# 3. manifest NIE w bundlu (leci obok)
[ ! -f "$T/b1/_BLIND_MANIFEST.json" ] && ok "manifest poza bundlem" || bad "manifest wyciekl do bundla"

# 3b. manifest wiąże każdy plik i mutation exact bytes czerwienieje
"$PY" "$HERE/driver.py" verify "$T/b1.manifest.json" >/dev/null 2>&1
want "digest manifest: exact bundle → PASS" 0 $?
printf '\nmutation\n' >> "$T/b1/SKILL.md"
"$PY" "$HERE/driver.py" verify "$T/b1.manifest.json" >/dev/null 2>&1
want "digest manifest: mutation → HOLD" 1 $?

# 4. pin fail-closed: podmiana bajtu → HOLD (rc 1)
cp -r "$HERE/fixtures/case-clean-baseline" "$T/pin"
"$PY" - "$T/pin/SKILL.md" "$T/pin.json" <<'PYEOF'
import hashlib, json, sys
p = sys.argv[1]
json.dump({"SKILL.md": hashlib.sha256(open(p, "rb").read()).hexdigest()}, open(sys.argv[2], "w"))
PYEOF
printf "\nmutacja\n" >> "$T/pin/SKILL.md"
"$PY" "$HERE/driver.py" blind "$T/pin" --pin "$T/pin.json" --out "$T/b2" >/dev/null 2>&1; want "pin mismatch → HOLD" 1 $?

# 4b. częściowy pin nie może otrzymać pin_verified=true
cp -r "$HERE/fixtures/case-clean-baseline" "$T/partial"
"$PY" - "$T/partial/SKILL.md" "$T/partial.json" <<'PYEOF'
import hashlib, json, sys
p = sys.argv[1]
json.dump({"SKILL.md": hashlib.sha256(open(p, "rb").read()).hexdigest()}, open(sys.argv[2], "w"))
PYEOF
printf 'neutral\n' > "$T/partial/EXTRA.md"
"$PY" "$HERE/driver.py" blind "$T/partial" --pin "$T/partial.json" --out "$T/b-partial" >/dev/null 2>&1
want "częściowy pin → HOLD" 1 $?

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

# 9. BRAMKA PII — szybki smoke: wabik po nazwie blokuje budowę i NIE zostawia bundla
mkdir -p "$T/pii/daily_accounting"
printf '# kandydat\n' > "$T/pii/SKILL.md"
printf '{"101": "AAA"}\n' > "$T/pii/daily_accounting/kurier_full_names.json"
"$PY" "$HERE/driver.py" blind "$T/pii" --out "$T/b3" >/dev/null 2>&1; want "PII w zakresie → ODMOWA (rc 3)" 3 $?
[ ! -e "$T/b3" ] && ok "odmowa nie zostawia bundla" || bad "odmowa zostawila bundle [$(ls "$T/b3" 2>/dev/null | tr '\n' ' ')]"

# 10. negatywny oracle PII + mutation ratchet (wabiki syntetyczne, wszystkie klasy)
"$PY" "$HERE/pii_oracle.py" > "$T/pii_oracle.log" 2>&1
if [ $? = 0 ]; then ok "pii_oracle: wabiki odrzucone, mutanty czerwone"
else bad "pii_oracle FAILED — patrz log:"; cat "$T/pii_oracle.log"; fi

echo ""
[ "$fail" = "0" ] && { echo "SELFTEST OK"; exit 0; } || { echo "SELFTEST FAILED"; exit 1; }
