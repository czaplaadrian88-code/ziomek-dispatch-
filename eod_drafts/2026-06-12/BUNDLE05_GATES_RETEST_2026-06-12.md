# BUNDLE-05 — re-test bramek geometrycznych (zlecenie Adriana 12.06)

**Pytanie:** czy 3 bramki z BUNDLE-05 (V327 cross-quadrant ×0.1, V326 wave
veto, intra-rest gap) działają na pewno — ostrzejszy test + dowód.

## 1. KOREKTA STANU: bramki NIE są wyłączone (audyt 03.06 NIEAKTUALNY)

Audyt twierdził „najmocniejsze bramki geometryczne są WYŁĄCZONE — działają
tylko jako shadow logging" i zalecał flip przed autonomią. **Stan realny
12.06 (zweryfikowany na żywym procesie):**

| Bramka | Flaga | Default | Override w environ shadow | Stan |
|---|---|---|---|---|
| V327 cross-quadrant score×0.1 | `ENABLE_V327_BUG_FIXES_BUNDLE` | "1" (flip 25.04) | BRAK | **ON** |
| V326 wave geometric veto (>3 km) | `ENABLE_V326_WAVE_GEOMETRIC_VETO` | "1" | BRAK | **ON** |
| intra-rest gap (>5 min hard reject) | `ENABLE_INTRA_RESTAURANT_GAP_LIMIT` | "1" | BRAK | **ON** |

Najpewniej audyt czytał stan przez ówczesny `override.conf` (sprzed
sprzątnięcia unitów w ETAP4 10.06) albo pomylił flagi. **Lista „flipów do
ACK" z BUNDLE-05 jest bezprzedmiotowa — nie ma czego flipować.**

## 2. Dowód empiryczny: bramki STRZELAJĄ (korpus 2153 PROPOSE, 02-12.06)

- **V327 mult<1.0:** 724 kandydatów, w tym **143 zwycięzców**; sign-guard
  Z-02 zadziałał 675× (ujemny score → mult pominięty — inwersja kary
  zablokowana).
- **V326 wave veto:** 1825 trafień kandydatów (bonus continuation wyzerowany).
- **intra-rest gap hard reject:** 1 (rzadki, ale żywy).
- **Kolateral na spójnych workach (cos>0.5):** 128 nominalnie, ale
  115/128 = sign-guard (zero efektu); **realnie pomnożonych: 13 (0,6%
  decyzji)**, strata med 12 pkt (max 74), **9/13 i tak wygrało**. Mismatch
  definicji kwadrant-vs-cosinus istnieje, lecz empirycznie marginalny —
  notatka dla E7, nie bug.

## 3. Ostrzejszy test (nowy plik, 22 testy — wszystkie zielone)

`tests/test_bundle05_gates_hardening.py`:
- `apply_bundle_score_mult`: pełna macierz brzegowa — sentinel −1e9
  nietykalny, zero/ujemny pod guardem pominięty, **test dokumentujący
  inwersję bez guarda** (−80×0.1=−8 bije −50 — czemu Z-02 musi zostać ON).
- wave veto: granica progu STRICT (km==thr → brak veta), wymaga bonus>0
  (= luka BUNDLE-03, którą łata `fix_c_additive_pen_shadow` z 12.06), brak
  koordów / pusty pda bez crasha, **veto liczy od chronologicznie OSTATNIEGO
  dropu** (nie pierwszego z listy).
- intra-rest: tz-naive stringi normalizowane, nieparsowalny timestamp
  pomijany bez crasha, dict out-of-order sortowany, równe timestampy = 0,
  **udokumentowana świadoma granica:** przeplot Raj→Pierożek→Raj NIE jest
  łapany (bramka patrzy tylko na sąsiednie pary tej samej restauracji).
- kontrakt „bramki uzbrojone domyślnie" — zmiana env-defaultu na OFF
  krzyczy w testach.

Suita pełna po zmianach: **48 failed = kanoniczny baseline** (nocne 50
zawierało 2 faile klasy time-of-day; ta sama klasa ubrała mój pierwotny
test TABLE03 — porównywał ręcznie liczone raw bez traffic-multipliera,
naprawiony na porównanie cache-path vs fresh-path przez ten sam pipeline).

## 4. Co z tego wynika

1. BUNDLE-05 **zamknięty bez flipów** — bramki działają, mają teraz
   utwardzone testy i dowód korpusowy.
2. Dla E7 (at#131 17.06): geometrię karze już **5 żywych mechanizmów**
   (corridor w score, V327 mult, V326 wave veto, R5 detour 4,0/km,
   intra-rest) + 2 cienie (bundle_fit, fix_c_additive) — strojenie wag MUSI
   deduplikować, nie dokładać.
3. Lekcja #188 w lessons.md (czego nie robić — dla innych sesji).
