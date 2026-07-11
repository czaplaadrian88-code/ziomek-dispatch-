# Audyt 360 — Wave 3 close — 2026-07-11

Status historyczny close lane'ow: **trzy lane'y zakonczone, clean/pushed; wtedy
zero merge kodu i zero live**.

## Addendum integracyjne — safe source release

DR1A i OPS0 zostały następnie przyjęte do mastera jako source/tool-only w
wydaniu `a360-wave3-safe-source-integrated-20260711`. DR1A przed integracją dostał
fix C32: atestację FD, cooperative root-only lock oraz exact comm+cgroup, z 12
nowymi testami. Lock/producenci nie są jeszcze provisioned live. Nie
zainstalowano restore, adapterów, timera OPS0 ani zmian systemd; nie wykonano
restore, tuningu, flipa, danych ani restartu. D1 i R0 pozostają poza masterem do
ręcznego odczytu at-214.

Raport integratora:
`eod_drafts/2026-07-11/AUDIT360_WAVE3_SAFE_SOURCE_INTEGRATION.md`.

## Wyniki

| Sprint | Branch / final SHA | Efekt | Finalne bramki | Werdykt |
|---|---|---|---|---|
| D1 FIREWALL-EXEMPT-TRUTH | `engine/a360-d1-firewall-exempt-truth` / `e193f2a` (`e75c4a8` kod) | `rule_verdict.v2`: physical breach osobno od odpowiedzialnosci decyzji; EXEMPT/INTRODUCED/UNKNOWN/PASS, provenance, mieszany v1+v2 | DEFAULT 5095/27/10/0; STRICT 5045/77/10/0; focused 36; parity/JSONL 92+1 skip; mutation RED zgodnie z oracle | TECH ACCEPT, merge HOLD do at-214 |
| DR1A RESTORE-PREP | `ops/a360-dr1a-restore-prep` / `0cfa748` (`b035523` kod) | one-shot carrier przez stdin, enforced quota+re-probe, fake 7-stage app smoke, exact run-id cleanup | DEFAULT 5106/27/10/0; STRICT 5056/77/10/0; DR0+DR1A 157; mutation 4/4 | SOURCE/FAKE ACCEPT; DR1B HOLD |
| OPS0 RUNTIME-EVIDENCE | `ops/a360-ops0-runtime-evidence` / `1bb4699` | read-only mapa usluga/PID/cgroup/properties, detektor skażenia testami, UNKNOWN zamiast SAFE | DEFAULT 5095/27/10/0; STRICT 5045/77/10/0; targeted 8 | TOOL ACCEPT; profil/limity UNKNOWN |

Branche startowaly z identycznego base `e0fd1e4`, maja rozlaczne write-sety,
sa zsynchronizowane z origin i nie maja dirty/untracked. Tmux65/66/67 sa idle;
pozostawiono je jako audytowalne sesje, nie zamykano bez polecenia.

Historycznie szczegolowe raporty pozostawaly razem z kodem na branchach. Po
addendum raporty DR1A i OPS0 są także w masterze; raport D1 nadal pozostaje na
branchu:

- D1: `engine/a360-d1-firewall-exempt-truth` →
  `eod_drafts/2026-07-11/A360_D1_FIREWALL_EXEMPT_TRUTH.md`;
- DR1A: `ops/a360-dr1a-restore-prep` →
  `eod_drafts/2026-07-11/A360_DR1A_RESTORE_PREP.md`;
- OPS0: `ops/a360-ops0-runtime-evidence` →
  `eod_drafts/2026-07-11/A360_OPS0_RUNTIME_EVIDENCE.md`.

Nie interpretowac braku raportu D1 na masterze jako braku wyniku; jego merge
jest celowo zamrożony do at-214.

## Co zmienia sie w Ziomku

- D1 nie zmienia wyboru kuriera ani planu. Usuwa false accusation: carried bez
  dowodu counterfactual nie jest nowym bledem decyzji, tylko `UNKNOWN`; realny
  stan pozostaje widoczny przez `physical_status`. Nowa schema nie ma
  enforcementu i nie jest jeszcze w masterze.
- DR1A nie wykonuje restore. Daje fail-closed, testowalny kontrakt do przyszlego
  game-day bez przekazywania sekretu przez argv/env/plik/log. Realne adaptery,
  decrypt, DB i RTO/RPO pozostaja nieudowodnione.
- OPS0 niczego nie stroi. Dowodzi aktualnej mapy 10 aktywnych uslug i daje
  narzedzie do bezpiecznych snapshotow. Pierwszy odczyt byl skazony testami,
  drugi single-sample, dlatego nie ma GO do MemoryMax/OOM/Restart.

## Niezalezny odbior i near-missy

Niezalezny review D1 zatrzymal false `VIOLATION_INTRODUCED`: relacja finalnej
dostawy do nowego pickupu nie dowodzila przejscia przez limit. Po poprawce
`INTRODUCED` wymaga baseline `<= limit` i final `> limit`; bez baseline jest
`UNKNOWN`. Review wymusil tez jawna jednostke `rule_variant_rows` i test
mieszanego ledgera v1/v2.

Dwa incydenty odczytowe nie ujawnily wartosci ani nie zmienily stanu, ale sa
zapisane jawnie:

- D1 jednorazowo otworzyl `/proc/.../environ` i filtrowal piec nazw; output byl
  pusty. Dalsze environ/cmdline zostaly zakazane.
- DR1A trzy razy uzylo ad-hoc `ps ... cmd` zanim zakolejkowana korekta zostala
  przetworzona. Widoczny output nie zawieral sekretu ani PII; po korekcie nie
  bylo powtorki.

Wniosek procesowy: filtrowanie outputu nie cofa faktu odczytu wrażliwego
carriera. Ad-hoc shell ma uzywac `comm`, unit/status i niewrazliwych metadata;
cmdline wolno czytac tylko audytowanemu narzedziu z negative testem braku emisji.

## Live, bramki i rollback

Odbior 18:01 UTC: dispatch-shadow PID 573430, panel-watcher PID 3659486 i
courier-api PID 925329 pozostaly active/running, `NRestarts=0`; parser v2
healthy, errors/pending=0. `flags.json` ma niezmienione mtime 10:27 UTC. Nie
wykonano flipa, danych, migracji, deployu, restartu, daemon-reload ani timera.
`atq` nadal ma wyłącznie at-214 na 2026-07-13 12:15 UTC.

- D1 rollback: `git revert e75c4a8`; przed merge brak runtime do cofania.
- DR1A rollback po integracji: revert commita dokumentacji, fixa C32 `1cdda89` oraz
  cherry-picków `930dbea` i `309330b` w odwrotnej kolejności; brak realnych
  zasobow do sprzatania.
- OPS0 rollback po integracji: revert cherry-picka `daeff60`; brak zmian runtime.

D1 i kod R0 pozostaja poza masterem do odczytu at-214. H1 nadal wymaga
integracji R0+D1, decyzji B-01/B-02 i osobnego ACK. DR1B wymaga zatwierdzonego
carriera/secret-store, realnego manifestu/provenance, niskiego obciazenia i
jawnego ACK encrypted drill. OPS tuning wymaga najpierw czystych godzinnych
okien, potem osobnych sprintow maintenance usluga-po-usludze.

Glowny checkout zachowuje cudzy dirty
`eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`; nie zostal dotkniety.
