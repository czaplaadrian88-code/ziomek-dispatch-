# Diagnoza zapaści adopcji GPS — 12/13.06.2026 (nocna sesja, STRETCH 2; czysta analiza, ZERO zmian)

> ## ⚠⚠ KOREKTA NADRZĘDNA 13.06 (Adrian) — PRZECZYTAJ ZANIM UŻYJESZ WNIOSKÓW
> **Kurierzy nie używają apki, bo ADRIAN IM TAK KAZAŁ — celowo.** Cel: Ziomek ma się
> nauczyć dodawać zlecenia BEZ GPS (kotwice czasowe/roster), bo (1) z GPS byłoby mu
> za łatwo, (2) gdy GPS padnie, system musi dalej działać. „Fale wykruszenia" opisane
> niżej to wykonanie polecenia Adriana, NIE behawioralne porzucenie ani regresja apki.
> **Rekomendacje §F typu „re-login / onboarding / egzekwowanie apki" = NIEAKTUALNE**
> do odwołania przez Adriana. Ustalenia FAKTOGRAFICZNE (kto/kiedy/które wersje,
> rewokacja 11.06 niewinna, tabela pos_source per dzień) pozostają ważne jako opis
> środowiska treningowego. Konsekwencja projektowa: bramki/mechanizmy zależne od GPS
> (G7 AUTON-01, GPS_AGE_DISCOUNT) kalibrować na wiarygodności KOTWIC, nie czekać na
> GPS. Dyrektywa utrwalona: memory/feedback_rules.md („Brak GPS = celowa polityka").

**TL;DR:** „368→13" to nie awaria techniczna, tylko **behawioralne porzucenie apki przez
kurierów, którzy dalej normalnie jeżdżą**. Od 11-12.06 GPS śle **jeden** realny kurier
(484 Andrei K, na STARYM buildzie bez X-App-Version). Każdy dropout przestał się
**logować w ogóle** (ostatnia sesja = ostatni fix), żaden nie przestał pracować.
Masowy „onboarding" 29.05 był jednodniowym wieczorem testowym bez żadnej retencji.
Dźwignia jest po stronie ops/produktu (vc49 GRAFIK + vc50 login UX), nie kodu silnika.

---

## 1. Liczby „368→13" odtworzone co do dnia (pos_source=gps w best, PROPOSE)

| Dzień | gps w best | Σ PROPOSE | komentarz |
|---|---|---|---|
| 02.06 | 95 | 217 | |
| 03.06 | 9 | 221 | |
| 04.06 | 30 | 239 | |
| 05.06 | 105 | 250 | szczyt |
| 06.06 | 37 | 144 | |
| 07.06 | 47 | 302 | ostatni dzień GPS konta 123 |
| 08.06 | 45 | 156 | flip LAST-KNOWN-POS (store) |
| **09.06** | **0** | 156 | **klif: zero fixów w gps_history tego dnia** (484 miał wolne, reszta już nie logowała) |
| 10.06 | 1 | 208 | tylko 400 (ostatni dzień jego apki) |
| 11.06 | 12 | 130 | 484 wrócił po dniach wolnych + telefon Adriana (21/413/179 — #187) |
| 12.06 | 14 | 140 | tylko 484 |

Σ 02-08.06 = 368, Σ 09-11.06 = 13 — dokładnie liczby z werdyktu FEAS-02.

## 2. Którzy kurierzy przestali słać GPS i OD KIEDY (gps_history + sessions, courier_api.db)

| cid | kurier (tier) | ostatni fix GPS | wersje apki (sesje) | dostawy PO zaniku GPS | wniosek |
|---|---|---|---|---|---|
| 509 | Dariusz M (std+) | **20.05** | None → 0.7.3 | **206 / 8 dni**, jeździ do dziś | porzucił apkę, pracuje |
| 470 | Piotr Zaw (std) | 29.05 | 0.7.4 | 112 / 6 dni, do 11.06 | jednodniowy test |
| 393 | Michał K. (std) | 29.05 | 0.9.0 | **206 / 8 dni**, do dziś | jednodniowy test |
| 441 | Sylwia L (std+) | 29.05 | 0.9.2 | 48 / 2 dni (ost. 03.06) | test; potem mało pracuje |
| 503 | Gabriel J (std) | 29.05 | 0.9.3→0.9.5 | 30 / 2 dni (ost. 07.06) | jednodniowy test |
| 508 | Michał Li (slow) | 29.05 | 0.9.1→0.9.6 | 75 / 7 dni, do dziś | jednodniowy test |
| 370 | Jakub OL (std) | **02.06** | None / 0.9.7 | 161 / 6 dni, do dziś | używał regularnie (14 dni), porzucił |
| 123 | „Bartek O." (gold) | **07.06 07:10** | 0.9.28→0.9.29 | 73 / 3 dni, do dziś | używał 05-07.06, porzucił; ⚠ patrz §4 |
| 400 | Adrian R (slow) | **10.06 16:57** | None (stary build) | 19 (tylko 10.06), potem 0 | logował się CODZIENNIE 01-03 i 10.06; po 10.06 nie pracuje albo bez apki |
| 484 | Andrei K (std) | **AKTYWNY** (1,3-1,6k fixów/dzień roboczy) | None (stary build!) | — | jedyny żywy nadawca; loguje się każdego ranka ~09:45 |
| 21/413/179 | Adrian/Mateusz O/Gabriel | 11.06 | 0.9.35/0.9.36 | — | telefon Adriana + testy vc49/vc50 (lekcja #187) |

Wzorzec wspólny: **ostatnia sesja = ostatni fix** — nikt nie ma sesji bez GPS ani GPS
bez sesji. To nie jest cicha awaria uploadu; kurierzy po prostu przestają odpalać apkę.
(Sesje revoked=1 hurtem to NORMALNY cykl logout/expiry — dzisiejsza sesja 484 też
przeszła na revoked po końcu zmiany ~19:31; rewokacja audytowa 11.06 dotyczyła 7
STARYCH sesji i nikogo nie wycięła z pracy — 484 zalogował się rano jak co dzień.)

## 3. Anatomia zapaści (chronologicznie)

1. **Baza nigdy nie była szeroka:** maj = 2-5 unikalnych nadawców/dzień przy ~25-30
   aktywnych kurierach. Stali użytkownicy: 484, 400, 370, okresowo 123, 509 (do 20.05).
2. **29.05 wieczór (18:21-22:43) = nadzorowany onboarding 6 kurierów** — wersje apki
   bumpowane na żywo 0.9.0→0.9.6 w 4 godziny (sesje sekwencyjne, po jednej na kuriera).
   **Retencja: 0/6** — nikt nie zalogował się następnego dnia. To był test urządzeń,
   nie adopcja.
3. Schodki w dół: 370 po 02.06 · 123 po 07.06 · 400 po 10.06 → od 11.06 został 484.
4. Klif w silniku 09.06 (gps w best = 0) to złożenie: dzień wolny 484 + reszta już
   nie logowała. Store LAST-KNOWN-POS (flip 08.06) nie ma czego replayować przy TTL
   25 min — „bez paliwa" z werdyktu FEAS-02 potwierdzone u źródła.
5. **Jedyny aktywny nadawca jeździ na prehistorycznym buildzie** (brak X-App-Version
   w sesjach 484 i 400) — release'y vc46-vc50 (strażnik statusu, GRAFIK, login UX)
   nigdy do niego nie dotarły; nowe wersje żyły głównie na telefonach testowych.

## 4. Side-finding (wymaga Adriana): konto cid=123 „Bartek O." (gold)

Wg raportu Bartek 2.0 Bartek O. zakończył dostawy 19.04 i nie ma go w
`grafik_full_names.json` — a konto 123 ma 264 peak-dostaw/30d w audit_log (23-36/dzień
do dziś) i słało GPS 05-07.06 z apki 0.9.28/0.9.29. Ktoś fizycznie jeździ na tym
koncie z tierem GOLD. Identyczna klasa co #187 (identyczne koordy dwóch cidów =
jeden telefon). Cross-ref: R04_ENFORCE_VERDICT.md §4 — to także jedyny gold_candidate
w R-04. **Do ustalenia kto, i czy tier gold jest zasłużony przez obecnego użytkownika.**

## 5. Co z tego wynika (rekomendacje ops — NIC nie wdrożono tej nocy)

1. **To nie jest problem kodu Ziomka** — FEAS-02 słusznie NIE-ROBIĆ; GPS_AGE_DISCOUNT
   czeka na adopcję; bramka G7 AUTON-01 (pos informed) przy obecnej adopcji przepuści
   śladową liczbę decyzji — flip AUTO bez wzrostu adopcji = autonomia na ~1 kurierze.
2. **Naturalny wabik już jest zbudowany:** vc49 GRAFIK (dyspozycje, sloty, banner T-7
   ~21.06) + vc50 naprawił dwie realne bariery loginu (lista 101→54 z dedupe, niewidoczne
   cyfry telefonu w dark mode). Sensowna sekwencja: smoke vc49/vc50 u Adriana → akcja
   re-onboardingu, NIE odwrotnie.
3. **Celuj w 6 sprawdzonych dropoutów** (509, 393, 370, 508, 470 + obecny użytkownik 123)
   — wszyscy nadal jeżdżą, wszyscy już raz apkę uruchomili; ich buildy są stare
   (0.7.x-0.9.7) → re-instalacja vc50 + login przy odprawie. 400 — wyjaśnić czy
   w ogóle jeszcze pracuje (ostatnia dostawa 10.06).
4. **Lekcja z 29.05:** onboarding „wszyscy naraz na wieczornym teście" daje retencję 0.
   Skuteczny wzorzec = 484: apka odpalana przy starcie zmiany jako element rutyny.
   Jeśli dyspozycje z vc49 staną się kanałem grafiku (a panel tego chce — grafik-source
   docelowo z panelu), login stanie się obowiązkowym elementem pracy — to jest
   strukturalny fix adopcji.
5. Metryka do briefingu (propozycja, nie wdrożona): `unikalni nadawcy GPS dziś / aktywni
   kurierzy` — 1/13 dziś vs 4-5/12 na początku czerwca; próg alarmu <3.

---
*Źródła: courier_api.db (gps_history 111,8k fixów, sessions 224), shadow_decisions
(.1+żywy, od 02.06), audit_log (dostawy), gps_positions_pwa.json, courier_tiers.json.
Metodologia read-only; żaden plik stanu/flaga nie zostały dotknięte.*

---
---

# ADDENDUM — niezależna weryfikacja (druga sesja, 12.06 ~19:45, read-only)

Druga, niezależna analiza tych samych źródeł **potwierdza wszystkie ustalenia §1-§5 powyżej**.
Poniżej tylko dane UZUPEŁNIAJĄCE, których nie ma wyżej (nic z powyższego nie zmieniono).

## AD-1. Pełna tabela pos_source zwycięzcy per dzień (WSZYSTKIE werdykty, nie tylko PROPOSE)

Liczby w §1 dotyczą werdyktów PROPOSE; poniżej rozkład dla wszystkich decyzji z best
(stąd lekko wyższe wartości — trend identyczny). Czasy UTC, dni wg `ts`.

| dzień | gps | last_assigned_pickup | last_picked_up_pickup | last_pu_delivery/interp | post_wave | pre_shift | no_gps | razem | %gps |
|---|---|---|---|---|---|---|---|---|---|
| 06-02 | 97 | 26 | 13 | 0 | 26 | 35 | 35 | 232 | 42% |
| 06-03 | 11 | 78 | 65 | 0 | 57 | 11 | 8 | 230 | 5% |
| 06-04 | 39 | 81 | 90 | 0 | 62 | 9 | 12 | 293 | 13% |
| 06-05 | 109 | 40 | 40 | 0 | 22 | 47 | 14 | 272 | 40% |
| 06-06 | 38 | 33 | 16 | 0 | 39 | 1 | 21 | 148 | 26% |
| 06-07 | 47 | 80 | 74 | 0 | 81 | 7 | 29 | 318 | 15% |
| 06-08 | 45 | 44 | 15 | 4 | 41 | 2 | 9 | 160 | 28% |
| **06-09** | **0** | 44 | 56 | 2 | 52 | 11 | 28 | 193 | **0%** |
| 06-10 | 1 | 61 | 59 | 2 | 60 | 21 | 20 | 224 | 0.4% |
| 06-11 | 20 | 68 | 86 | 3 | 44 | 24 | 8 | 253 | 8% |
| 06-12 | 22 | 31 | 101 | 0 | 64 | 13 | 21 | 252 | 9% |

`pos_from_store=true` w best: 0 przez całe okno do 10.06, potem 11.06: **11**, 12.06: **47**
— store LAST-KNOWN-POS realnie przejmuje rolę dopiero teraz (głównie pozycje 484 i kotwice pickup).

## AD-2. Rewokacja 11.06 — dokładnie KTO i potwierdzenie niewinności

Backup `courier_api.db.bak-pre-gate-guard-2026-06-11` (00:22): dokładnie 7 sesji `revoked=0`
→ id 150/155/159 (**413 Mateusz O**, w tym 2× wersja „d"/debug), 154 (**441 Sylwia L**),
157 (**503 Gabriel J**), 160 (**508 Michał Li**), 203 (**21 Adrian**). Wszystkie utworzone
29.05–02.06; GPS tych kurierów ustał już **29.05** — rewokacja skasowała tokeny martwe od ~13 dni.
Po rewokacji: 413 re-login 11.06 14:21 (0.9.35, +11 fixów), 21 re-login 15:02, 179 pierwsze
logowanie 15:08 (0.9.36). 441/503/508 nie zalogowali się ponownie — ale i tak nie słali od 29.05.
W journalu/logach brak fali 401 powiązanej z aktywnymi nadawcami.

## AD-3. Logowania PIN w czerwcu (kompletna lista — to jest cała „adopcja")

| cid | sukcesy | porażki | ostatni udany login (UTC) |
|---|---|---|---|
| 484 Andrei K | 9 | 3 (06.06) | 06-12 09:46 |
| 21 Adrian | 17 | 0 | 06-11 15:02 |
| 179 Gabriel O | 2 | 0 | 06-11 15:30 |
| 413 Mateusz O | 1 | 0 | 06-11 14:21 |
| 400 Adrian R | 4 | 0 | 06-10 09:41 |
| 123 (konto „Bartek O.") | 17 | 2 (05.06) | 06-06 19:43 |
| 370 Kuba Olchowik | 1 | 0 | 06-02 10:00 |

Nikt inny nie próbował (zero failed attempts od innych cid) → reszta floty nawet nie dotyka ekranu logowania.

## AD-4. Flota pracująca w czerwcu BEZ JEDNEJ SESJI w historii apki (cele onboardingu)

Z kandydatur shadow 01–12.06 + `courier_last_pos.json`: **75 Patryk, 207 Marek, 289 Grzegorz W,
354 Filip P, 376 Paweł SC, 387 Aleksander G, 409 Mateusz Bro, 457 Adrian Cit, 471 Łukasz Więcko,
500 Grzegorz Rogowski, 514 Tomasz Ch, 515 Szymon P, 520 Michał Rom, 526 Bartosz Kl,
529 Rafał Jankowski, 530 Bartosz Ch, 531 Piotr Kulaszewski, 532** — 18 cid, większość bieżącej floty.
Dziś (12.06) z 13 kurierów w `courier_last_pos.json` tylko **1** ma źródło `gps` (484).

## AD-5. Drobna korekta techniczna do §2

`sessions.app_version` od vc46 jest też AKTUALIZOWANE in-place z headera `X-App-Version`
przy każdym GPS batchu (`auth.update_app_version`, guarded write). Skoro sesje 484 i 400
pozostają NULL mimo żywych batchy (484: 1,3k/dzień), to ich apki **na pewno nie wysyłają
headera** → build sprzed vc46 potwierdzony nie tylko brakiem pola przy loginie.

*Addendum read-only; jedyna zmiana na dysku = ten dopisek (zgodnie z [[feedback-multisession-shared-deploy]] — treść §1-§5 równoległej sesji nietknięta).*
