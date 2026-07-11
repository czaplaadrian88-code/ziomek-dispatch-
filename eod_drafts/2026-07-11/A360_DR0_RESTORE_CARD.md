# A360-DR0 RESTORE — karta wykonawcza

Status: RUNNING w tmux63 od 2026-07-11 12:09 UTC.

Effort: `ultra`

## Problem i dowod

Istnienie backupu nie dowodzi odtworzenia. Aktualny
`docs/deploy/ha-lite/restore_from_restic.sh` ma m.in.:

- PostgreSQL z `ON_ERROR_STOP=0`, wiec blad SQL moze nie zatrzymac restore;
- wykrywanie Papu tylko jako `*.sql.gz`, bez zaszyfrowanego wariantu;
- `--load-db` zwiazane z dumpem panelu, bez dowodu osobnego Papu;
- integralnosc sprawdzana tylko dla dumpu panelu;
- brak twardej listy wymaganych sciezek, smoke, licznikow i RTO/RPO;
- brak jawnego wymuszenia `0700` dla katalogu scratch.

## G0 i ownership

- tmux: 63
- branch: `ops/a360-dr0-restore`
- worktree: `/root/a360_dr0_wt/dispatch_v2`
- base: tag `a360-wave1-closed-20260711`
- write allowlist:
  - `docs/deploy/ha-lite/restore_from_restic.sh`
  - jeden dedykowany verifier, jesli odpowiedzialnosci nie da sie utrzymac w
    skrypcie
  - `tests/test_restore_from_restic_a360_dr0.py`
  - `eod_drafts/2026-07-11/A360_DR0_RESTORE.md`
- scratch tylko `0700`, pliki wyniku `0600`.
- zakaz odczytu/wyswietlania tresci `.env`, sekretow, tokenow, PII, adresow i
  danych osobowych. Raport zawiera tylko metadane, liczniki i statusy.
- zywy skrypt, produkcyjne bazy, unity i ruch pozostaja nietkniete.

Wspolne backlogi i pamiec aktualizuje tylko integrator.

## Zachowanie po zakonczeniu

Restore bedzie fail-closed i rozrozni panel, Papu, SQLite oraz wymagane pliki
JSON. Blad SQL, decrypt, brak zasobu lub brak wymaganej sciezki zatrzyma werdykt.
Powstanie zmierzone RTO/RPO i smoke izolowanego odtworzenia. Produkcja nie
zostanie przelaczona ani zmieniona.

## Testy i pozytywny dowod

1. Fake-restic/fake-docker known-answer bez danych produkcyjnych.
2. Blad SQL musi dac non-zero; `ON_ERROR_STOP=1` jest tripwire.
3. Plain i encrypted dump maja jawna, rozlaczna obsluge.
4. Odmowa produkcyjnych nazw DB; brak niejawnego `--force`.
5. Target `0700`, output `0600`, wymagane sciezki i missing-file RED.
6. SQLite `integrity_check`, strict PostgreSQL restore, liczniki i smoke w
   osobnym kontenerze/volumenie.
7. Rzeczywisty drill na scratchu dopiero przy niskim loadzie, bez rownoleglej
   pelnej regresji R0.

## Ryzyka, rollback i bramki

- Ryzyko: przypadkowe uzycie produkcyjnego kontenera lub ujawnienie danych.
  Kontrola: allowlista targetu, fail-closed nazwy, permissions i redakcja.
- Rollback: usunac tylko scratch, tymczasowy kontener/baze/volumen i zrevertowac
  commit. Brak rollbacku produkcji, bo produkcji nie wolno dotknac.
- Jezeli osobny kontener jest niemozliwy, STOP po integralnosci artefaktow.
- Instalacja skryptu, produkcyjna baza, systemd/nginx/DNS, ruch i restart
  wymagaja osobnego ACK.
