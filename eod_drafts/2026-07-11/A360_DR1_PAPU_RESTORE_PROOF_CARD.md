# A360-DR1A RESTORE-PREP — karta sprintu: kontrakt + wynik

Status: **SOURCE/FAKE ACCEPT; SOURCE IN MASTER w wydaniu `a360-wave3-safe-source-integrated-20260711`; NOT INSTALLED/NOT EXECUTED; DR1B HOLD**

Effort: `high`.

Kontrakt wykonawczy: branch `ops/a360-dr1a-restore-prep`, worktree
`/root/a360_dr1a_wt/dispatch_v2`, base `e0fd1e4`. Wykonawca nie edytuje
`ZIOMEK_BACKLOG.md`, kart kolejki ani repo pamieci; integrator odbiera wynik.
Bez realnego klucza, restic, decrypt, Dockera, DB, deployu i restartu.

Wynik lane'a: kod `b035523`, korekta raportu C32 `0cfa748`; branch
clean/pushed. Final DEFAULT `5106/27/10/0`, STRICT
`5056/77/10/0`, DR0+DR1A `157/157`, mutation `4/4`. Powstal bezsekretowy
one-shot carrier przez stdin, enforced quota z re-probe, fake siedmiostopniowy
app smoke i exact run-id cleanup. Realne adaptery pozostaja nieinstalowane.

Integracja usunęła bloker C32 i dodała 12 testów FD/lock/comm/cgroup; targeted
STRICT ma 177/177. Operacyjny backup i runnery nie są jeszcze przepięte na nowy
root-only lock, więc nie zmienia to werdyktu DR1B ani stanu live.

Ponizsze sekcje opisuja pierwotny kontrakt wykonawczy; wynik rzeczywisty jest
wyzej oraz w raporcie lane'a na branchu wskazanym w close Wave 3.

## Problem i dowod

A360-DR0 naprawia source restore i dowodzi syntetycznej mechaniki, ale pelny
realny RTO Papu pozostaje nieudowodniony. Managed guard slusznie zatrzymal probe
przed przekazaniem klucza. Nie ma zatwierdzonego, bezsekretowego kontraktu
carriera ani app-level smoke. Faza A moze przygotowac i mutation-testowac te
granice bez sekretu, restic i Dockera; realny drill jest osobna faza B.

## Zakres

- zaprojektowac syntetyczny interfejs jednorazowego carriera, ktory nie loguje,
  nie serializuje i nie ujawnia wartosci; testy uzywaja tylko canary;
- pinowac provenance snapshotu (tag/path/hostname), freshness i wymagany
  manifest artefaktow;
- przed mutacja sprawdzic brak rownoleglego backupu, reserve/quota scratch oraz
  Docker root;
- zbudowac fake app/import/health/start-order smoke i kontrakt strict SQL;
- udowodnic exact `run_id` cleanup na fake partial-create;
- przygotowac runbook i jednoznaczna bramke GO/HOLD dla fazy B.

Zakazane w fazie A: odczyt realnego repo/restic, klucz, decrypt, Docker,
produkcyjna baza/kontener, instalacja skryptu live, systemd/nginx/DNS,
przelaczenie ruchu i wildcard/prune cleanup.

## Wplyw na Ziomka

Przygotuje bezpieczny, testowalny tor do realnego game-day bez obchodzenia
ochrony sekretow. Sam sprint A nie oglasza realnego RTO/RPO ani sprawnosci
konkretnego snapshotu.

## Testy, bramki i rollback

- fake carrier/restic/docker mutation i negative controls;
- fail-closed przy zlym provenance, brakujacym artefakcie, zbyt malym miejscu,
  aktywnym backupie, bledzie decrypt/SQL/app-smoke;
- finalnie zero targetow, cache, kontenerow, volumenow i aktywnego restic;
- rollback kodu przez jawny revert; rollback drilla przez exact-label cleanup.

Start fazy A dopiero po czystym, pushed i zaakceptowanym DR0. Faza B ma effort
`ultra` i wymaga osobnego jawnego ACK wymieniajacego realny encrypted drill,
zatwierdzonego carriera oraz niskiego obciazenia. Dzisiejszy ogolny ACK na
deploy/restart API nie jest ACK na decrypt ani restore drill.
