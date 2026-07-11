# A360-D1 FIREWALL-EXEMPT-TRUTH — karta proponowanego sprintu

Status: **IMPLEMENTED/TESTED/PUSHED `e193f2a`; merge HOLD do odczytu at-214**

Effort: `ultra`.

Kontrakt wykonawczy: branch `engine/a360-d1-firewall-exempt-truth`, worktree
`/root/a360_d1_wt/dispatch_v2`, base `e0fd1e4`. Wykonawca nie edytuje
`ZIOMEK_BACKLOG.md`, kart kolejki ani repo pamieci; integrator odbiera wynik.
Bez flipa, enforcementu, deployu, restartu i zapisu do danych live.

Wynik lane'a: kod/testy `e75c4a8`, raport `761d896`, korekta transcriptu
`e193f2a`; branch clean/pushed. Final DEFAULT `5095/27/10/0`, STRICT
`5045/77/10/0`, focused `36/36`, parity/JSONL/reader `92 passed, 1 skipped`.
Carried bez pre-decision baseline jest `UNKNOWN`, nie false `INTRODUCED`.

## Problem i dowod

Audyt 360 SPRI-04 potwierdzil, ze invariant firewall nie rozdziela naruszenia,
ktore juz istnialo w carried/pre-existing planie, od nowego naruszenia
wprowadzonego przez biezaca decyzje. Wspolny licznik moze wiec oskarzac nowa
decyzje za stary stan i nie jest bezpiecznym oracle przed H1 R6-HARD.

Poprzedni lock Sprintu 1 zostal zweryfikowany read-only hashami: 31 plikow jest
identycznych z aktualnym masterem, 6 z juz commitowanymi blobami historycznymi,
unikalnych blobow jest 0, a zaden proces Codex/Claude nie ma cwd w tym worktree.
Dirty worktree pozostaje nietkniety, lecz logiczny ENGINE lock jest zwolniony.
D1 startuje w nowym worktree z aktualnego taga Wave 2, nigdy w Sprint1.

## Zakres

- pelna mapa producerow/consumerow firewalla w core, pipeline, shadow,
  serializerach A+B, jsonl i raportach;
- osobne, rozlaczne klasy `EXEMPT_PREEXISTING`, `VIOLATION_INTRODUCED` i
  `PASS`, z provenance reguly i etapu;
- carried/pre-existing nie znika z diagnostyki, ale nie obciaza decyzji jako
  nowy blad;
- nowa metryka realnie dochodzi do `shadow_decisions.jsonl` i readera;
- instrumentation/log-only: bez enforcementu, flipa, zmiany feasibility,
  scoringu, selection ani planu.

Dokladna allowlista powstaje po mapie kompletności. Minimalnie obejmuje
`core/invariant_firewall.py`, jego testy, jeden kanoniczny punkt wiring oraz
producenta/consumenta metryki; zadnej drugiej implementacji reguly.

## Wplyw na Ziomka

Ziomkowi nie zmienia sie wybor kuriera. Zmienia sie prawda diagnostyczna: system
odrozni problem odziedziczony od problemu stworzonego przez nowa decyzje.
Dopiero taki instrument moze bezpiecznie bramkowac H1.

## Testy, bramki i rollback

- goldeny: carried z naruszeniem przed decyzja = EXEMPT; nowe naruszenie =
  VIOLATION; czysty plan = PASS;
- mutation probes zamiany EXEMPT/VIOLATION i odpiecia jsonl musza byc RED;
- parity wyboru/verdictu/planu bajt-w-bajt; DEFAULT, STRICT, entropy;
- metryka w obu serializerach i realnym jsonl, bez PII;
- rollback = revert instrumentation; brak flagi, danych i restartu.

H1 pozostaje zablokowany do D1 + R0 po `at-214` + decyzji B-01/B-02 i osobnego
ACK. D1 nie zmienia relacji HARD/SOFT. Development moze ruszyc, ale merge D1
czeka na odczyt `at-214`: job wykonuje paired replay na aktualnym silniku, wiec
zmiana core przed werdyktem skazilaby zamrozone okno Sprintu 3.
