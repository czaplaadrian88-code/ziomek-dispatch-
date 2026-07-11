# Audit 360 — zamkniecie Wave 2, S0 live i kolejna trojka

Data: 2026-07-11 UTC

Status: **WAVE 2 CLOSED; source/docs/test w masterze; zero restartu dispatch; S0 API LIVE**

## Wyniki zamykanych lane'ow

### R0 REPLAY-TRUTH

- branch `evidence/a360-r0-replay-truth` final `1b38447`, clean/pushed;
- niezalezny werdykt: technicznie ACCEPT, wydaniowo HOLD;
- DEFAULT 4979/0, STRICT 4929/0, reviewer cluster 71/71, entropy bez regresji;
- naprawiono partial frozen input/live fallback, outer validator, OSRM
  consume-once oraz normalizacje `flags` w paired;
- trust pozostaje narrow/partial; surplus recorded OSRM nie jest jeszcze
  wykrywany;
- kod i testy NIE sa scalone. Do integracji weszly wylacznie finalny raport i
  karta. Merge czeka na odczyt at-214 13.07 12:15 UTC.

### DR0 RESTORE

- branch `ops/a360-dr0-restore` final `d873f0b`, clean/pushed;
- niezalezny werdykt: ACCEPT do source-only integracji;
- focused final 138/138; reviewer high-risk 34/34; bash-n, py_compile i
  diff-check PASS;
- source ma strict SQL, wersjonowany manifest/provenance, nieoslabialne real
  progi, scratch/Docker budgety, verify concurrency/capacity i exact cleanup;
- real provenance verify, encrypted Papu, restore DB, app health/start-order i
  service RTO/RPO sa **DR1 HOLD / NOT DONE**;
- live `/root/.openclaw/workspace/scripts/restore_from_restic.sh` nie byl
  instalowany ani zmieniany; zero restic/Docker/DB/uslug w fazie review.

### DEP0 SBOM

- branch `supply/a360-dep0-sbom` final `53730e9`, clean/pushed;
- niezalezny werdykt ACCEPT; DEFAULT 4949/0, STRICT 4899/0, targeted 11/11;
- wersjonowany przenosny config, discovery expected=active 6/6, missing=extra=0;
- byte-identical artifact z branch i main root, bez surowych `/root/`;
- dispatch i API `pip check`/import PASS; cztery direct API sa UNPINNED;
- CVE/EOL pozostaja uczciwie UNKNOWN; zero zmian manifestow, venv i pakietow.

## S0 API-OWNERSHIP — wydanie live

Adrian wydal jawny ACK na deploy i restart. Courier API zostal
fast-forwardowany `073d6a8 -> 320aa0e`, a dokumentacyjny master/origin to
`fa249e6`. Backup git bundle 0600:
`/root/ziomek_backups/a360_s0_api_20260711_1407UTC/courier_api_predeploy.bundle`.
Tag rollback: `a360-s0-api-prelive-20260711-1407UTC`; tag zachowania live:
`a360-s0-api-live-20260711`.

Baseline 167/167; po merge klaster 28/28 i pelne API 186/186; postrestart
ownership 19/19. Jeden restart tylko `courier-api`: PID 3047051 -> 925329,
NRestarts0, active/running, `/api/ping` PASS, zero zagregowanych markerow
Traceback/ERROR/CRITICAL. Shadow, watcher i Telegram nie byly restartowane.

## ENGINE lock — VERIFY-CLOSE

Read-only hash audit starego Sprint1: 30 tracked + 7 untracked; 31 blobow =
aktualny master, 6 = istniejace commity historyczne, 0 unikalnych WIP i 0
procesow ownera. Worktree pozostawiono fizycznie dirty i nietkniety. Logiczny
status: **ENGINE LOCK RELEASED**. Pelny dowod:
`A360_ENGINE_LOCK_VERIFY_CLOSE.md`.

## Kolejne trzy proponowane sprinty

1. `A360-D1 FIREWALL-EXEMPT-TRUTH`, effort `ultra`: rozdzieli stary carried
   problem (`EXEMPT`) od naruszenia stworzonego przez nowa decyzje
   (`VIOLATION`), doprowadzi metryke do jsonl i zachowa parity decyzji. Moze
   ruszyc developersko; merge dopiero po at-214.
2. `A360-DR1A RESTORE-PREP`, effort `high`: syntetyczny carrier canary,
   app/import/health/start-order fake smoke i runbook GO/HOLD, bez sekretu,
   restic i Dockera. Realny DR1B ma effort `ultra` i osobny ACK.
3. `A360-OPS0 RUNTIME-SYSTEMD-EVIDENCE`, effort `high`: read-only pomiar RSS,
   cgroup MemoryCurrent/Peak/Swap, PSI, NRestarts i efektywnej precedencji
   unitow. Bez `systemctl cat`, environ, EnvironmentFile, zmian `/etc` i
   restartow.

Fazy code/prep sa rozlaczne. Realny DR1B nie moze biec w oknie pomiarowym
OPS0. H1 R6-HARD nie jest jeszcze gotowy: wymaga at-214, integracji R0, D1,
decyzji B-01/B-02 i osobnego ACK zmiany HARD.

## Testy integratora i rollback

- baseline `6510e89`: 4938 passed, 27 skipped, 10 xfailed, 0 failed;
- combined STRICT focused DR0+DEP0: 149 passed;
- finalny DEFAULT: 5087 passed, 27 skipped, 10 xfailed, 0 failed;
- finalny `HERMETIC_STRICT=1`: 5037 passed, 77 skipped, 10 xfailed, 0 failed;
- obie pelne suity: 147 znanych warningow, bez nowego faila;
- restore `bash -n`, py_compile, JSON/SBOM, raw-path redaction i diff-check:
  PASS;
- Audit 360 validator: required=35, findings=110, tools=15, PASS;
- lifecycle: 505/505 curated, 0 bledow; entropy bez wzrostu wzgledem baseline;
- rollback point: `a360-wave2-premerge-20260711`; finalny tag:
  `a360-wave2-closed-20260711`.

Rollback source/docs/test Wave 2: jawny revert zakresu po rollback tagu; zmiany
sa kompatybilne wstecz i nie maja automatycznego consumera runtime. R0 code
pozostaje osobno. DR0 nie ma instalacji live do cofania. DEP0 nie zmienil
srodowisk. Dlatego dispatch nie wymagal i nie dostal restartu. Rollback S0 jest
gotowy, lecz przywraca BEZP-02: revert czterech commitow zachowania + jeden
restart API; preferowany fix-forward.

Chroniony dirty `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`, dane
kurierow, flags.json i runtime state pozostaly nietkniete.
