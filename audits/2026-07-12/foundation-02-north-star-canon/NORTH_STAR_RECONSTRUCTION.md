# North Star Reconstruction — Ziomek

Status: **forensic reconstruction / candidate, not authoritative**. Oś kodu: `c7de9f29127851a59507fac92d14f328336afe61`. Oś pamięci: `ca55742b969404663dff80e4ec2aadfe3c64bdbf`. Dowód live jest ograniczony do okna Promptu 01: 2026-07-12 14:09–14:45 UTC. Każde stwierdzenie normatywne ma claim ID z `CANON_CLAIMS_LEDGER.jsonl`; tekst oznaczony `PROPOSED_SYNTHESIS` nie jest decyzją właściciela.

## Jednostronicowy rdzeń

### Misja

Ziomek ma przejąć rutynowe decyzje dyspozytorskie i osiągać możliwie wysoką, bezpieczną oraz użyteczną autonomię. Ma poprawiać jakość na podstawie rzeczywistych outcome i korekt, a rozwój ma chronić system przed nawrotem znanych klas błędów i utratą wiedzy. (`NS-001`, `NS-002`, `NS-003`, `LRN-001`, `LRN-003`)

### Użytkownicy, interesariusze i wartość

- Operator otrzymuje decyzję wykonalną albo jawny least-damage/ALERT z operacyjnym uzasadnieniem, zamiast ciszy i obowiązku ponownego wykonania całej analizy. (`BR-008`, `NS-009`)
- Właściciel produktu odzyskuje czas z rutynowej koordynacji, pozostając właścicielem semantyki, wyjątków i awansu autonomii. (`NS-003`, `NS-006`)
- Kurier, restauracja i odbiorca są chronieni przez uczciwe deklaracje, commitments, wykonalność i świeżość, a nie przez maksymalizację abstrakcyjnego score. (`BR-001`, `BR-002`, `BR-004`, `BR-005`)

### Docelowa rola Ziomka

Ziomek docelowo wybiera, wyjaśnia i — tylko w jawnie awansowanych klasach — wykonuje decyzje. Gdy normalna wykonalność nie istnieje, nadal pokazuje najmniej szkodliwą opcję ze statusem `NO/ALERT`; nie przepisuje jej na feasible i nie ukrywa problemu. (`NS-002`, `BR-008`, `SYN-002`)

### Docelowa rola człowieka

Właściciel produktu ustala konstytucję biznesową. Potwierdzona bieżąca rola operatora obejmuje zatwierdzenie przerzutu; jego agree/override jest dowodem decyzji człowieka, nie ground truth jakości. `PROPOSED_SYNTHESIS`: człowiek ma docelowo nadzorować awans autonomii i wyjątki, a nie pozostawać domyślnym dispatcherem trudnych przypadków. (`NS-006`, `NS-007`, `GT-001`, `SYN-004`)

### Hierarchia celu

1. Zachowaj nazwane HARD i prawdę deklaracji; SOFT nie może ich osłabić. (`BR-001`, `BR-004`)
2. W normalnym trybie chroń R6=35; 40 jest wyłącznie sufitem zatwierdzonego Alarmu, nigdy przywilejem klasy. Liczby są potwierdzone, lecz dokładne start/end interwału wymagają decyzji `UNK-007`–`UNK-009`. (`BR-002`, `BR-003`, `CF-009`)
3. Wybierz wykonalny plan; przy braku takiego pokaż jawny least-damage/ALERT. (`BR-008`, `BR-011`, `SYN-002`)
4. Dopiero wewnątrz właściwej warstwy optymalizuj kolejność, committed pickup i pozostałe SOFT. (`BR-005`, `BR-006`, `BR-010`, `BR-011`)
5. Zwiększaj autonomię tylko wtedy, gdy jakość, stabilność i odwracalność są udowodnione. (`NS-004`, `NS-005`, `LRN-003`, `SYN-001`)

### Nienaruszalne zasady

- HARD przed SOFT. (`BR-001`)
- R6: 35 normalnie, 40 tylko Alarm/ratunek, nigdy klasa kuriera. (`BR-002`, `BR-003`)
- Zadeklarowany czas jest prawdą HARD, a commitment po przypisaniu nie jest cicho przepisywany. (`BR-004`, `BR-005`, `BR-006`)
- Brak GPS/pre-shift nie jest ukrytą karą; rzeczywista niewykonalność ma własny HARD i powód. (`BR-007`)
- Istniejąca flota prowadzi do jawnej propozycji lub least-damage/ALERT, nie do udawanego braku kandydata. (`BR-008`, `BR-009`)
- Klik, last-inside, arrival, physical pickup i handoff zachowują rozdzielne znaczenia. (`GT-002`–`GT-006`, `GT-010`)
- Jeden case ani jedna korekta nie tworzą globalnej reguły. (`LRN-002`)
- Zmiana zachowania musi mieć dowód, obserwację i rollback. (`LRN-003`)

### Bezpieczna autonomia

`PROPOSED_SYNTHESIS`: bezpieczna autonomia jest własnością konkretnej klasy decyzji, nie całego procesu naraz. Klasa awansuje dopiero, gdy ma jednoznaczny cel i event, zamknięte HARD, dowód na właściwym outcome, jawny zakres niepewności, obserwowalność, kill-switch, stop-loss i sprawdzony rollback. (`SYN-001`)

### Ciągłe uczenie

Uczenie oznacza kontrolowany feedback loop: rozdziel decyzję człowieka od outcome, zachowaj provenance, waliduj zmianę na wspólnym korpusie i dopiero po dowodzie awansuj zachowanie. Nie oznacza auto-retrainingu, imitacji klików ani globalnej reguły po jednym przypadku. (`GT-001`, `LRN-001`–`LRN-004`)

### Sukces

`PROPOSED_SYNTHESIS`: sukces łączy rosnący udział samodzielnych decyzji z lepszym zweryfikowanym outcome, zerową nową regresją HARD, audytowalnością i malejącą potrzebą rutynowej interwencji. Agreement operatora sam nie jest KPI. Eventy pickup/delivery i progi pozostają do związania. (`SYN-003`, `UNK-001`, `UNK-002`, `UNK-005`)

### Antycele

- Maksymalizacja agreement/override rate zamiast jakości outcome. (`GT-001`, `LRN-001`)
- Osłabianie HARD, by skompensować błędną predykcję lub zwiększyć autonomię. (`BR-001`, `NS-004`)
- Ukrywanie szkody jako `KOORD`, `SKIP` lub brak kandydata bez jawnego stanu. (`BR-008`)
- Nazywanie proxy ground truth albo mieszanie różnych anchorów ETA/SLA. (`GT-001`, `GT-008`)
- Awans po jednej korekcie, pojedynczym replayu albo nazwie flagi. (`LRN-002`, `LRN-003`)
- Nadawanie uprawnień live na podstawie samego istnienia executora. (`NS-006`, `IMP-013`, `LIVE-002`)
- Traktowanie historycznej roadmapy, starego promptu lub append-only reference jako aktualnego kanonu. (`CF-001`, `CF-007`, `CF-008`)

## Rozwinięcie

### Klasy decyzji i aktualny zakres autonomii

| Klasa | Docelowa odpowiedzialność Ziomka | Stan intencji | Stan baseline/effective | Granica człowieka |
|---|---|---|---|---|
| Ocena feasibility | Rozstrzygnąć HARD przed scoringiem; zachować jawny `NO`. (`BR-001`) | potwierdzona | normalny filtr istnieje, ale guard jest log-only, a latentny re-admit może zmienić `NO→MAYBE`. (`IMP-001`, `IMP-002`) | Zmiana HARD wymaga właściciela. |
| Wybór normalny | Wybrać najlepszy wykonalny plan względem wyniku floty, nie pojedynczego orderu. (`BR-001`, `BR-019`) | potwierdzona | scoring/selection istnieją; ich wagi są implemented-only. | Operator nie powinien wykonywać całej analizy od nowa. |
| Least-damage | Zawsze pokazać najmniej szkodliwego kandydata ze statusem `NO/ALERT`. (`BR-008`, `SYN-002`) | obowiązek widoczności potwierdzony; execute otwarte | część best-effort działa, lecz `no_solo_candidates` i inne gates pozostawiają dziury. (`IMP-008`, `IMP-009`, `IMP-016`) | Granica execute wymaga `UNK-006`. |
| Alarm R6 | Automatycznie, per decyzja, przejść do Alarmu dopiero po niewykonalności Strategii 1 i 2; R6=40 dla wszystkich. (`BR-002`, `BR-016`) | trigger koncepcyjny i scope potwierdzone | helpery/FSM nie sterują decyzją, observer nie dostarcza pełnego predicate, a stan FSM jest globalny zamiast per-decision. (`IMP-005`, `IMP-019`) | Dla samego Alarmu otwarte są precedencja R27 `±10` (`UNK-003`) i execute (`UNK-006`); cross-mode interval R6 jest osobno w `UNK-007`–`UNK-009`. |
| Przerzut | Wybrać i wyjaśnić przerzut. (`BR-012`) | aktualnie approval-before-execute | shadow/feed, brak nadanego autonomous execute | Operator zatwierdza do jawnego awansu (`UNK-006`). |
| Auto-assignment | Wykonać wyłącznie decyzję w awansowanym bezpiecznym segmencie. (`SYN-001`) | horyzont, nie obecne uprawnienie | executor/gate istnieją; effective OFF w Prompt 01. (`IMP-013`, `LIVE-002`) | Obecnie człowiek pozostaje w pętli. |
| ETA/obietnica | Przewidywać nazwany event i nie przepisywać commitment. (`BR-005`, `GT-008`) | semantyka split potwierdzona | symulator działa, KPI physical pozostaje unbound. (`IMP-014`, `GT-007`) | Właściciel wiąże eventy i koszt (`UNK-001`, `UNK-002`, `UNK-005`). |
| Uczenie/kalibracja | Proponować zmianę na podstawie outcome, nie click agreement. (`LRN-001`, `LRN-003`) | potwierdzona | modele/kalibratory shadow nie dowodzą continuous auto-learning. (`LRN-004`) | Właściciel zatwierdza promocję zachowania i trwałą korektę (`UNK-004`). |

### Eskalacja i ręczne przejęcie

Eskalacja ma być wynikiem jawnego kontraktu: system pokazuje decyzję, status feasibility, naruszony constraint, skalę szkody, niepewność, potrzebne działanie i granicę execute. Nie może być zastąpiona przez `best=None`, cichy hold ani goły score. (`BR-008`, `NS-009`, `SYN-002`)

Aktualnie człowiek pozostaje obowiązkowo w pętli dla przerzutów oraz wszystkich klas, którym nie przyznano execution authority. W oknie Promptu 01 auto-assignment był efektywnie wyłączony. (`BR-012`, `LIVE-002`)

### Niepewność i wyjaśnialność

- Niepewność źródła danych musi zachować swój zakres: no-GPS nie oznacza gorszego kuriera, last-inside nie oznacza pickup, arrival nie oznacza handoff. (`BR-007`, `GT-003`, `GT-005`)
- R6 musi zawsze nazywać początek i koniec interwału. Baseline używa hybrydy ready/picked-up → predicted delivery, ale nie wolno przedstawiać jej jako rozstrzygniętej semantyki produktu. (`CF-009`, `IMP-020`–`IMP-022`, `UNK-007`–`UNK-009`)
- Wyjaśnienie powinno podać operacyjny trade-off i naruszone zasady, a nie tylko final score. (`NS-009`)
- Brak procesowego attestation flagi oznacza `UNKNOWN`, nie automatycznie OFF. (`LIVE-003`)

### Fallback

`PROPOSED_SYNTHESIS`: fallback powinien być typowany jako co najmniej `input_hold`, `zero_fleet`, `degraded_estimate`, `least_damage_no_alert` albo `human_approval_required`. Technicznie nieocenialna flota nie może po cichu udawać `zero_fleet`. Każdy typ musi mieć oddzielne feasibility i execute permission; dzięki temu Always-propose nie osłabia HARD. (`SYN-002`, `SYN-008`)

### Observability i audytowalność

Każda decyzja i zmiana zachowania powinna zachowywać wersję świata, flag, reguły, kandydata, plan, status feasibility, powód, niepewność oraz późniejszy outcome z oddzielnym provenance. Rozdzielenie czystego rdzenia od efektów oraz deterministyczny replay są potwierdzoną intencją architektoniczną wspierającą ten kontrakt, a nie osobnym celem biznesowym. (`ARCH-001`, `ARCH-006`, `ARCH-007`)

### Feedback loop

1. Zapisz decyzję i dokładne wejścia bez wycieku przyszłości. (`ARCH-005`, `ARCH-007`, `GT-007`)
2. Zapisz operator agree/override jako decyzję człowieka, nie outcome. (`GT-001`)
3. Dołącz późniejszy outcome tylko przez związany event i kwalifikowane lineage. (`GT-002`–`GT-009`)
4. Grupuj powtarzalne korekty jako hipotezę, nie automatyczną regułę. (`LRN-002`, `UNK-004`)
5. Porównaj ON/OFF na tym samym korpusie i właściwym oracle. (`LRN-003`)
6. Awansuj po dowodzie, obserwacji i zatwierdzeniu właściwej granicy; zachowaj rollback. (`SYN-001`)

### Kryteria awansu autonomii

`PROPOSED_SYNTHESIS` — warunki minimalne dla pojedynczej klasy decyzji:

- jednoznaczny cel produktu, event i kohorta;
- pełna mapa HARD/SOFT i brak latentnego obejścia;
- porównanie z właściwym baseline na zweryfikowanym outcome;
- jawny koszt fałszywie pozytywnego wykonania i stop-loss;
- obsługa missing/low-confidence bez ukrytej dyskryminacji;
- wyjaśnienie, log decyzji, kill-switch i sprawdzony rollback;
- okres obserwacji oraz jawny właściciel awansu.

Podstawa: `SYN-001`, `LRN-003`, `GT-009`. Lista nie jest kartą autonomii ani zgodą na Prompt 03 implementation.

### Rekomendacja a wykonanie

Rekomendacja i wykonanie są dwoma osobnymi uprawnieniami. Always-propose nakazuje widoczność rekomendacji, ale nie przyznaje execution authority dla niewykonalnego least-damage. Auto-assignment code nie oznacza, że wykonanie jest aktywne. (`BR-008`, `IMP-013`, `LIVE-002`, `UNK-006`)

### Lokalna optymalizacja a wynik całego systemu

Wybór jednego kuriera nie może ignorować stanu worka, commitments, następnych stopów i skutku dla floty. Jednostką jakości jest wynik floty, nie sam najbliższy courier ani największy score. (`BR-019`, `BR-010`, `BR-011`)

## `PROPOSED_SYNTHESIS` — elementy do review jako część kandydata

1. Definicja bezpiecznej autonomii per klasa (`SYN-001`).
2. Widoczny `NO/ALERT` bez execute ponad zatwierdzonym limitem jako pogodzenie Always-propose z HARD (`SYN-002`).
3. Wielowymiarowa definicja sukcesu: autonomous share + właściwy outcome + brak regresji HARD + mniej rutynowej interwencji (`SYN-003`).
4. Człowiek jako właściciel konstytucji/supervisor awansu, nie domyślny fallback (`SYN-004`).
5. Minimalizacja danych w artefaktach decyzji/uczenia jako rozszerzenie kontraktu ETA v1 (`NS-010`).
6. Coverage/missingness fail-closed i zakres twierdzenia nie szerszy od sensora (`SYN-005`, `SYN-006`).
7. Jawny typ fallback dla technicznie nieocenialnej floty, odrębny od `zero_fleet` (`SYN-008`).

Te propozycje wymagają review całego kandydata, ale nie każda stanowi osobną nieredukowalną decyzję w `OWNER_DECISION_PACKET.md`. Żaden punkt nie zmienia aktualnych uprawnień runtime.
