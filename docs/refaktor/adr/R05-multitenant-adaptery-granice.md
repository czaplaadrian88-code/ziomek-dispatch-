# ADR-R05: Multi-tenant przez adaptery ingest/wykonania; prawda przypisań → silnik

Status: proponowany-KIERUNKOWY (aktywacja dopiero po wariancie B + osobnej, jawnej zgodzie Adriana — zmiana kontraktu odpowiedzialności)

## Kontekst
Cel 12-mies. (Faza 0): ~400 zam/d + multi-tenant + integracje wg `docs/integracje/` (IR v1, model Wolt Drive). Dziś: pętla przypisania przechodzi przez ZEWNĘTRZNY panel gastro (HTML poll → POST → HTML poll) i to gastro jest źródłem prawdy przypisań (raw/01a); bbox Białystok = stała globalna HARD (`common.py:844`); state-files bez wymiaru tenanta. 400/d w jednym mieście przejdzie na obecnej architekturze; multi-tenant i API dla restauracji/POS — nie.

## Decyzja (kierunek)
1. **Ingest = adaptery o wspólnym kontrakcie NEW_ORDER** (idempotentne event_id już istnieje): gastro-HTML (dzisiejszy), IR v1 API, paczki. Silnik nie wie, skąd zlecenie.
2. **TenantContext w WorldState:** bbox/strefy/progi/cenniki per tenant z configu; pola tenanta w state/logach ADDITIVE (kontrakt JSONL nietykalny — stare pola bez zmian).
3. **Przypisanie = transakcja silnika:** claim-ledger ON (po dowodzie ETAP 5) + rekord przypisania w silniku; gastro staje się adapterem WYKONANIA (POST jak dziś), a nie źródłem prawdy. Przejście przez okres SHADOW: silnik prowadzi rekord równolegle i mierzy rozjazdy z gastro zanim cokolwiek się przełączy.
4. **Bez brokera/CQRS/nowej bazy:** 400/d ≈ 1 zlecenie/2 min w peaku — istniejący `events.db` + pliki z fcntl mają zapas rzędu wielkości; formalizujemy tylko kontrakt eventów i idempotencję na granicach.

## Konsekwencje
- Jedyna droga do multi-tenant bez rewrite'u; wykonalna TYLKO na czystym rdzeniu (B) — bez niego to przybudówki na splątaniu.
- Zmiana odpowiedzialności operacyjnej (prawda przypisań) = decyzja biznesowa Adriana, nie techniczna; do tego czasu gastro pozostaje prawdą.
- Zależności zewnętrzne (partnerzy IR, pilot 2. tenanta) wyznaczają tempo — nie planować dat w tym programie.

## Źródła
`02-diagnoza.md` D8; `docs/integracje/00-08+99`; Faza 0 odpowiedzi Adriana (multi-tenant, integracje „wg tego co na serwerze"); `raw/01a-e2e.md` (granica gastro).
