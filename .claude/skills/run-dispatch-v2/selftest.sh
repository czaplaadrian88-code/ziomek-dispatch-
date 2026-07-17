#!/usr/bin/env bash
# Selftest run-dispatch-v2 — sprawdza KONTRAKT BEZPIECZEŃSTWA drivera, nie
# uruchamiając ciężkich/live ścieżek. Kluczowe: bramka ACK MUSI blokować bez
# ZIOMEK_DRIVER_ACK=1. Exit != 0 = regresja kontraktu bezpieczeństwa.
#
# CZEGO NIE robi: nie odpala 'test'/'guard-run' (pełna suita, wymaga ACK),
# nie restartuje niczego. Read-only.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
D="$HERE/driver.sh"
fail=0
want() { if [ "$2" = "$3" ]; then echo "  PASS $1"; else echo "  FAIL $1 (rc oczek=$2 fakt=$3)"; fail=1; fi; }

echo "# selftest run-dispatch-v2"

# 1. składnia
bash -n "$D"; want "bash -n skladnia" 0 $?

# 2. brak argumentu → usage, exit 1
"$D" >/dev/null 2>&1; want "brak komendy → usage exit 1" 1 $?

# 3. BRAMKA ACK: 'test' bez ACK → odmowa (exit 2), NIE uruchamia suity
env -u ZIOMEK_DRIVER_ACK "$D" test >/dev/null 2>&1; want "test bez ACK → HOLD exit 2" 2 $?

# 4. BRAMKA ACK: 'guard-run' bez ACK → odmowa (exit 2)
env -u ZIOMEK_DRIVER_ACK "$D" guard-run >/dev/null 2>&1; want "guard-run bez ACK → HOLD exit 2" 2 $?

# 5. komunikat bramki nazywa ACK (żeby operator wiedział, czego brakuje)
msg=$(env -u ZIOMEK_DRIVER_ACK "$D" test 2>&1 || true)
echo "$msg" | grep -q "ZIOMEK_DRIVER_ACK" && echo "  PASS bramka wskazuje ZIOMEK_DRIVER_ACK" || { echo "  FAIL bramka nie mowi jak odblokowac"; fail=1; }

echo ""
[ "$fail" = "0" ] && { echo "SELFTEST OK"; exit 0; } || { echo "SELFTEST FAILED"; exit 1; }
