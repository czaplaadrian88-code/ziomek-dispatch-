# Known-answer cases i negative controls

## Wykonane lub istniejące

| Kontrola | Znana odpowiedź | Wynik | Znaczenie |
|---|---|---|---|
| TEST-11 post-flip | checker=`open`, stary test=`known-open` | RED zgodnie z reprodukcją | wykrywa stale assertion i live read dependency |
| world replay ostatniej nocy | verdict musi być DIFFS przy diff/miss | DIFFS | instrument wykrywa problem, lecz unit może pozostać zielony |
| OSRM strict branch | timeout/malformed/fallback nie mogą dać GREEN | 28 testów zielonych | waliduje wąską logikę brancha, nie status LIVE |
| HERMETIC-GUARD | próba zapisu live podczas pytest ma failować | guard zadziałał w błędnym pierwszym baseline | write isolation działa; read isolation TEST-11 nadal nie |
| full HERMETIC_STRICT | live-read test bez kwarantanny ma zostać ujawniony | pięć script-tests RED, guard zablokował odczyt | lista kwarantanny i test isolation są niekompletne |
| package sanitizer | fixture z syntetycznym e-mailem ma zatrzymać build | RED, rc=1 na tymczasowej kopii | potwierdza konkretny detektor e-mail; nie dowodzi wszystkich klas PII/sekretów |
| package counts | 106+4=110; 15 tools; 35 required | validator | wykrywa brak/duplikat/dryf CSV–JSON |
| targeted audit cluster | FEAS/plan/flag/replay mechanizmy nie mają nowych faili | 77 passed, 1 xfailed | raporty nie rozjechały się z istniejącymi testami klastra |

## Wymagane przed podniesieniem trust

| Instrument | Negative control | Oczekiwana odpowiedź |
|---|---|---|
| health scoreboard | brak/stale jednego wymaganego źródła | nie-GREEN i niezerowy rc |
| legacy OSRM health | upstream OFF, fallback ON | RED z provenance upstream |
| world_replay | mutacja trasy/kolejności/pomijanego inputu | DIFF i wskazane pole |
| replay gate | missing-only, low coverage, extra OSRM call | RED; coverage w werdykcie |
| night_guard | gradual shrink + summary bez nodeidów | RED bez reseedu baseline |
| flag lifecycle | brak wymaganego carriera | RED z procesem/ownerem |
| flag hygiene | nazwa tylko w komentarzu/starym worktree | orphan RED |
| flag effect | test zawiera nazwę, ale ON==OFF | brak zaliczenia efektu |
| scheduled flip | brak floor/heartbeat, unknown profile, zero markerów, race | brak zapisu flagi, niezerowy rc |
| backup/restore | corrupt/incomplete/decrypt fail/Papu mismatch | sentinel/restore RED |

Kontrole fault/network/restore muszą działać w izolacji. Nie wolno wykonywać ich
na produkcji jako „testu audytu” bez osobnego planu, backupu i ACK.
