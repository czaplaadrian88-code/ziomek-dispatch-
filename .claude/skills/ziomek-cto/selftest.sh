#!/usr/bin/env bash
# Selftest ziomek-cto — oracle ZEWNĘTRZNY, egzekwowany co noc (tests/test_skills_selftest.py).
#
# Oracle NIE jest autowalidacją autora:
#   * scope: lista 8 bliźniaków „dyskryminacja pozycji / no-GPS" pochodzi WPROST z
#     memory/ziomek-change-protocol.md (sekcja „DYSKRYMINACJA POZYCJI", Adrian 29.06 —
#     potwierdzone przypadki produkcyjne, łatane ≥4×). Skill ma je zwrócić KOMPLETNIE.
#   * dod: wymogi (test ON≠OFF, dowód regresji/replay/rollbacku) = Przykazanie #0
#     ETAP 4/5/7 — fixture niepełny MUSI zostać odrzucony, kompletny przyjęty.
#   * brief: delegacja zdrowia do run-dispatch-v2 (zero własnej kopii) — dowód przez
#     mock + grep anty-reimplementacyjny.
# Mutation-probe (C13/C14): sprawdzamy też, że asercje GRYZĄ (usunięty bliźniak z
# rejestru → oracle by to złapał; wycięty dowód regresji → dod FAIL).
# Hermetyczność: zapisy tylko do mktemp; ZIOMEK_CTO_NO_LIVE=1 wyłącza odczyty hosta.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=$(command -v python3)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fail=0
ok()  { echo "  PASS $1"; }
bad() { echo "  FAIL $1"; fail=1; }
want_rc() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (rc oczekiwane=$2 faktyczne=$3)"; fi; }

echo "# selftest ziomek-cto"

# 1. rejestr bliźniaków: parsowalny, klasa pozycyjna ma >=8 miejsc
"$PY" - "$HERE/references/twins-registry.json" <<'PYEOF'
import json, sys
reg = json.load(open(sys.argv[1]))
n = len(reg["klasy"]["rowne-traktowanie-pozycji"]["miejsca"])
sys.exit(0 if n >= 8 else 1)
PYEOF
want_rc "rejestr: JSON OK + klasa pozycyjna >=8 miejsc" 0 $?

# 2. ORACLE scope: temat no-GPS zwraca KOMPLET 8 bliźniaków z protokołu #0
OUT=$("$PY" "$HERE/driver.py" scope "równe traktowanie no-GPS" 2>&1); rc=$?
want_rc "scope: exit 0 na temacie-oracle" 0 $rc
MISS=""
for marker in "F1.7" "_selection_bucket" "_demote_blind_empty" "_best_effort_fastest_pickup_key" \
              "drive_min_calibration" "auto_assign_gate" "reassignment_forward_shadow" "feed.py"; do
  echo "$OUT" | grep -qF "$marker" || MISS="$MISS $marker"
done
if [ -z "$MISS" ]; then ok "scope: komplet 8/8 bliźniaków no-GPS (oracle = protokół #0)"; else bad "scope: ZGUBIONE bliźniaki:$MISS"; fi

# 3. scope: temat spoza rejestru NIE zgaduje (exit 3 + lista klas)
"$PY" "$HERE/driver.py" scope "zupelnie niezwiazany temat xyz" >/dev/null 2>&1
want_rc "scope: nieznany temat → exit 3 (nie zgaduje klasy)" 3 $?

# 4. scope: uszkodzony rejestr = fail-closed (exit 2), NIE pusta mapa
printf '{"klasy": ' > "$T/broken.json"
ZIOMEK_CTO_REGISTRY="$T/broken.json" "$PY" "$HERE/driver.py" scope "no-gps" >/dev/null 2>&1
want_rc "scope: uszkodzony rejestr → exit 2 (fail-closed)" 2 $?

# 5. MUTATION-PROBE rejestru: usunięty bliźniak → oracle z pkt 2 by to ZŁAPAŁ
"$PY" - "$HERE/references/twins-registry.json" "$T/mutated.json" <<'PYEOF'
import json, sys
reg = json.load(open(sys.argv[1]))
m = reg["klasy"]["rowne-traktowanie-pozycji"]["miejsca"]
reg["klasy"]["rowne-traktowanie-pozycji"]["miejsca"] = [p for p in m if "feed.py" not in p["plik"]]
json.dump(reg, open(sys.argv[2], "w"))
PYEOF
MOUT=$(ZIOMEK_CTO_REGISTRY="$T/mutated.json" "$PY" "$HERE/driver.py" scope "równe traktowanie no-GPS" 2>&1)
if echo "$MOUT" | grep -qF "feed.py"; then bad "mutation-probe: usunięty bliźniak wciąż w output (asercja NIE gryzie)"; else ok "mutation-probe: usunięcie bliźniaka z rejestru byłoby złapane przez oracle"; fi

# 6. ORACLE dod: fixture BEZ testu ON≠OFF = ODRZUCONY (exit 1, FAIL na fladze)
DOUT=$("$PY" "$HERE/driver.py" dod "$HERE/fixtures/fixture-diff-incomplete.diff" 2>&1); rc=$?
want_rc "dod: fixture niepełny → exit 1" 1 $rc
echo "$DOUT" | grep -q "FAIL  flaga ENABLE_CTO_FIXTURE_DEMO: test ON≠OFF" \
  && ok "dod: FAIL wskazuje brak testu ON≠OFF" || bad "dod: brak FAIL na teście ON≠OFF"

# 7. ORACLE dod: fixture kompletny (test ON≠OFF + dowody) = PRZYJĘTY (exit 0)
"$PY" "$HERE/driver.py" dod "$HERE/fixtures/fixture-diff-complete.diff" \
      --evidence "$HERE/fixtures/fixture-evidence-complete.txt" >/dev/null 2>&1
want_rc "dod: fixture kompletny + evidence → exit 0" 0 $?

# 8. MUTATION-PROBE dowodu: wycięta linia 'regresja:' → dod MUSI odrzucić
grep -v '^regresja:' "$HERE/fixtures/fixture-evidence-complete.txt" > "$T/ev-noreg.txt"
"$PY" "$HERE/driver.py" dod "$HERE/fixtures/fixture-diff-complete.diff" --evidence "$T/ev-noreg.txt" >/dev/null 2>&1
want_rc "mutation-probe: evidence bez regresji → exit 1 (check uzbrojony)" 1 $?

# 9. brief DELEGUJE zdrowie do run-dispatch-v2 (mock przechwytuje wywołanie)
cat > "$T/mock_driver.sh" <<'EOF'
#!/bin/bash
echo "MOCK-RUN-DRIVER wywolany z argumentami: $@"
EOF
chmod +x "$T/mock_driver.sh"
printf '## 🔴 TEST-P0 otwarty\n## ✅ zamkniete\n' > "$T/todo.md"
BOUT=$(ZIOMEK_CTO_NO_LIVE=1 ZIOMEK_CTO_RUN_DRIVER="$T/mock_driver.sh" ZIOMEK_CTO_TODO="$T/todo.md" \
       "$PY" "$HERE/driver.py" brief 2>&1); rc=$?
want_rc "brief: exit 0 (mock + NO_LIVE)" 0 $rc
echo "$BOUT" | grep -q "MOCK-RUN-DRIVER wywolany z argumentami: health" \
  && ok "brief: zdrowie POSZŁO przez run-dispatch-v2 (health)" || bad "brief: delegacja do run-dispatch-v2 nie zaszła"
if echo "$BOUT" | grep -q "TEST-P0 otwarty" && ! echo "$BOUT" | grep -q "zamkniete"; then
  ok "brief: HOLD/P0 z todo wyciągnięte (✅ odfiltrowane)"
else
  bad "brief: filtr todo HOLD/P0 nie działa"
fi

# 10. anty-reimplementacja: driver NIE ma własnej kopii health-checków run-dispatch-v2
if grep -qE "is-active|night_guard_history" "$HERE/driver.py"; then
  bad "driver reimplementuje health/guard (znaleziono is-active/night_guard_history)"
else
  ok "driver bez reimplementacji health/guard (grep czysty)"
fi

# 11. handoff: szablon z faktami, zero zapisu, limit MEMORY 200 zn. raportowany
HOUT=$(ZIOMEK_CTO_NO_LIVE=1 "$PY" "$HERE/driver.py" handoff --temat t 2>&1); rc=$?
want_rc "handoff: exit 0" 0 $rc
echo "$HOUT" | grep -q "MEMORY.md" && echo "$HOUT" | grep -q "HANDOFF" \
  && ok "handoff: oba bloki (sprint_timeline + MEMORY.md)" || bad "handoff: brak bloków szablonu"

echo ""
[ "$fail" = "0" ] && { echo "SELFTEST OK"; exit 0; } || { echo "SELFTEST FAILED"; exit 1; }
