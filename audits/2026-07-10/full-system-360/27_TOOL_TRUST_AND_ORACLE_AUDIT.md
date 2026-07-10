# Audyt wiarygodności narzędzi i oracle

## Werdykt

Z 15 instrumentów: 8 ma trust `PARTIAL`, 5 `VOID`, 1 `VALIDATED_NARROW`, a 1
`STALE`. Jedynym wąsko zwalidowanym instrumentem jest strict OSRM health ze
Sprintu 3; nie jest on częścią bazowego mastera ani pełnym health routingu.

75 zielonych testów narzędzi obecnego mastera i 28 testów OSRM health z brancha
Sprintu 3 potwierdza mechanikę, nie prawdziwość każdego zapewnienia operacyjnego.
Pełna struktura jest w `tool_trust_matrix.json` i `tool_trust_matrix.csv`.

| Instrument | Trust | Najważniejsza luka |
|---|---|---|
| odzyskany Audyt 360 | PARTIAL | P2/P3 nie ma pełnego current-snapshot review |
| health scoreboard | PARTIAL | mieszane mianowniki, rc=0 i brak automatycznego consumera |
| legacy OSRM health | VOID | fallback może stworzyć false green |
| strict OSRM health Sprint 3 | VALIDATED_NARROW | dostępność/protokół, nie jakość map/trasy; nie LIVE na bazie |
| world_replay | PARTIAL | niepełny input i tylko wybrane pola |
| world_replay gate/timer | VOID | brak coverage gate i ignorowany rc jednostki |
| night_guard | PARTIAL | parser/shrink/history nie mają mocnych negative controls |
| invariant firewall | PARTIAL | brak consumera/enforcementu, oracle częściowo endogeniczny |
| flag lifecycle | PARTIAL | registry completeness ≠ effective-process completeness |
| flag hygiene | VOID | literal w komentarzu/starym worktree ukrywa sierotę |
| flag docs | PARTIAL | substring i zaakceptowany debt nie dowodzą poprawności |
| flag effect | VOID | obecność nazwy nie dowodzi ON≠OFF |
| scheduled flip gate | VOID | missing gate/heartbeat/zero-marker/concurrency fail-open |
| backup sentinel | PARTIAL | świeżość ≠ odzyskiwalność |
| restore/DR | STALE | drill nie obejmuje dzisiejszego kompletnego backupu |

## Dziesięć zapewnień do sfalsyfikowania

1. Scoreboard GREEN przy brakującym lub starym źródle.
2. OSRM healthy przy odciętym upstreamie i działającym fallbacku.
3. Replay PARITY po mutacji pomijanego pola lub dodatkowym wywołaniu OSRM.
4. Zielony systemd unit gate przy verdict `DIFFS`.
5. Night guard OK po gradual shrink i failure summary bez odzyskanych nodeidów.
6. Lifecycle 504/504 po usunięciu wymaganego carriera procesu.
7. Zero orphanów, gdy nazwa flagi żyje tylko w komentarzu lub starym worktree.
8. Flaga „tested”, gdy test nigdy nie porównuje ON z OFF.
9. Scheduled flip rc=0 przy brakującym floor, heartbeat albo zero markerów.
10. Backup sentinel OK dla snapshotu, którego nie da się odszyfrować/odtworzyć.

## Minimalny kontrakt zaufanego instrumentu

Każdy instrument musi mieć: jawny producer i consumer, denominator i coverage,
freshness/watermark, rozłączne klasy wyników, niezerowy exit dla czerwonego stanu,
golden/known-answer, mutation tripwire oraz termin/ownera reakcji. Bez tego jego
wynik jest obserwacją pomocniczą, nie werdyktem wydania.
