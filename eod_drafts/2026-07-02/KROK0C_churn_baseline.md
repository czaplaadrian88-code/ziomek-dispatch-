# KROK 0c — BASELINE migotania propozycji (proposal churn) — 2026-07-02

**Cel:** stały, powtarzalny pomiar migotania top-1 proponowanego kuriera między
tickami, ZANIM wejdzie histereza. Bez tego baseline nie da się udowodnić, że
przyszła histereza redukuje churn bez regresji.

**Narzędzie (read-only, CLI):** `dispatch_v2/tools/proposal_churn_monitor.py`
**Źródło:** `dispatch_state/reassignment_shadow.jsonl` (forward-shadow reassignmentu,
timer `dispatch-reassignment-shadow` ~co 3 min). Rekord = ocena JEDNEGO zlecenia w
JEDNYM ticku. `best_cid` = top-1 proponowany kurier; churn = zmiany `best_cid`
wzdłuż ticków tego samego `order_id`. Timestampy parsowane kanonem
`ledger_io.parse_sla_ts` (ts writera = aware ISO UTC).

**Reprodukcja:**
```
cd /root/.openclaw/workspace/scripts
python3 -m dispatch_v2.tools.proposal_churn_monitor --all --per-day      # pełne okno
python3 -m dispatch_v2.tools.proposal_churn_monitor --window-days 7      # ostatnie 7 dni
```

---

## 1. Baseline — liczby

Denominator churnu = zlecenia z ≥2 tickami (jedyne, które fizycznie mogą churnować).
Jawne `n` przy każdej liczbie.

| metryka | pełne okno (23.06→02.07) | ostatnie 7 dni (25.06→02.07) |
|---|---|---|
| rekordów (ticków) | 25 363 | 19 392 |
| zleceń (distinct) | 2 140 | 1 659 |
| zleceń ≥2 ticki (denom) | **2 130** | **1 652** |
| ticki/zlecenie (mediana / śr / max) | 11 / 11,85 / 84 | 11 / 11,69 / 66 |
| **≥1 zmiana top-1** | **1782/2130 = 83,7%** | **1353/1652 = 81,9%** |
| **≥3 zmiany top-1** | **1167/2130 = 54,8%** | **865/1652 = 52,4%** |
| **śr. zmian/zlecenie** | **3,25** | **3,16** |
| łącznie zdarzeń zmiany | 6 962 | 5 244 |

Zgodne z ad-hoc pomiarem team-leada (83% / 54,5% / 3,26 przy 2104 zleceniach,
mediana 11 ticków) — różnice to inny denom (my: ≥2-tick orders) i +19 rekordów
dopisanych na żywo w trakcie sesji. **Baseline potwierdzony i zamknięty w toolu.**

**Rozkład zmian/zlecenie (pełne okno, zlec≥2ticki):** 0→348, 1→308, 2→307,
3→312, 4→239, 5→176, 6→174, 7→97, 8→80, 9→42, 10→26, 11+→21 (ogon do 27 zmian).
Ok. 16% zleceń nie migocze wcale; masa churnu siedzi w paśmie 1–6 zmian.

**Kontekst:** 67,4% ticków proponuje reassign (`would_reassign=true`), 32,6% =
hold (best==holder). Czyli w większości ticków shadow chce ruszyć zlecenie — tym
istotniejsza stabilność tego, KOGO proponuje.

---

## 2. Dekompozycja przyczyn zmiany

⚠ **Ograniczenie danych:** log zapisuje `pool_feasible` jako **LICZBĘ**, nie
imienny skład puli. Nie da się więc TWARDO rozstrzygnąć „poprzedni best wypadł z
puli" (feasibility churn — histereza NIE naprawi) vs „poprzedni best dalej
feasible, scoring się przetasował" (czysty flicker — histereza naprawi). Poniżej
PROXY; procenty liczone od 6 962 zdarzeń zmiany (pełne okno; 7d w nawiasach).

### a) wg zmiany liczności puli feasible
| klasa | pełne okno | 7d | interpretacja |
|---|---|---|---|
| **pool_same** (liczność bez zmian, best się przetasował) | 3010 (43,2%) | 43,1% | najczystszy kandydat na **flicker** — histereza pomoże NAJBARDZIEJ |
| **pool_shrank** (liczność spadła) | 2537 (36,4%) | 36,9% | prawdopodobnie ktoś wypadł → **feasibility churn**, histereza słabo pomoże |
| **pool_grew** (liczność wzrosła) | 1415 (20,3%) | 20,0% | pojawiła się nowa, lepsza opcja — histereza sporna (trzymać starego vs przełączyć) |

### b) wg udziału holdera w zmianie
| klasa | pełne okno | 7d |
|---|---|---|
| swap między dwoma NIE-holderami (best other1→other2) | 4289 (61,6%) | 62,9% |
| propozycja znika (other→holder) | 1464 (21,0%) | 20,5% |
| propozycja pojawia się (holder→other) | 1209 (17,4%) | 16,6% |

61,6% zmian to przerzucanie best między dwoma zewnętrznymi kandydatami — to
właśnie pasmo, w którym flicker boli koordynatora (proponowany kurier ciągle
inny, choć zlecenia i tak nikt nie trzyma tak/inaczej „lepiej").

### c) sygnatury czystego flickera (DOLNE granice tego, co histereza BY naprawiła)
| sygnatura | pełne okno | 7d | siła dowodu |
|---|---|---|---|
| `pool_same` (patrz a) | 3010 (43,2%) | 43,1% | proxy |
| **prev-best wraca jako best PÓŹNIEJ** w tym zleceniu | 2896 (41,6%) | 41,0% | mocny: kandydat nie wypadł trwale |
| **revert A→B→A** (best wraca w NASTĘPNYM ticku) | 1226 (17,6%) | 17,5% | najmocniejszy: ping-pong = szum scoringu |

Trzy niezależne proxy zbiegają się: **~41–43% zdarzeń zmiany to prawdopodobnie
czysty flicker** (poprzedni najlepszy dalej dostępny, tylko scoring się
przetasował). To górny cel dla histerezy. Twarde `revert A→B→A` (17,6%) to
minimum, którego histereza z niemal pewnością pozbędzie się bez kosztu.

### d) instabilność pozycji jako osobny root-cause
`a_pos_source` (źródło pozycji holdera: gps / last_assigned_pickup /
last_picked_up_* / interp / pre_shift) zmienił się dokładnie NA ticku zmiany
best w **984 (14,1%)** przypadków (7d: 14,0%). Kolejne 1 621 zmian źródła pozycji
NIE wywołało zmiany best. Wniosek: część churnu napędza re-estymacja pozycji
(flip gps↔interp↔last_*), niezależnie od scoringu — histereza samego scoringu
tego nie ruszy; osobny lewar (stabilizacja pos_source / jego własna histereza).

### Rozbicie tabelaryczne (pełne okno)
```
6962 zdarzeń zmiany top-1
├─ pool_same    3010 (43%)  ─┐ przecina się z:
├─ pool_shrank  2537 (36%)   ├─ reappear_later 2896 (42%)  → ~flicker
├─ pool_grew    1415 (20%)   └─ revert A→B→A   1226 (18%)   → twardy flicker
└─ z tego pos_source flip na zmianie: 984 (14%) → churn pozycyjny, osobny lewar
```

---

## 3. Ograniczenia (czytać przed użyciem do werdyktu histerezy)

1. **Poziom = PROPOZYCJA (shadow), NIE commitowany przydział.** Mierzymy migotanie
   tego, co silnik BY zaproponował, nie tego, co realnie wykonano. To górna
   granica na to, ile histereza mogłaby wygładzić na WYJŚCIU propozycji;
   faktyczny churn commitów jest zapewne niższy (koordynator/executor już filtruje).
2. **Brak imiennego składu puli** → dekompozycja flicker vs feasibility jest
   PROXY, nie pomiarem. `pool_same` może kryć wymianę „jeden wypadł, jeden wszedł"
   przy stałej liczności. Dlatego podano TRZY zbieżne proxy (pool_same,
   reappear_later, revert) — spójne ~41–43% wzmacnia, ale nie dowodzi.
3. **Tick ≈ 3 min, nierówny.** Sekwencja ticków bywa dziurawa (log żywy,
   rotacja). Churn liczony po faktycznej kolejności `ts`, nie po równym rastrze.
4. **Denominator = zlecenia ≥2 ticki.** Zlecenia jedno-tickowe (9–10 szt.) nie
   mogą churnować i są wyłączone z liczb %; ujęte w `n_orders`.
5. **`delta_score` w logu = holder-vs-best, często `None`** — nie użyto do
   progowania „drobna vs istotna zmiana"; gdyby writer zaczął logować pełny score
   top-1 per tick, dałoby się rozdzielić flicker istotny od kosmetycznego.
6. **Per-doba (`--per-day`) tnie zlecenia po północy UTC** — dobra do trendu, nie
   do sum; autorytatywny baseline = pełne okno / 7d.

**Trend dzienny (pełne okno):** ≥1% waha się 75–93%, śr. zmian 2,0–4,2; dni
high-load (23.06=4,15; 26.06=4,01) mają wyraźnie wyższy churn niż spokojne
(25.06=1,97). Spójne z tezą „load > clock": więcej migotania pod obciążeniem puli.

---

## 4. Jak wpiąć jako timer (DO PRZYSZŁEGO WDROŻENIA — ZA ACK, NIC NIE INSTALOWANO)

Propozycja (pełne bloki także w stopce `proposal_churn_monitor.py`):

`/etc/systemd/system/dispatch-proposal-churn.service`
```ini
[Unit]
Description=Proposal churn baseline (read-only, reassignment shadow)
[Service]
Type=oneshot
WorkingDirectory=/root/.openclaw/workspace/scripts
ExecStart=/usr/bin/python3 -m dispatch_v2.tools.proposal_churn_monitor --window-days 7 --per-day
```

`/etc/systemd/system/dispatch-proposal-churn.timer`
```ini
[Unit]
Description=Codzienny baseline migotania propozycji
[Timer]
OnCalendar=*-*-* 05:15:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
```

Tool jest **read-only** (tylko stdout, ZERO zapisu do `dispatch_state`), więc
wynik ląduje w journalu (`journalctl -u dispatch-proposal-churn`). Gdyby chciano
serię historyczną do porównań — dopisać opcjonalny `--emit <ścieżka>` (świadomie
NIE dodane teraz, żeby trzymać gwarancję „zero zapisu").

**Użycie po histerezie:** ten sam tool, to samo okno → porównanie
`≥1%` / `≥3%` / `śr` / `flick_same%` ON vs OFF. Dowód redukcji = spadek `≥3%` i
`śr` przy zachowaniu parytetu OFF≈baseline oraz braku wzrostu spóźnień/feasibility.
Cel realistyczny = zbić ~pasmo flickera (te ~41–43%), NIE tknąć feasibility churnu
(~36% pool_shrank — to wymuszone podażą, nie szum).

---

## 5. Pliki
- **Tool:** `/root/.openclaw/workspace/scripts/dispatch_v2/tools/proposal_churn_monitor.py` (nowy, read-only)
- **Raport:** `/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-07-02/KROK0C_churn_baseline.md` (ten plik)
- **Źródło danych:** `/root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl`
- Zero zmian w silniku / flagach / systemd. Zero git commit.
