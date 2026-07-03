# ADR-008: Rdzeń silnika NIE jest fizycznie przenoszony (nawigacja przez docs)

Status: obowiązuje (decyzja audytu 2026-07-03, zaakceptowana w `docs/audyt/10-PLAN.md` §2a)

## Kontekst
Cel audytu = orientacja nowej sesji w minutach zamiast godzinach. „Idealna" odpowiedź strukturalna to przeprowadzka modułów silnika do czystszego drzewa (np. `schedule_utils`→pakiet, `monitoring`→`observability`). Blokery: (a) systemd startuje `-m dispatch_v2.X` — zmiana ścieżek modułów = deploy silnika; (b) konsola (`nadajesz_clone/panel`) i apka (`courier_api`) IMPORTUJĄ `dispatch_v2` jako bibliotekę cross-repo (3 repa) — przeprowadzka łamie wszystkie naraz. Zysk nawigacyjny da się osiągnąć dokumentacją za ~0 ryzyka.

## Decyzja
W tym audycie NIE przenosimy fizycznie modułów rdzenia silnika. Warstwa nawigacyjna (`docs/ARCHITECTURE.md` + `docs/CODEMAP.md` + `docs/decisions/`) daje orientację za zerowe ryzyko; przeprowadzka to deploy najwyższego ryzyka (łamie 3 repa) przy zerowym zysku nawigacyjnym ponad CODEMAP. Ewentualna pakietyzacja rdzenia (R-20 `schedule_utils`, R-21 `monitoring`→`observability`) = OSOBNY sprint pod pełnym Przykazaniem #0, nie porządki.

## Konsekwencje
- Wolno: dodawać warstwę nawigacyjną (docs), archiwizować nie-kod, sprzątać `.bak`/dane/artefakty (Faza 3 audytu).
- Nie wolno: przenosić modułów importowanych przez systemd/konsolę/apkę bez pełnego #0 (zmiana ścieżki = deploy silnika + złamanie importów cross-repo w 3 repach).
- Nawigacja odbywa się przez CODEMAP/ARCHITECTURE, NIE przez reorganizację drzewa katalogów.
- Warstwy pozostają LOGICZNE (kanon 10 warstw odpowiada kodowi) mimo że fizyczny układ plików nie jest „idealny" — to świadome odchylenie, nie dług do spłaty na siłę.

## Źródła
`docs/audyt/10-PLAN.md` §2a (architektura docelowa, świadome odchylenie) + R-20/R-21 + WD-16; `docs/audyt/01-ZALEZNOSCI.md` §3 (import cross-repo konsola/apka jako biblioteka) + §4 (`schedule_utils` hub poza pakietem).
