# A360-DR1A RESTORE-PREP — karta proponowanego sprintu

Status: **PROPOSED — drugi, rownolegly; tylko faza A code/prep**

Effort: `high`.

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
