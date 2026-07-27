# WB2 — guardy WARUNKOWE warstwy P-1 (lex-committed-window)

**Status:** ZBUDOWANE, flaga `ENABLE_LEX_WINDOW_GUARDS_V2` **OFF**. Zero live, zero flipów.
**Podstawa:** incydent CZASY 492 (`/root/handover/CZASY_INCYDENT_492_DIAGNOZA_2026-07-27.md`,
plan v4 sekcja 13) + **owner ACK D1+D2** z 2026-07-27
(`memory/owner-decision-czasy-d1d2d3-2026-07-27`) + jednoznaczna specyfikacja Sola RUN3-b
(sekcja 3) + WB1 (`docs/WB1_LEDGER_V2_SCHEMA.md`).

**Poza zakresem:** producent snapshotu loadgov, monitor parytetu, WB3 (kontrakt tożsamości
odbiorów), siatka progów w cieniu, jakikolwiek flip.

---

## 1. Co dokładnie było zepsute

Warstwa P-1 ma mandat: **ratuj okno odbioru** (`czas_kuriera` ± tolerancja) nawet kosztem
miękkiej preferencji carried-first. 2026-07-27 o 18:09:12 przestawiła trasę kuriera 492
przy `viol 0 → 0` — czyli bez żadnej przesłanki okna — dla 2,7 min jazdy, pogarszając
świeżość niesionego jedzenia z 24,3 na 28,5 min. Skala doby: **561 przestawień, z tego 248
bez poprawy okna**; mediana pogorszenia świeżości `+7,0 min`, maksimum `+29,7`.

Trzy luki, każda w innym miejscu:

| # | Luka | Kod (przed WB2) | Skutek |
|---|---|---|---|
| 1 | ochrona opóźnień pomijała niesione | pętla `for oid in assigned` | jedzenie w torbie bez żadnej ochrony |
| 2 | sufit świeżości był ABSOLUTNY | `carry_cap = max(35.0, bcarry)` | wolno pogorszyć 8 → 34,8 min, bo „mieści się pod 35" |
| 3 | brak progu materialności zysku | — | 0,1 min jazdy wystarczyło, by przestawić trasę |

Do tego czwarta, w innej warstwie: przy nieudanym re-czasowaniu ścieżka regeneracji
zapisywała **przestawioną kolejność ze starymi ETA** (`_f6_stale`) — kurier dostawał trasę A
z czasami trasy B.

## 2. Dlaczego guardy są WARUNKOWE (decyzja ownera D1)

Płaskie guardy zabijają dokładnie to, czego warstwa broni. Symulacja Sola na zamrożonym
ledgerze (561 wierszy):

| Wariant | Przeżywa ze ŚCISŁĄ poprawą okna (z 313) |
|---|---|
| G3 `GAIN=1` bezwarunkowe | 211 (**zabite 102**) |
| G2 delta 3 + cap 35 | ≤ 56 (**zabite ≥ 257**) |
| G2 + bezwarunkowe G3 | ≤ 23 (**zabite ≥ 290**) |
| **G3 + G2-delta z wyjątkiem `dviol < bviol`** | **313 (zabite 0)** |

Dlatego: **gdy kandydat ŚCIŚLE zmniejsza naruszenia okna, wspólny guard delty i G3 są
zawieszone.** Incydent 492 mimo to ginie, bo miał `dviol == bviol` — remis nie jest poprawą.

## 3. Kontrakt guardów

Wszystko za flagą `ENABLE_LEX_WINDOW_GUARDS_V2` (default OFF). **Hierarchia klucza lex
(`okno → jazda → świeżość`) NIETKNIĘTA** — guardy zawężają wyłącznie zbiór dopuszczalnych
permutacji, nie zmieniają porządku wyboru.

### Warunek nadrzędny (zawsze, także pod wyjątkiem)

`W(kandydat) ≤ W(baseline)`. Dotąd wynikało to tylko z tego, że identity jest w puli;
teraz jest jawne, bo wyjątek D1 mówi o „ścisłej poprawie" i musi mieć swoją parę.

### G1 — opóźnienie KAŻDEJ dostawy

`handoff_i(kandydat) − handoff_i(baseline) ≤ LEX_WINDOW_DELAY_TOL_MIN` (3,0) dla dostaw
`assigned` **i** `carried`. Baseline = permutacja identycznościowa dokładnie tej sekwencji,
która weszła do warstwy. Porównanie na SUROWYCH minutach, bez zaokrągleń prezentacyjnych.

### G2 — świeżość per sztuka

Dwa warunki, jeden rozłączny los:

* **delta** — `carry_i(kandydat) ≤ carry_i(baseline) + tolerancja` (ta sama stała co G1);
* **cap trybu** — `carry_i(kandydat) ≤ 35` (normalnie) / `40` (tylko kanoniczny Alarm).
  Gdy baseline **sam** jest nad capem, dopuszczalne jest wyłącznie niepogorszenie —
  bez wyjątku absolutnego.

**Cap NIE jest zawieszany wyjątkiem D1.** Zawieszana jest wyłącznie delta.

### G3 — minimalny materialny zysk jazdy

`drive(baseline) − drive(kandydat) ≥ LEX_WINDOW_MIN_GAIN_MIN` (1,0), surowe minuty,
**inclusive** (dokładnie 1,0 przechodzi).

### Wyjątek D1

Gdy `dviol < bviol`: G1, G2-delta i G3 → `exempt`. Zostają: warunek nadrzędny, cap G2,
`breaches > bbreach` (R6 HARD), precedencja i NO-RETURN.

Filtr działa na **KAŻDEJ nie-identity permutacji PRZED wyborem minimum**. Wariant „wybierz
zwycięzcę, potem odrzuć" jest błędny, bo pomija bezpiecznego drugiego kandydata.

### G4 — jedna granica commitu

Wspólna ścieżka obu writerów: `transform → strict retime → final validator
(post-floor/post-pin) → CAS save`.

* re-czasowanie padło na ścieżce regeneracji ⇒ **powrót do kolejności sprzed reorderu**
  (jej czasy są jej własne i prawdziwe) + ALERT. `_f6_stale` na tej ścieżce **znika**;
* finalny walidator sprawdza pokrycie, monotoniczność, precedencję i kopertę świeżości
  względem planu **sprzed całego stosu reorderów** (relax → cap-Z → lex), żeby dwa lokalne
  „+3 min" nie skumulowały się w ciszy;
* „poprzedni plan" = ostatni **TRWAŁY** zwalidowany plan o tym samym aktywnym zbiorze
  zamówień, zgodnej sygnaturze worka i obecnym tokenie generacji. Brak takiego planu ⇒
  `NO_CURRENT_VALID_PLAN` + ALERT, **plik nietknięty**;
* **ratchet:** przy `retime=None` `save_plan` jest niewywoływalne (test statyczny na źródle).

### G5 — loadgov (STRICT-STUB)

`plan_recheck` **nie może** dziś poznać EWMA: to stan pamięci procesu `dispatch_pipeline`.
Wyliczanie go po raz drugi utworzyłoby drugiego writera tej samej prawdy, więc WB2 dostarcza
wyłącznie **konsumenta** kontraktu (`core/loadgov_snapshot.py`): `ewma`, `observed_at`,
`valid_until`, `generation`, `fingerprint`. Brak/stary/niekompletny snapshot ⇒ **strict 5**.
Tolerancja 10 wymaga wg OD-04 kanonicznego **Alarm certificate**, którego nikt nie produkuje
— ścieżka loose jest dziś nieosiągalna i tak ma zostać do osobnej decyzji ownera.
Kanoniczny producent snapshotu = **osobne zadanie**.

## 4. Jedna metryka świeżości (13.2 p.3)

Przed WB2 w jednym punkcie decyzyjnym żyły **cztery** polityki świeżości na **dwóch różnych
zegarach**: lex liczył carry na czasie PRZYJAZDU, a cap-Z na czasie po dwellu. Dropoff dwell
to domyślnie 3,5 min — **więcej niż cały budżet tolerancji (3 min)**. To nie były dwie
tolerancje, tylko jeden błąd rachunkowy w dwóch egzemplarzach.

`core/carry_freshness.py` jest kanonicznym właścicielem definicji:

```
handoff_at_i = arrival_at_i + dwell_dropoff_i
carry_i      = handoff_at_i − possession_at_i
```

Wołają go G2 (`plan_recheck._facts_of`) i cap-Z reseq (`route_simulator_v2._capz_bag_metrics`).
**Polityka cap-Z (Opcja 3: Z = 20, overage, detour) NIETKNIĘTA** — wspólna jest miara, nie próg.

Konsekwencja matematyczna (dowód Sola): dla tego samego zlecenia kotwica possession jest
stała między permutacjami, więc `Δcarry ≡ Δhandoff`. G1 i G2-delta to **jeden predykat**
(`carry_freshness.delta_min`) raportowany w dwóch kohortach — nie dwa niezależne checkery.

## 5. Flagi i progi

| Klucz | Default | Nośnik |
|---|---|---|
| `ENABLE_LEX_WINDOW_GUARDS_V2` | `false` | `ETAP4_DECISION_FLAGS` + `_D3_FALA_A_FLAGS` (hot-flip) |
| `LEX_WINDOW_DELAY_TOL_MIN` | `3.0` | `FLAGS_JSON_NUMERIC_OVERRIDES` |
| `LEX_WINDOW_CARRY_CAP_MIN` | `35.0` | `FLAGS_JSON_NUMERIC_OVERRIDES` |
| `LEX_WINDOW_CARRY_CAP_ALARM_MIN` | `40.0` | `FLAGS_JSON_NUMERIC_OVERRIDES` |
| `LEX_WINDOW_MIN_GAIN_MIN` | `1.0` | `FLAGS_JSON_NUMERIC_OVERRIDES` |

Progi decyzyjne idą przez `flags.json` (hot-reload), **nigdy przez `os.environ`** (13.2 p.8).
Kanonicznym właścicielem wartości jest `common.py`; `plan_recheck` je **wiąże**, nie liczy
po raz drugi. Rollback = flip OFF, bez rewertu i bez migracji danych.

## 6. Ledger v2 — bez mutacji zamrożonego schematu

Wypełniamy pola, które WB1 przygotował: `guards.G1…G5` (`{verdict, threshold_effective,
margin, reason}`), `loadgov.*`, `validator.final`. Kształt sekcji jest **wymuszany w
writerze** — nadmiarowe klucze są odrzucane, bo nowe pole oznacza schemat **v3**, nigdy cichą
mutację v2. Liczniki `candidates.rejected` zyskują pozycje `guard_*`.

Rekord ma zawsze ten sam zestaw kluczy niezależnie od stanu flagi: przy OFF guardy są `null`
(„nie oceniano"), nigdy nieobecne. Analiza nie musi zgadywać, czy pola brakuje, bo guardy
były wyłączone, czy dlatego, że zgubił je writer.

**`guards.G4` i `validator.final` zostają `null` — to jest właściwy zapis, nie brak.** Rekord
`decision` powstaje w warstwie reorderu, a G4 orzeka dwie warstwy wyżej, po floorze i pinie:
w chwili emisji rekordu jego werdykt jeszcze **nie istnieje**. Dopisanie go wymagałoby albo
drugiego zapisu do tego samego wiersza (append-only tego zabrania), albo trzymania rekordu
w pamięci do końca commitu i tracenia go przy każdym wyjątku po drodze. Fakt G4 jest więc
zapisany tam, gdzie powstaje — w `write_receipt`:

| `write.outcome` | `write.error` | Znaczenie |
|---|---|---|
| `written` | `null` | finalny walidator przepuścił, plan utrwalony |
| `not_attempted` | `g4_<powód>` | finalny walidator odrzucił, plik nietknięty |

Rekonstrukcja jest jednoznaczna i **nie wymaga schematu v3**.

**Otwarte:** per-kandydatowa siatka `TOL = 3/5/8/10` (13.3) wymaga danych, których v2 nie
przechowuje (per-item handoff/carry dla NIEWYBRANYCH permutacji). Świadomie **nie**
dokładamy tego do v2 — to decyzja o schemacie v3, nie efekt uboczny WB2.

## 7. Bramka jakości

`tests/test_wb2_conditional_guards.py` (39 testów), fixture
`tests/fixtures/wb2_incident_492_20260727T160912Z.json`.

| Klasa | Co dowodzi |
|---|---|
| **samowalidacja fixture** | przy guardach OFF silnik odtwarza zamrożony wiersz ledgera: ta sama sekwencja, `viol 0→0`, `d_drive −2,7`, świeżość `24,3 → 28,5` |
| **negatywny oracle** | guardy ON przy progach D2 ⇒ zostaje baseline (incydent ginie) |
| **atrybucja** | incydent ginie na guardzie DELTY, nie na capie (28,5 < 35 — dlatego stary sufit go przepuścił) |
| **wyjątek D1** | ścisła poprawa okna przeżywa mimo `Δcarry +6,6`; remis NIE jest przesłanką; cap nadal obowiązuje |
| **mutation** | rozluźnienie delty **przywraca** incydent; usunięcie wyjątku D1 **zabija** naprawę okna; odwrócenie każdego guardu czerwieni |
| **2×2** | `NONCARRIED_DROPOFF_REORDER × LEX` — kohorta carried bajt-identyczna |
| **koniunkcja G2 + cap-Z** | obie warstwy wołają `carry_freshness` (jeden predykat, nie dwa zgodne przypadkiem) |
| **G4** | monotoniczność, pokrycie, precedencja, koperta świeżości, `NO_CURRENT_VALID_PLAN`, ratchet `save_plan` |
| **G5** | brak/niekompletny/przeterminowany snapshot ⇒ strict; ratchet: bez Alarm certificate **nigdy** loose |

## 8. Co MUSI się wydarzyć przed flipem

1. **WB1 ≥ 48 h czystego baseline'u** ledgera v2 z pokryciem klas (warunek D3) —
   kalibracja progów bez tego jest zgadywaniem.
2. Deploy kodu (OFF) **poza peakiem, za osobnym ACK**.
3. Cień ≥ 2 dni + siatka `TOL = 3/5/8/10` osobno dla strict-W i equal-W.
4. Replay z dowodem **pozytywnego** wpływu (nie samego braku regresji).
5. **ACK ownera na flip.** Flaga jest decyzyjna — zmienia zbiór dopuszczalnych permutacji.

---

**Owner:** Adrian Czapla <ac@nadajesz.pl> · **Autor:** WB2 (Opus pod nadzorem CTO/Fable)
