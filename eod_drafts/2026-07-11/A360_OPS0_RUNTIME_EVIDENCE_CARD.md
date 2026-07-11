# A360-OPS0 RUNTIME-SYSTEMD-EVIDENCE — karta proponowanego sprintu

Status: **RUNNING — Wave 3, tmux 67; read-only runtime evidence**

Effort: `high`.

Kontrakt wykonawczy: branch `ops/a360-ops0-runtime-evidence`, worktree
`/root/a360_ops0_wt/dispatch_v2`, base `e0fd1e4`. Wykonawca nie edytuje
`ZIOMEK_BACKLOG.md`, kart kolejki ani repo pamieci; integrator odbiera wynik.
Bez odczytu EnvironmentFile/environ, zmian `/etc`, deployu, restartu i testu OOM.

## Problem i dowod

Audyt 360 utrzymal OPS-03/04: nie ma jednego zweryfikowanego kontraktu
`MemoryMax`, `OOMScoreAdjust` i `Restart` per usluga, a precedencja unitow,
drop-inow i EnvironmentFile bywa mylona z tym, co widzi `systemctl show`.
Nie wolno stroic limitow na podstawie defaultow w plikach; najpierw trzeba
zmierzyc efektywny proces, RSS/swap/pressure i zachowanie pod obciazeniem.

## Zakres

- read-only mapa aktywna usluga -> PID -> interpreter -> unit/drop-in ->
  efektywny `MemoryMax`, `MemoryHigh`, `OOMScoreAdjust`, `Restart`, timeouty;
- osobno process RSS oraz cgroup `MemoryCurrent`, `MemoryPeak` i
  `MemorySwapCurrent`; do tego PSI/pressure, page faults i `NRestarts` w
  jawnym, reprezentatywnym oknie godzinowym bez sztucznego obciazenia;
- wykrycie sprzecznych lub martwych drop-inow i wskazanie realnej precedencji;
- rozdzielenie procesow dispatch, panel/API/Papu oraz zdefiniowanie bezpiecznych
  kandydatow zmian usluga-po-usludze;
- raport z progiem, ryzykiem, planem maintenance i rollbackiem, bez odczytu
  wartosci EnvironmentFile lub sekretow.

Sprint uzywa tylko celowanych, bezpiecznych properties `systemctl show`,
wybranych plikow cgroup i niewrazliwych pol `/proc`. Nie wykonuje `systemctl
cat`, nie czyta pelnego `/proc/*/environ`, EnvironmentFile ani inline
Environment. Nie edytuje `/etc`, unitow, drop-inow, venv ani konfiguracji; nie
robi `daemon-reload`, restartu, deployu, kill ani testu OOM.

## Wplyw na Ziomka

Da prawdziwa podstawe do ochrony przed OOM i swap-thrash bez ustawiania limitu,
ktory sam ubije dispatcher. Pokaze tez, ktora konfiguracja jest faktycznie
aktywna, zamiast opierac hardening na martwym pliku.

## Testy, bramki i rollback

- co najmniej dwa odczyty w nazwanym oknie z provenance PID/start/NRestarts;
- parity `systemctl`/`/proc`/cgroup bez drukowania environment;
- negative control: narzedzie nie moze czytac EnvironmentFile, `.env`, sekretow
  ani danych runtime;
- raport oznacza brak pomiaru `UNKNOWN`, nigdy `SAFE`;
- rollback raportu = revert; brak zmian live do cofania.

Kazda pozniejsza zmiana unitow, `daemon-reload` i restart sa osobnym sprintem,
usluga-po-usludze, ze swiezym ACK i backupem drop-inow.
