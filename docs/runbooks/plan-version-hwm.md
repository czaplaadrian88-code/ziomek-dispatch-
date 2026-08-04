# Runbook: licznik wersji planów (`.version_hwm`) — diagnoza i naprawa

**Dla kogo:** operator/dyżurny. Nie musisz znać Pythona ani wnętrza silnika.
**Czego dotyczy:** pliku `courier_plans.json.version_hwm` w katalogu
`/root/.openclaw/workspace/dispatch_state/`.
**Narzędzie:** `dispatch_v2/tools/repair_version_hwm.py`.

> **Zanim cokolwiek zrobisz:** krok 1 (diagnoza) jest w 100 % bezpieczny i wolno
> go uruchomić zawsze, także w peaku. Nic nie zapisuje. Krok naprawy wymaga
> osobnego potwierdzenia i nigdy nie uruchamia się przypadkiem.

---

## 1. O co w ogóle chodzi (jedno zdanie na warstwę)

Każdy plan kuriera ma numer wersji. Żeby ten sam numer nigdy nie został wydany
dwa razy (bo wtedy dwa różne plany wyglądałyby jak ten sam i jeden zapis
skasowałby po cichu drugi), silnik trzyma obok pliku planów **mały plik-licznik**
`courier_plans.json.version_hwm`. W tym pliku są dwie rzeczy:

- `last_issued` — najwyższy numer, jaki kiedykolwiek wydano;
- `covers_all_issued` — **dowód**, że ten licznik naprawdę obejmuje wszystko, co
  wydano (`true`) albo że dowód wygasł (`false`).

Dowód wygasa **normalnie i celowo** za każdym razem, gdy plany są zapisywane przy
**wyłączonej** fladze `ENABLE_PLAN_CORRUPT_RAISE` — bo wtedy numery wydaje stara
ścieżka, której licznik nie widzi. To nie jest awaria.

Gdy silnik nie ma ważnego dowodu i **jednocześnie** nie może przeczytać pliku
planów, **celowo się zatrzymuje** zamiast zgadywać. To jest zachowanie poprawne:
lepiej chwilowo bez planów niż dwa plany z tym samym numerem.

---

## 2. Jak POZNAĆ, że to ten problem

To jest najtrudniejsza część, bo **awaria jest cicha**. Konsumenci planów łapią
błędy (`except Exception`), więc nic nie krzyczy — flota po prostu jedzie dalej
bez świeżych planów. Nie licz na alert.

**Objawy pośrednie (to zobaczysz najpierw):**

- plany kurierów przestają się odświeżać, konsola pokazuje stare trasy;
- propozycje/rekalkulacje „nic nie robią", choć zamówienia przychodzą;
- brak nowych wpisów planów, choć silnik żyje i nie ma restartów.

**Potwierdzenie w logu** — szukaj tych trzech nazw:

```bash
grep -E "PLAN_VERSION_RECOVERY_BLOCKED|PLAN_VERSION_CONTINUITY_ALREADY_VOID|version HWM" \
  /root/.openclaw/workspace/scripts/logs/*.log | tail -20
```

| Co znajdziesz | Co to znaczy |
|---|---|
| `PLAN_VERSION_RECOVERY_BLOCKED` | **To jest ten problem.** Silnik zatrzymał odtwarzanie planów, bo nie ma dowodu pokrycia numerów. Idź do kroku 3. |
| `PLAN_VERSION_CONTINUITY_ALREADY_VOID` | Sidecar jest uszkodzony w treści, ale **zapisy planów działają** (to celowe zabezpieczenie). Idź do kroku 3 — trzeba naprawić, ale nie jest to pożar. |
| `PLAN_VERSION_CONTINUITY_INVALIDATED` | **Normalne.** Pierwszy zapis po wyłączeniu flagi. Nic nie rób. |
| `PLAN_VERSION_HWM_RECONCILED` | **Normalne.** Silnik sam odbudował dowód ze zdrowego pliku planów. Nic nie rób. |

**Nie masz pewności? Uruchom diagnozę — jest bezpieczna.**

---

## 3. Diagnoza (zawsze bezpieczna, także w peaku)

```bash
/root/.openclaw/venvs/dispatch/bin/python \
  /root/.openclaw/workspace/scripts/dispatch_v2/tools/repair_version_hwm.py
```

Dodaj `--json`, jeśli chcesz surowy wynik do wklejenia w zgłoszeniu.

Narzędzie **nie bierze blokady pliku i nie zapisuje ani jednego bajtu**, więc
nie może niczego zepsuć ani nikogo zablokować.

Kod wyjścia: `0` = zdrowo, `1` = wymaga Twojego działania.

### Co zobaczysz i co z tym zrobić

| `sidecar` | `WERDYKT` | Co to znaczy | Co robisz |
|---|---|---|---|
| `MISSING` + main bez nowej numeracji | `OK` | Flaga nigdy nie była włączona. **Stan produkcji na dziś.** | nic |
| `PROVEN` | `OK` | Dowód ważny i pokrywa plany. | nic |
| `UNPROVEN` / `LEGACY_NO_MARKER` + main czytelny | `SELF_HEALING` | Dowód wygasł po oknie z wyłączoną flagą, ale plik planów jest zdrowy — **silnik odbuduje dowód sam** przy pierwszym odczycie po włączeniu flagi. | nic, obserwuj |
| `UNPROVEN` / `LEGACY_NO_MARKER` + main **nieczytelny** | `BLOKADA` | To jest `PLAN_VERSION_RECOVERY_BLOCKED`. | krok 5 (**najpierw odtwórz plik planów**) |
| `CONTENT_REJECTED` | `BLOKADA` | Bajty licznika są uszkodzone (śmieci, zły format). Zapisy planów przy wyłączonej fladze **działają**; blokada dotyczy odczytu/odtwarzania. | krok 4 |
| `IO_UNAVAILABLE` | `BLOKADA` | Licznika **nie da się odczytać** (uprawnienia/dysk). To blokuje **także zapisy planów przy wyłączonej fladze**. | krok 6 |
| `MISSING` + main **z** nową numeracją | `BLOKADA` | Licznik zniknął, a numery z nowej puli już są w użyciu. | krok 4 |

Wiersz `Naprawa jest możliwa tym narzędziem:` na końcu wydruku pojawia się
**tylko wtedy**, gdy naprawa naprawdę przejdzie. Jeśli go nie ma — nie próbuj na
siłę, przejdź do kroku 5 albo 6.

---

## 4. Naprawa (uszkodzony licznik, zdrowy plik planów)

Warunek konieczny: diagnoza pokazała `BLOKADA` **i** `main: READABLE`.

```bash
HWM_REPAIR_ACK=REPAIR-HWM-CONFIRMED \
/root/.openclaw/venvs/dispatch/bin/python \
  /root/.openclaw/workspace/scripts/dispatch_v2/tools/repair_version_hwm.py --repair
```

Co się dzieje pod spodem — warto wiedzieć, bo o to zapytają:

1. Narzędzie bierze **wyłączną blokadę planów**, żeby nie ścigać się z silnikiem.
2. Czyta stan **jeszcze raz pod blokadą** (nie działa na obrazie sprzed sekundy).
3. Kopiuje stare bajty licznika do `...version_hwm.bak-repair-<data>Z`.
   **Nic nie ginie.**
4. Wylicza nową wartość: **najwyższy numer widoczny w pliku planów, w jego
   poprzedniku i w starym liczniku, plus zapas 1000**. Licznik może wyłącznie
   **wzrosnąć** — narzędzie odmawia, gdyby wynik miał być niższy niż cokolwiek,
   co widać na dysku.
5. Zapisuje przez ten sam mechanizm, którego używa silnik, i **weryfikuje wynik**
   ponownym odczytem. Jeśli zapis się nie uda, przywraca bajty, które zastał.

Kod wyjścia `0` = naprawione. Potem uruchom diagnozę jeszcze raz — musi pokazać
`PROVEN` / `OK`.

**Skąd zapas 1000?** Numer bywa zarezerwowany chwilę przed zapisem planu. Awaria
dokładnie między tymi krokami zostawia numer „spalony", ale niewidoczny w żadnym
pliku. Zapas gwarantuje, że nie wydamy go po raz drugi.

---

## 5. Przypadek: nieczytelny plik planów (`main`)

**Narzędzie CELOWO odmówi naprawy** i wypisze `NAPRAWA ODMÓWIONA: main jest
nieczytelny`. To nie jest błąd narzędzia.

Powód: jedynym dowodem na to, jakie numery zostały wydane, jest sam plik planów —
bo to w nim lądują także numery wydane przy wyłączonej fladze. Bez niego każda
wartość licznika byłaby zgadywaniem, a zgadnięcie za nisko oznacza wydanie tego
samego numeru drugi raz i cichą utratę czyjegoś planu.

**Kolejność działań (nie odwracaj jej):**

1. Odtwórz `courier_plans.json`. Kandydaci, w tej kolejności:
   - `courier_plans.json.prev` (poprzednia dobra wersja, obok pliku głównego),
   - backup `restic` (`docs/deploy/ha-lite/restore_from_restic.sh`).
2. Uruchom **diagnozę** i upewnij się, że `main: READABLE`.
3. Dopiero teraz krok 4 (naprawa).

Plany są stanem **pochodnym** — zostaną przeliczone. Utrata części planów jest
odwracalna; wydanie tego samego numeru dwa razy nie jest.

---

## 6. Przypadek: licznika nie da się odczytać (`IO_UNAVAILABLE`)

To jedyny stan, w którym **zapisy planów są zablokowane także przy wyłączonej
fladze**. Jest to **świadomy koszt**, potwierdzony przez niezależną recenzję
(blind A-2 iteracja 6, sekcja 2): jeśli licznika nie można ani odczytać, ani
zapisać, to może on nadal twierdzić „dowód ważny" — a wtedy przepuszczenie
zapisów przy wyłączonej fladze cicho reaktywuje dokładnie ten błąd, przed którym
całe zabezpieczenie powstało.

**Co robisz:**

1. Sprawdź plik i katalog:
   ```bash
   ls -la /root/.openclaw/workspace/dispatch_state/courier_plans.json.version_hwm
   df -h /root/.openclaw/workspace/dispatch_state
   dmesg | tail -30
   ```
2. Napraw **dostęp** (uprawnienia, miejsce na dysku, stan urządzenia).
   Właściciel i tryb mają odpowiadać plikowi planów obok.
3. Uruchom diagnozę ponownie. Gdy licznik da się odczytać, wróć do kroku 3/4.

**Nie kasuj licznika „żeby odblokować".** Bajtów, których nikt nie przeczytał,
nie wolno wyrzucić — mogą zawierać ważny dowód. Napraw dostęp, potem czytaj.

---

## 7. Wycofanie zmiany (rollback)

Naprawa dotyka **jednego** pliku i zawsze zostawia po sobie kopię.

```bash
ls -la /root/.openclaw/workspace/dispatch_state/courier_plans.json.version_hwm.bak-repair-*
```

Żeby wrócić do stanu sprzed naprawy — zatrzymaj zapisy planów (okno bez ruchu),
skopiuj wybraną kopię z powrotem na `courier_plans.json.version_hwm`, uruchom
diagnozę i porównaj z wydrukiem sprzed naprawy.

⚠ Cofnięcie licznika **w dół** przywraca ryzyko dwukrotnego wydania numeru. Rób
to wyłącznie wtedy, gdy naprawa okazała się pomyłką i nikt w międzyczasie nie
zapisywał planów.

---

## 8. Czego to narzędzie NIE robi

- Nie włącza i nie wyłącza żadnej flagi (`ENABLE_PLAN_CORRUPT_RAISE` zostaje jak
  było).
- Nie restartuje żadnej usługi.
- Nie dotyka `courier_plans.json` ani `courier_plans.json.prev` — czyta je tylko.
- Nie naprawia się „samo" ani z crona. Zawsze potrzebny jest człowiek i jawne
  potwierdzenie `HWM_REPAIR_ACK=REPAIR-HWM-CONFIRMED`.

---

## 9. Zgłoszenie / eskalacja

Do zgłoszenia dołącz:

```bash
/root/.openclaw/venvs/dispatch/bin/python \
  /root/.openclaw/workspace/scripts/dispatch_v2/tools/repair_version_hwm.py --json
```

oraz wycinek loga z kroku 2. To wystarczy, żeby ktoś inny odtworzył Twój obraz
sytuacji bez dostępu do maszyny.

**Kontekst techniczny (dla dyżurnego inżyniera):** finding **N-4** z recenzji
A-2 (iteracje 3, 5 i 6). Zachowanie fail-closed silnika opisują
`plan_manager._rebase_recovered_versions` i `_invalidate_version_hwm_continuity`;
testy narzędzia: `tests/test_repair_version_hwm_n4.py`.
