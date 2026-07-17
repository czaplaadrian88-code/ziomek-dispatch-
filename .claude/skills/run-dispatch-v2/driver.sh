#!/usr/bin/env bash
# Driver do uruchamiania i diagnozowania Ziomka (dispatch_v2).
#
# KONTRAKT BEZPIECZENSTWA:
#   Komendy READ-ONLY dzialaja bez ACK: health, guard, litter, flags, collect, services.
#   Komendy oznaczone [ACK] moga dotknac zywego stanu i sa ZABLOKOWANE, dopoki
#   nie ustawisz ZIOMEK_DRIVER_ACK=1 w tym samym wywolaniu. To celowe tarcie.
#
# Zywy stan Ziomka: /root/.openclaw/workspace/dispatch_state/  (NIE dispatch_v2/dispatch_state)
# Kanoniczny python: /root/.openclaw/venvs/dispatch/bin/python (systemowy nie ma ortools)

set -uo pipefail

PY=/root/.openclaw/venvs/dispatch/bin/python
REPO=/root/.openclaw/workspace/scripts/dispatch_v2
STATE=/root/.openclaw/workspace/dispatch_state
FLAGS=/root/.openclaw/workspace/scripts/flags.json
HIST=$STATE/night_guard_history.jsonl

SERVICES="dispatch-shadow dispatch-panel-watcher dispatch-gps dispatch-telegram
          dispatch-night-guard dispatch-cod-weekly courier-api nadajesz-panel"

die() { echo "BLAD: $*" >&2; exit 2; }
need_ack() {
  [ "${ZIOMEK_DRIVER_ACK:-0}" = "1" ] || die "komenda '$1' moze dotknac ZYWEGO stanu.
  Wymaga jawnego ACK wlasciciela. Uruchom ponownie z ZIOMEK_DRIVER_ACK=1 dopiero
  PO uzyskaniu zgody. Patrz SKILL.md sekcja 'Bramki ACK'."
}

cmd_services() {
  echo "# stan uslug (read-only)"
  for s in $SERVICES; do
    printf "%-24s %-10s %s\n" "$s" "$(systemctl is-active "$s" 2>/dev/null)" \
                              "$(systemctl is-enabled "$s" 2>/dev/null)"
  done
  echo
  echo "# UWAGA: dispatch-telegram inactive = STAN ZAMIERZONY (kanal wyciszony swiadomie)."
  echo "# NIE naprawiaj tego jako awarii. Patrz memory/telegram-notifications-mute-2026-06-26."
}

cmd_guard() {
  # Nocny straznik regresji. exit 1 = ALARM (zaprojektowany), NIE awaria.
  # journal NIE zawiera werdyktu - jest wylacznie w tym pliku.
  [ -f "$HIST" ] || die "brak $HIST — straznik nie zapisal ani jednego ticku"
  echo "# nocny straznik: ostatnie werdykty (zrodlo: $HIST)"
  echo "# systemd 'failed' przy verdict=ALERT jest POPRAWNY - to alarm, nie crash."
  $PY - "$HIST" <<'PYEOF'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
for r in rows[-6:]:
    print(f"  {r.get('ts','?')}  {r.get('verdict','?')}")
last = rows[-1]
print(f"\n# ostatni tick ({last.get('ts')}): verdict={last.get('verdict')}")
p = last.get("pytest") or {}
print(f"  pytest: {p.get('summary_line','?')}")
for f in p.get("confirmed_failed") or []:
    print(f"  CONFIRMED-FAIL: {f}")
sc = last.get("suite_contract") or {}
if sc.get("contract_ok") is False:
    print(f"  SUITE-CONTRACT ZLAMANY (manifest v{sc.get('manifest_version')})")
for a in last.get("alerts") or []:
    print(f"  ALERT: {a}")
PYEOF
}

cmd_litter() {
  # Smiec testowy w ZYWYM stanie = dowod, ze hermetic guard przeciekl.
  # Guard subprocesow jest FAIL-OPEN (conftest.py:50) => przeciek jest CICHY.
  #
  # KRYTERIUM = PROWENIENCJA PISARZA, nie nazwa pliku. Nazwa klamie:
  # 'liveness_probe_state.json' brzmi jak sonda, a pisze go observability/
  # liveness_probe.py (produkcja). Filtrowanie po '*probe*' dawalo falszywy
  # alarm — a alarm krzyczacy o niczym uczy ludzi go ignorowac (to dokladnie
  # mechanizm, przez ktory 4 noce ALERTU przeszly niezauwazone).
  #
  # Kandydat := plik w zywym stanie, ktorego jedynym LITERALNYM pisarzem jest tests/.
  #
  # ⚠ TO HINT, NIE WERDYKT — i to jest wazne. Grep widzi tylko nazwy WPISANE
  # doslownie. Produkcja czesto KONSTRUUJE nazwe (rotacja `f"{log}.{n}.gz"`,
  # `path + ".lock"`), wiec literalnie wystepuje ona TYLKO w tescie => falszywy
  # alarm na PRAWDZIWYM pliku produkcyjnym. Zmierzone: learning_log.jsonl.2.gz
  # (9,5 MB rotowany log) i *.lock byly tak zgloszone. Skasowanie ich = strata
  # danych produkcyjnych. Dlatego ponizej sa wykluczone, a reszta wymaga
  # RECZNEGO potwierdzenia przed jakimkolwiek usunieciem.
  echo "# kandydaci na przeciek testowy w zywym dispatch_state (read-only, HINT)"
  local found=0
  for f in "$STATE"/*; do
    [ -f "$f" ] || continue
    local base hits
    base=$(basename "$f")
    # nazwy konstruowane przez produkcje — grep ich nie zobaczy, pomijamy
    case "$base" in
      *.lock|*.gz|*.bak*|*.[0-9]) continue ;;
    esac
    hits=$(grep -rl -- "$base" --include="*.py" "$REPO" 2>/dev/null \
             | grep -v "/.claude/" | grep -v "__pycache__" || true)
    [ -z "$hits" ] && continue
    if [ "$(echo "$hits" | grep -vc "/tests/")" -eq 0 ]; then
      printf "  KANDYDAT: %s  %s\n" "$(ls -la "$f" | awk '{print $5" B  "$6" "$7" "$8}')" "$base"
      echo "$hits" | sed 's|^|            jedyny pisarz: |'
      found=1
    fi
  done
  [ "$found" = "0" ] && echo "  brak kandydatow"
  echo
  echo "# POTWIERDZONY przeciek = test zapisal do PRODUKCJI i plik tam ZOSTAL."
  echo "# Skutek uboczny: test z 'assert not os.path.exists(probe)' pada juz ZAWSZE,"
  echo "# niezaleznie od tego, czy guard dziala => alarmu nie da sie ugasic bez cleanupu."
  echo "# PRZED usunieciem: potwierdz recznie, ze zaden kod produkcyjny nie tworzy"
  echo "# tej nazwy dynamicznie. Usuniecie = zapis do zywego stanu = WYMAGA ACK."
}

cmd_flags() {
  # 3 SWIATY FLAG (ADR-004): silnik=flags.json, panel=flags.systemd.env+drop-iny,
  # apka=drop-iny+courier_api/config.py. 'systemctl show -p Environment' NIE pokazuje
  # wartosci z EnvironmentFile - dlatego czytamy plik wprost.
  echo "# flagi SILNIKA (flags.json, hot-reload) — to NIE sa flagi panelu ani apki"
  $PY -c "
import json
d = json.load(open('$FLAGS'))
print(f'  kluczy: {len(d)}')
for k in sorted(d):
    if isinstance(d[k], bool):
        print(f'  {k} = {d[k]}')
" | head -24
  echo "  ..."
  echo
  echo "# Panel i apka maja WLASNE swiaty flag — patrz docs/decisions/ADR-004."
}

cmd_collect() {
  # --collect-only NIE wykonuje testow => nie ryzykuje zapisu do zywego stanu.
  echo "# zbieranie suity (bez wykonania)"
  cd "$REPO" || die "brak $REPO"
  DISPATCH_UNDER_PYTEST=1 $PY -m pytest tests/ -q --collect-only 2>&1 | tail -3
}

cmd_test() {
  # [ACK] Pelna suita wykonuje testy, ktore CELOWO probuja pisac do zywego stanu
  # (np. test_hermetic_guard_zp207). Guard jest FAIL-OPEN => przy jego awarii
  # zapis LADUJE w produkcji. Dlatego bramka.
  need_ack test
  cd "$REPO" || die "brak $REPO"
  echo "# kanoniczna komenda regresji (JEDYNA poprawna — systemowy python3 nie ma ortools)"
  $PY -m pytest tests/ -q "$@"
}

cmd_guard_run() {
  # [ACK] Uruchamia pelna suite (patrz cmd_test). Wartosc: journal NIE ma stderr
  # straznika (0 linii) — tylko tak zobaczysz, dlaczego pada.
  need_ack guard-run
  cd "$REPO" || die "brak $REPO"
  echo "# night_guard z PRZECHWYCONYM stderr (journal go nie zawiera)"
  $PY -m dispatch_v2.tools.night_guard 2>&1
  echo "# exit=$?  (1 = ALARM, nie crash)"
}

case "${1:-}" in
  services)  cmd_services ;;
  guard)     cmd_guard ;;
  litter)    cmd_litter ;;
  flags)     cmd_flags ;;
  collect)   cmd_collect ;;
  test)      shift; cmd_test "$@" ;;
  guard-run) cmd_guard_run ;;
  health)    cmd_services; echo; cmd_guard; echo; cmd_litter ;;
  *)
    cat <<'EOF'
driver.sh — uruchamianie i diagnostyka Ziomka

READ-ONLY (bez ACK):
  health      services + guard + litter razem (zacznij od tego)
  services    stan uslug systemd
  guard       werdykt nocnego straznika (journal go NIE ma)
  litter      smieci testowe w zywym stanie = przeciek guarda
  flags       flagi silnika (flags.json)
  collect     zebranie suity bez wykonania

[ACK] — moga zapisac do ZYWEGO stanu, wymagaja ZIOMEK_DRIVER_ACK=1:
  test [args] pelna regresja pytest
  guard-run   night_guard z przechwyconym stderr
EOF
    exit 1 ;;
esac
