# COM-P0-OOM-DISPOSABLE-REPRO-04 v1.0-DRAFT — HOLD_DESIGN

## Wynik

Non-MAIN wykonał wyłącznie DESIGN_READ_ONLY. Nie uruchomiono reprodukcji,
kontenera, procesu, sieci ani wolumenu; zero production/repo/shared-memory
mutation przez workera.

Artefakty:

- draft runbook 0600 root:root:
  `/tmp/COM-P0-OOM-DISPOSABLE-REPRO-04_v1.0_DRAFT_RUNBOOK.md`, SHA-256
  `e22e53fde7fdf61ba53d3039fd47b478b1713a241a8cd4e02fce9823929be79a`;
- final handoff 0600 root:root:
  `/tmp/codex_handoff_2026-07-15_1713_COM-P0-OOM-DISPOSABLE-REPRO-04.md`,
  SHA-256 `47821e46666285ecd61d4b0e0aebe681c93d17d3f01f36692f227f4c3cf5c14e`.

Draft jest wartościowym szkieletem: utrzymuje network-none/non-prod boundary,
synthetic-only intent, single executor, limity, oracle classes, exact-ID cleanup,
retention oraz osobny owner gate. Nie jest jednak `EXECUTION_READY`. MAIN i dwa
niezależne review wydają `HOLD_DESIGN`.

## P0 — blokery bezpieczeństwa i authority

1. Inert command używa `docker run`, więc proces startuje przed niezależnym
   sprawdzeniem effective config. Finalna kolejność musi być:
   `create → durable/fsync ledger → inspect+hash+policy PASS → start`.
2. C65 nie jest mechanicznie wdrożone. Porównanie zmiennych środowiskowych nie
   dowodzi latest revision/revoke ani świeżego ACK. Potrzebny stały globalny
   lock, kanoniczny gate store, atomowe consume jednorazowego nonce z fsync,
   expiry/revoke i dowód, że cached ACK nie może przejść.
3. Threat model nie jest jeszcze wymuszony przez carrier: brak czystego
   `DOCKER_CONFIG`, odrzucenia proxy/credential helperów i baked image env,
   przypiętego runtime/seccomp/AppArmor/userns oraz jawnego lokalnego,
   limitowanego log drivera. `network=none` kontenera nie zastępuje zewnętrznej
   blokady egress disposable hosta.
4. Exact image przed stagingiem wymaga osobnych OCI manifest/config/platform/
   RootFS identities oraz prywatnego skanu baked secrets bez emitowania
   wartości. Image ID nie może być przedstawiany jako RepoDigest.

## P1 — blokery metodologii i crash safety

1. Fixtures są montowane pod `/repro-fixtures`, ale aplikacja dostaje osobny
   pusty tmpfs `/home/node/.openclaw`; nie ma zahashowanego materializera ani
   manipulation checku, że właściwy consumer odczytał właściwy synthetic input.
   M0 jawnie nie jest prawdziwym schema OpenClaw.
2. Macierz nie jest jeszcze OVAT: E0 zmienia kilka elementów argv/entrypoint;
   W0 zmienia count i total bytes; V0 presence i value; L0 może zmienić także
   automatyczny V8 heap limit. Potrzebne frozen pary A/B z jednym diffem,
   kolejność A-B-A, warm-cache/time controls i exact fingerprints.
3. READY regex/wrapper/argv nie są zamrożone. Brakuje niezależnych goldenów i
   mutation probes dla NORMAL, V8 fatal, cgroup kill i unrelated failure oraz
   dopasowania do fingerprintu incydentu: Node/V8, heap limit, fatal class,
   ostatnia phase i krzywa pamięci/czasu.
4. Baseline obiecuje V8/GC timeline, lecz mierzy tylko RSS/cgroup; diagnostic
   repeat jest non-parity. `/repro-artifacts` nie jest writable przy read-only
   rootfs, a Node report może zawierać env/network values. Potrzebne bezpieczne
   tmpfs, bazowa heap telemetry i oddzielna redakcja diagnostic artifact.
5. Budżet 12 create nie mieści 12 wariantów, powtórek control/candidate,
   diagnostic repeat ani retry. 30 min × 2 CPU daje górną granicę 60 CPU-min,
   nie 24. Należy zbudować adaptacyjny DAG i policzyć wszystkie create/cleanup
   oraz nienaruszalną rezerwę czasu/narzędzi na cleanup.
6. `--memory 3g --memory-swap 4g` oznacza 4 GiB łącznie RAM+swap, czyli do
   1 GiB swap. Limity muszą używać jednoznacznej semantyki bajtowej; dodać
   nofile/core/file-size i bounded logs.
7. Ledger/cleanup nie są crash-consistent. Potrzebne write-ahead identity,
   atomiczne stany+fsync, inventory recovery oraz niezależny deadline killer.
   Przy mismatch bezpieczny path zatrzymuje znany exact ID, a dopiero deletion
   czeka na review. Evidence seal finalizuje się po cleanup receipt, nie przed.
8. Obiecane kopiowanie workspace do tmpfs i przełączenie read-only dla procesu
   nie ma mechanizmu. Harness/materializer/sampler/classifier/cleanup muszą być
   immutable, hash-bound i objęte execution manifestem.

## P2 — kompletność operacyjna

- zdefiniować szyfrowanie, ownera klucza, TTL job i receipt usunięcia;
- staging exact image oraz provisioning disposable hosta włączyć do kosztu;
- oddzielić limit badawczy od nienaruszalnej rezerwy cleanup;
- w przyszłym ACK zamiast „one run” wskazać dokładny campaign DAG, maksymalną
  liczbę create/repeat/diagnostic/retry oraz budżety;
- wynik eksperymentu może odblokować tylko projekt fixu kandydata; nigdy sam
  unmask/start/restart ani recovery.

## Warunki zdjęcia HOLD_DESIGN

1. Nowy `v1.1-DRAFT` z hash-bound harness i execution manifest schema.
2. Prawidłowe synthetic schemas, exact app paths i manipulation checks.
3. Prawdziwe OVAT A/B/A, frozen oracle/classifier goldeny i mutation probes.
4. Crash-safe `create→ledger→inspect→start`, independent killer i cleanup
   receipt przed final evidence seal.
5. Mechaniczny C65 gate/nonce/revoke oraz pełne host+container isolation.
6. Poprawione limity, log/core/env/image controls i wykonalny campaign budget.
7. Ponowne dwa niezależne review. Dopiero potem MAIN może rozważyć technical
   CTO gate; owner ACK pozostaje osobny i nie obejmuje produkcji ani recovery.

## Stan produkcji

Postcheck podczas review: unit nadal `/dev/null`, masked+inactive/dead,
MainPID0, wants absent, 18789/18790 absent. Recovery pozostaje `HOLD_OFFLINE`.
Browser/:9222 pozostaje oddzielnym SEC1/N-D. MAIN nie uruchomił reprodukcji ani
nie wykonał mutacji produkcji.
