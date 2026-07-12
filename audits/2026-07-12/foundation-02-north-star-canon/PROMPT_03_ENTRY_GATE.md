# Brama wejścia do Promptu 03

## Werdykt Promptu 02

`PARTIAL`

Synteza jest użyteczna, a misja i hierarchia North Star są spójne, ale siedem nieredukowalnych decyzji biznesowych blokuje autorytatywny kanon KPI, dokładny interwał R6 oraz finalne karty wykonawcze autonomii.

## Potwierdzone

- Misja: wysoka, bezpieczna i użyteczna autonomia, poprawiana na realnych outcome/korektach bez utraty wiedzy (`NS-001`–`NS-005`).
- Właściciel produktu zachowuje władzę nad semantyką, HARD/SOFT i awansem autonomii (`NS-006`).
- HARD przed SOFT (`BR-001`).
- R6: 35 normalnie, 40 tylko Alarm/ratunek, nigdy klasa kuriera (`BR-002`, `BR-003`).
- Alarm: auto, per decyzja, dopiero po niewykonalności Strategii 1 i 2; R6=40 dla wszystkich (`BR-016`).
- Declared time jest HARD; committed pickup jest chroniony, normalne ±5 jest SOFT (`BR-004`–`BR-006`).
- No-GPS/pre-shift nie może być ukrytą karą (`BR-007`).
- Always-propose wymaga feasible albo jawnego least-damage/ALERT przy istniejącej flocie (`BR-008`, `BR-009`).
- Agree/override jest decyzją/feedbackiem, nie outcome; pickup/delivery click jest proxy, nie physical event (`GT-001`, `GT-010`).
- Arrival, last-inside, exit, pickup, delivery arrival i handoff mają rozdzielne znaczenia (`GT-002`–`GT-006`).
- Uczenie ma korzystać ze zweryfikowanych outcome i nie może globalizować jednego przypadku (`LRN-001`–`LRN-003`).
- ADR-008 został rozstrzygnięty chronologicznie: fizyczny `core/` istnieje; ważny pozostaje time-scoped wymóg świadomego stranglera/#0 (`ARCH-002`, `ARCH-003`).
- Parser v2 był efektywny i zdrowy w oknie Promptu 01, ale nie jest elementem North Star (`IMP-012`, `LIVE-001`).
- Auto-assignment był efektywnie OFF w oknie Promptu 01 (`LIVE-002`).

## Nierozstrzygnięte

1. Kanoniczny cel/event pickup (`UNK-001`).
2. Kanoniczny cel/event delivery (`UNK-002`).
3. Precedencja Alarmowego R27 `±10` wobec commit `max ±5/nietykalny` (`UNK-003`). Trigger i scope Alarmu są potwierdzone; exact machine predicate/observability są kwestią techniczną.
4. Formalny akt promocji trwałej korekty do kanonu (`UNK-004`).
5. KPI coverage/missingness/cost/promotion gate po związaniu eventów (`UNK-005`).
6. Execution boundary per klasa dla ALERT/least-damage/przerzutu (`UNK-006`).
7. Kanoniczny interwał R6: start, end oraz relacja ready-age do in-vehicle age (`CF-009`, `UNK-007`–`UNK-009`). Progi 35/40 i auto-aktywacja Alarmu nie są ponownie otwarte.

Dokładne opcje i rekomendacje `PROPOSED_SYNTHESIS` są tylko w `OWNER_DECISION_PACKET.md`.

## Spójność North Star

`YES FOR MISSION AND GOAL HIERARCHY; PARTIAL FOR HARD SEMANTICS.`

Nie ma centralnego konfliktu między autonomią a jakością: autonomia jest celem, lecz HARD, jakość, stabilność i dowód mają pierwszeństwo przed tempem awansu. Always-propose dotyczy widoczności decyzji; nie jest samoistnym uprawnieniem do wykonania niewykonalnego planu. Jednocześnie R6 ma potwierdzone liczby i precedencję, ale niejednoznaczny interval start/end; tej luki nie wolno ukryć pod nazwą baseline anchor.

## Gotowość Product Canon Candidate

`READY FOR OWNER REVIEW — NOT READY TO BECOME AUTHORITATIVE.`

Kandydat ma provenance i oddziela intencję, baseline implementation, effective runtime, outcome evidence, historię oraz syntezę. Nie powinien zastąpić oficjalnego kanonu bez rozstrzygnięcia pakietu decyzji i osobnej akceptacji właściciela.

## Brama do konstytucji Codexa i kart autonomii

`READY_AFTER_OWNER_DECISIONS`

Warunki:

- OD-05 musi określić, jak Codex rozpoznaje trwałą zmianę kanonu.
- OD-06 musi określić granicę execute/approve per klasa.
- OD-04 musi być rozstrzygnięte przed finalną kartą R27/Alarm dla planów z odchyłką ponad ±5.
- OD-01/OD-02/OD-03 muszą być rozstrzygnięte przed finalnymi kartami ETA/KPI i jakąkolwiek promotion gate.
- OD-07 musi związać start/end R6 i relację ready-age/in-vehicle age przed finalną konstytucją HARD, oracle R6 i kartą R6/Alarm.
- Do tego czasu wolno jedynie przygotować ogólną strukturę z jawnymi stanami `UNBOUND/HOLD`; nie wolno przyznać nowych uprawnień, zmieniać flag ani implementować kart.

## Co nie blokuje późniejszej pracy

- Techniczny mapping driftu R6/R-DECLARED/Always-propose/no-GPS jest wystarczający do przyszłej analizy, lecz Prompt 02 nie proponuje diffu.
- Fizyczny `core/` i parser są rozstrzygniętymi faktami technicznymi.
- Pytania KPI nie blokują review centralnego North Star; blokują tylko autorytatywne KPI i zależne karty.

Ten plik nie jest Promptem 03 i nie rozpoczyna jego wykonania.
