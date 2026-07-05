# 01 — MAPA RYNKU systemów restauracyjnych (PL → EU → świat)

> Wielki Audyt Integracji — FAZA 1. Data: 2026-07-05.
> Źródła: 5 równoległych researchy webowych (POS PL/CEE, POS globalne, silniki zamówień, agregatory+huby, uzupełniające). Statusy: **[ZW]** = zweryfikowane w oficjalnym źródle (URL w sekcjach/raportach) · **[DW]** = do weryfikacji (pytanie w `99-RESEARCH-BRIEF.md`) · **[HIP]** = hipoteza.
> Klasyfikacja ścieżki: **SAMI** (otwarte API/marketplace) · **PARTNER** (umowa/certyfikacja) · **MIDDLEWARE** (przez hub — wskazany) · **BRAK**.

## TL;DR — 6 wniosków, które ustawiają strategię

1. **Slot „wyślij kuriera z POS" w Polsce już istnieje i jest obsadzany** — przez Wolt Drive, Uber Direct, Glovo On-Demand, Stava i DeliGoo, wpiętych natywnie (GoPOS) lub przez huby (Restimo) i giełdy kurierów (Restaumatic). Nie tworzymy kategorii — **wchodzimy jako kolejny dostawca do istniejących kafelków wyboru kuriera.** [ZW]
2. **Dwa huby dają nieproporcjonalną dźwignię:** **Restimo** (PL: 1 integracja → GoPOS, POSbistro, Dotykačka, SOGA, S4H, LSI, nomee, ABS POS… + 5 agregatorów; >6 mln zamówień od I.2024) i **Deliverect Dispatch** (globalnie: otwarty, udokumentowany program dla dostawców logistyki — webhooki Validate/Create/Update/Cancel). [ZW]
3. **Globalne POS-y amerykańskie są w PL nieobecne** (Toast, Square, Clover, Aloha, TouchBistro — brak PL). Z globalnych liczą się: **Lightspeed K-Series, SumUp POS Pro, Oracle Simphony** (sieci/hotele). Wolumen PL siedzi na graczach lokalnych. [ZW]
4. **Kategoria B (silniki zamówień własnych) to nasz najcenniejszy segment** — restauracja ma własny kanał i realnie potrzebuje kuriera. **Restaumatic (~5000 restauracji) ma gotową „Giełdę Kurierów"** z zapowiedzią „kolejnych dostawców wkrótce" = otwarte okno wejścia. [ZW]
5. **Agregatory (Pyszne, Uber Eats, Glovo, Wolt, Bolt Food) nie wpuszczają zewnętrznej logistyki przez API** — ale wszystkie wspierają jakąś formę self-delivery → możemy być „własną flotą restauracji", a zlecenia przechwytywać z warstwy POS/middleware, którą restauracja i tak ma. [ZW]
6. **Bezpośredni konkurenci lokalni: DeliGoo** (własna flota, >700 restauracji, 24 oddziały, sieci Da Grasso/Dominium) i **Stava** (franczyza, od 2014) — obaj już wpięci w GoPOS/Restimo/Restaumatic. **Papu.io** to konkurent w warstwie software'u dispatch. [ZW]

---

## MEGA-TABELA ZBIORCZA

Legenda kolumn skróconych: **Pop.PL** = popularność w PL · **Segm.** = dominujący segment (L=pojedyncze lokale, S=sieci, P=premium) · **Cert.** = certyfikacja · **Sbx** = sandbox · **Koszt** = koszt wejścia · **Integracje delivery** = istniejący dostawcy kurierscy (dowód wykonalności ścieżki).

### Kategoria A1 — POS gastronomiczne PL/CEE

| System | Geo | Pop.PL | Segm. | API | Webhooki | Marketplace | Partner program | Cert. | Sbx | Koszt | Integracje delivery | Ścieżka | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **GoPOS** ⭐ | PL | Wysoka | L+S (QSR/pizza) | REST publ. (gopos.pl/integrators) | [DW] | program integratora | formularz + office@gopos.pl | [DW] | [DW] | [DW] | **Wolt Drive, Stava, DeliGoo natywnie** + agregatory (GoHub) | **PARTNER** (slot zajęty — wejść jako alternatywa) | [ZW] |
| **POSbistro** | PL | Śr-Wys | L | przez partners@posbistro.com | [DW] | brak publ. | e-mail partnerski | [DW] | [DW] | [DW] | DeliGoo, Stava; Wolt/Bolt via Restimo | **MIDDLEWARE (Restimo)** | [ZW] |
| **Dotykačka** | CZ/PL/SK | Średnia | L | **REST v2 publ. + docs EN** (api.dotykacka.cz) | [DW] | otwarty (klucz testowy) | samoobsługa | lekka | **TAK** | niski | Wolt natywnie (~7 dni akt.); Restimo | **SAMI / MIDDLEWARE** | [ZW] |
| **Storyous (Teya)** | CZ/PL/SK/ES/HR/HU | Średnia | L | Teya portal (developer.teya.com; zakres zamówień [DW]) | [DW] | Teya Partners | korporacyjny | [DW] | [DW] | [DW] | Foodora natywnie | **PARTNER (Teya)** | [ZW] |
| **iiko** | global (PL via partnerzy) | Nis-Śr | S+P | REST (Transport/Cloud) | [DW] | partner + huby | program partnerski | [DW] | [DW] | [DW] | Glovo/Wolt (region); huby Venus/Kwaaka | SAMI/MIDDLEWARE — niski prio | [ZW] |
| **LSI Gastro** | PL | Śr-Wys | S (pizzerie) | niepubliczne [DW] | [DW] | direct LSI | kontakt | [DW] | [DW] | [DW] | **Restimo oficjalnie** → Wolt Drive/Stava/DeliGoo | **MIDDLEWARE (Restimo)** | [ZW] |
| iPOS | PL? | ? | ? | ? | ? | ? | ? | ? | ? | ? | nie potwierdzony jako POS gastro | **WERYFIKACJA** | [HIP] |
| **SOGA** (ESC) | PL | Średnia | L (desktop) | niepubliczne [DW] | [DW] | reseller | kontakt | [DW] | [DW] | [DW] | na liście Restimo | **MIDDLEWARE (Restimo)** | [ZW] |
| **S4H** | PL | Średnia | S (hotele/catering) | modułowe [DW] | [DW] | direct | kontakt | [DW] | [DW] | [DW] | **Restimo oficjalnie** | **MIDDLEWARE (Restimo)** | [ZW] |
| **Poster** | CEE/global | Nis-Śr | L (kawiarnie) | **REST publ. + WEBHOOKI + embed app** (dev.joinposter.com) | **TAK** | otwarty katalog app (self-serve) | samoobsługa | lekka | de facto | niski | Restimo; huby regionalne | **SAMI** (najłatwiejszy technicznie; mały udział PL) | [ZW] |
| SambaPOS | TR/global | Niska | L (open-source) | ApiServer (GraphQL) | [DW] | społeczność | brak | — | [DW] | wysoki/lokal | brak natywnych PL | SAMI — niski prio | [ZW] |
| r_keeper (UCS) | global | Niska | S+P | częśc. (via huby) | [DW] | UCS + huby | [DW] | [DW] | [DW] | [DW] | Bitebell/Kwaaka → Wolt/Glovo/Bolt | MIDDLEWARE — niski prio | [ZW] |
| nomee | PL | Niska (nowy) | L | API do urządzeń; Restimo | [DW] | [DW] | kontakt | [DW] | [DW] | [DW] | Restimo | MIDDLEWARE | [ZW] |

### Kategoria A2 — POS globalne obecne w Europie

| System | Geo | Pop.PL | Segm. | API | Webhooki | Marketplace | Partner program | Cert. | Sbx | Koszt | Integracje delivery | Ścieżka | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Lightspeed K-Series** ⭐ | EU szeroko | obecny | L+S+P | REST publ. dla partnerów (api-portal.lsk.lightspeed.app) | **TAK** | Partner integrations | rejestracja + ręczna akceptacja API client (kseries.api@lightspeedhq.com) | lekka | **TAK** | nis-śr | Deliverect/HubRise | **PARTNER / MIDDLEWARE** | [ZW] |
| **SumUp POS Pro / Goodtill** (ex-Tiller) | EU (FR/UK/IE; PL [DW]) | płatności TAK; POS [DW] | L | REST publ. (tillersystems-v3.readme.io, apidoc.thegoodtill.com, developer.sumup.com) | **TAK** (orders) | częśc. | portal dev | lekka | [DW] | niski | prawd. Deliverect [DW] | **PARTNER** | [ZW/DW] |
| **Oracle MICROS Simphony** | EU szeroko | TAK (S/hotele) | S+P | REST publ.: Transaction Services **Gen2** + Config API | **TAK** | Oracle Restaurants Marketplace | **OPN → ISV → walidacja Oracle** | **ciężka** | TAK (Simphony Lab, darmowy) | wysoki (OPN [DW]) | DoorDash/UberEats/Grubhub przez Gen2 | **PARTNER (ciężki)** — tylko pod klienta sieciowego | [ZW] |
| Toast | UK/IE only | **BRAK PL** | S | REST partner (doc.toasttab.com) | TAK | directory | License Agreement + wniosek | średnia | TAK | średni | **Toast Delivery Services = Uber Direct** (od XII 2024, $6.99/zam.) | **BRAK w PL** | [ZW] |
| Square for Restaurants | UK/IE/FR/ES | **BRAK PL** | L | REST publ. (developer.squareup.com) | TAK | App Marketplace | app partner | lekka | TAK | niski | OrderOut/Deliverect | **BRAK w PL** | [ZW] |
| Clover (Fiserv) | UK/IE/DE/AT | **BRAK PL** | L | REST publ. | TAK | App Market (multi-market) | dev portal | lekka | TAK | niski | DoorDash direct; Deliverect | **BRAK w PL** (DE/AT → PARTNER) | [ZW] |
| NCR Voyix Aloha | US; EU słabo | ~BRAK | S/QSR | REST (developer.ncrvoyix.com) | [DW] | częśc. | dev portal | średnia | TAK | średni | Deliverect | MIDDLEWARE — niski prio | [ZW] |
| Revel (Shift4) | US/UK/EU | biuro PL; sprzedaż [DW] | S | „custom API" 300+ integracji | [DW] | częśc. | [DW] | [DW] | [DW] | [DW] | [DW] | **DO WERYFIKACJI** | [DW] |
| TouchBistro | UK (okrojony) | **BRAK PL** | L | ograniczone/middleware | ~NIE | częśc. | — | — | [DW] | — | Deliverect | BRAK PL / MIDDLEWARE | [ZW] |

### Kategoria B — silniki zamówień online / strony własne

| System | Geo | Pop.PL | Segm. | Własna logistyka? | API | Webhooki | Partner program | Cert. | Sbx | Koszt | Integracje delivery | Ścieżka | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Restaumatic** (+Skubacz/Apetilo) ⭐ | PL+RO/SK/HR/IT | **Wysoka (~5000)** | L+S | zarządzanie własnymi kierowcami (RePOS Delivery) + **Giełda Kurierów** | POS-integracje; publ. docs [DW] | prawd. [DW] | brak publ. self-serve; **BD: +48 732 081 111** | [DW] | [DW] | kontakt BD | **Wolt Drive, Uber Direct, Glovo On-Demand, Stava, DeliGoo** + „kolejni wkrótce" | **PARTNER #1** (wejść na Giełdę) | [ZW] |
| **UpMenu** ⭐ | PL+global | Wys-Śr | L | TAK (apka kierowcy dla floty restauracji) | **REST API (key+secret)**; docs URL [DW] | [DW] | per-partner z zespołem | [DW] | [DW] | kontakt | **Glovo On-Demand, Stava, Uber Direct, Shipday** | **PARTNER #2** | [ZW] |
| **Papu.io** ⚠ | PL | Śr-Wys (nisza dowozów) | L | **TAK — pełny dispatch+GPS+apka kierowcy = KONKURENT** | API token (wsysanie zamówień) | [DW] | onboarding przez zespół (help@papu.io) | [DW] | [DW] | kontakt | wsysa: Uber Eats/Pyszne/Glovo/Wolt/Choice/UpMenu | **KONKURENT** / (PARTNER integr-in) | [ZW] |
| Zamów.online | PL | Średnia | L | handoff „do kuriera"; platformy via Restimo | via Restimo [DW] | [DW] | kontakt | [DW] | [DW] | [DW] | Restimo → kurierzy | **PARTNER / MIDDLEWARE (Restimo)** | [ZW] |
| RestApp | US/UK | Znikoma | L | REST (POS) | [DW] | [DW] | [DW] | — | — | — | strefy własne | **BRAK** (PL) | [ZW] |
| GloriaFood (Oracle) | global | Niska | L | webhook + API key per restauracja | TAK | **program KOŃCZONY** | — | — | ⛔ | Tookan, GetSwift, FreeOrdy, Shipday, DelivApp | **POMINĄĆ — wygaszane 30.04.2027** | [ZW] |
| Flipdish | IE/UK | Niska | L+S | API/partner (Deliverect connector) | [DW] | przez Account Managera (opłata) | [DW] | [DW] | kontakt | Uber Direct, Relay | PARTNER — niski prio | [ZW] |
| **WooCommerce** | global | (platforma) | L | REST + hooks | **TAK** (order hooks) | **wordpress.org — self-publish, free** | review lekki | lokalny | ~0 (czas dev) | **Shipday** (wzorzec), Orderable | **SAMI — najniższa bariera; zbudować wtyczkę** | [ZW] |
| Shopify | global | Niska (gastro) | L | REST/GraphQL + webhooks | TAK | App Store (review, OAuth) | review Shopify | dev store | opłata+czas | Shipday, Uber Direct, QuickShipper (Wolt/Glovo/Uber) | PARTNER — niski prio | [ZW] |
| PrestaShop | EU | Niska (gastro) | L | Webservice + hooks | TAK | Addons (walidacja, płatne) | certyfikacja | lokalny | średni | XpressDelivery, myOwnDeliveries | PARTNER/MIDDLEWARE — niski prio | [ZW] |

### Kategoria C — agregatory / marketplace

| System | Pop.PL | Self-delivery? | Transmisja zamówień na zewnątrz | Program dla zewn. logistyki | Ścieżka | Status |
|---|---|---|---|---|---|---|
| **Pyszne.pl (JET)** | ⭐⭐⭐ dominant (~6000 rest.) | **TAK** (prowizja self ~13-15% vs ~30% [DW]) | przez Deliverect/Restimo/POS; developers.just-eat.com | **BRAK** | **MIDDLEWARE + „flota restauracji" przy self-delivery** | [ZW] |
| **Uber Eats** | ⭐⭐ | TAK | Order Integration API (developer.uber.com) | BRAK — **Uber Direct = konkurent** | MIDDLEWARE + self-delivery | [ZW] |
| **Glovo** | ⭐⭐⭐ | SŁABO [DW 100% własna flota?] | Deliverect/Restimo/POS | BRAK (własna flota) | MIDDLEWARE | [ZW] |
| **Wolt** | ⭐⭐ rośnie | TAK | Deliverect/POS | BRAK — **Wolt Drive = konkurent** (już w Deliverect Dispatch) | MIDDLEWARE | [ZW] |
| **Bolt Food** | ⭐⭐ **aktywny w PL 2026** (wyszedł tylko z HR/RPA/NG) | TAK („Dostawa i Odbiór własny") | Restimo/Deliverect | BRAK | MIDDLEWARE + self-delivery; monitorować ryzyko wyjścia | [ZW] |

### Kategoria D — middleware / huby / iPaaS

| System | Obecność PL | API/docs | Program dla delivery-providera | Cert. | Sbx | Koszt | Istniejący delivery providerzy | Ścieżka | Status |
|---|---|---|---|---|---|---|---|---|---|
| **Restimo** ⭐⭐ | **PL champion** (>6 mln zam. od I.2024; POS: GoPOS/POSbistro/Dotykačka/ID POS/ChoiceQR/SOGA/S4H/LSI/nomee/ABS; 5 agregatorów) | docs.restimo.com | **routuje do „courier service"; kategoria dla nowego dostawcy [DW]**; /become-a-partner; hello@restimo.com | [DW] | [DW] | [DW] | **Wolt Drive, Uber Direct, Glovo On-Demand, Stuart, Stava, DeliGoo** | **MIDDLEWARE-KLUCZ: wejść jako kurier — priorytet #1 PL** | [ZW/DW] |
| **Deliverect** ⭐⭐ | rośnie (global 52 kraje) | **publiczne, pełne** (developers.deliverect.com) | **TAK — Dispatch integration** (webhooki Validate/Create/Update/Cancel + availability) | TAK (test przez Delivery Manager App; kryteria [DW]) | **TAK** | [DW] | **Wolt Drive + 40+ last-mile** | **PARTNER-OPEN: priorytet #2 (globalna dźwignia)** | [ZW] |
| HubRise | [DW] niska? | publiczne | delivery apps (Hop; Stuart? [DW]) | [DW] | [DW] | [DW] | Hop Delivery i in. | PARTNER-OPEN — opcja ekspansji EU | [ZW/DW] |
| UrbanPiper | [HIP] niska | jest | dispatch „Prime" | [DW] | [DW] | [DW] | DSP globalne | niski prio PL | [HIP] |
| Otter (CloudKitchens) | [HIP] brak | jest | via Shipday/Nash | [DW] | [DW] | [DW] | Shipday, Nash | niski prio PL | [HIP] |
| Chowly / Checkmate / Omnivore (Olo) / Ordering.co | znikoma | jest | US-centryczne | — | — | — | US DSP | pomijalne dla PL | [HIP] |
| Zapier | global | TAK | n/d | — | TAK | freemium | n/d | fallback glue | [ZW] |
| **Make** | **EU/PL-friendly** | TAK | n/d | — | TAK | tanie | n/d | **fallback glue dla egzotycznych systemów** | [ZW] |

### Kategoria E — dispatch/delivery-as-a-service (konkurenci; pełny benchmark = FAZA 2)

| Gracz | Obecność PL | Model | Gdzie już wpięty | Zagrożenie |
|---|---|---|---|---|
| **Wolt Drive** | TAK | white-label kurier via API (merchant.wolt.com/pl/pol/wolt-drive) | Restaumatic, GoPOS, Restimo, **Deliverect Dispatch** | WYSOKIE |
| **Uber Direct** | TAK (przez Uber Eats) | courier-as-a-service, 90+ integracji | Restaumatic, UpMenu, Flipdish, Shopify | WYSOKIE |
| **Glovo On-Demand** | TAK | flota Glovo jako usługa | Restaumatic, UpMenu, Restimo | ŚREDNIE |
| **Stuart** | TAK (PL wśród rynków) | courier API (api-docs.stuart.com) | Restimo; agregatory | ŚREDNIE |
| **DeliGoo** | **TAK — PL-native** (>700 rest., 24 oddziały; sieci Da Grasso/Dominium/Falla) | własna flota, płatność za dostawę, **REST API (apidoc.deligoo.pl)**, onboarding ≤3 dni | GoPOS, Restimo, Restaumatic | **WYSOKIE lokalnie** |
| **Stava** | TAK (franczyza od 2014; 23-62 oddziały [DW]) | franczyza floty | GoPOS, UpMenu, Restimo, Restaumatic | WYSOKIE lokalnie |
| Shipday | US-centryczny | meta-dispatch jako plugin (Woo/Shopify) | Otter, GloriaFood | wzorzec architektury do skopiowania w PL |
| DoorDash Drive | brak PL | white-label US | Toast (do XII 2024), Clover | niskie PL (benchmark F2) |

### Kategoria F — uzupełniające (przeglądowo)

| Podkategoria | Gracze | Znaczenie dla flow zamawiania kuriera |
|---|---|---|
| KDS | Fresh KDS (global); wbudowane: GoPOS, Papu.io, OrderingStack, ABS POS, Dotykačka | **ŚREDNIE-rosnące jako TRIGGER** („jedzenie gotowe → wołaj kuriera"), ale zawsze wewnątrz POS — nie osobny cel integracji; sygnał „ready" = lepszy moment wywołania kuriera |
| Rezerwacje | MojStolik.pl, TheFork, OpenTable | **NISKIE** — domena dine-in, rozłączna z dostawą; pomijamy |
| Lojalność/CRM | UpMenu, GoPOS GoCRM, LoyaltyPlant, BonusQR, Paneo | **NISKIE** — warstwa nad zamówieniem, nie punkt integracji; pomijamy |
| Inne kanały (skan braków) | Otbot (Messenger-bot PL), sieci pizzy (Domino's własny „AnyWare" — zamknięty; Da Grasso/Dominium → DeliGoo), fintech (SumUp/PayEye — tylko płatności), Deliverky [DW], XDELIVER [DW], Trasado [HIP: nie istnieje] | social/fintech łapane downstream przez POS; sieci pizzy = zamknięte lub zakontraktowane — nie na pilota |

---

## WNIOSKI PER KATEGORIA

**A1 (POS PL/CEE):** Wolumen PL siedzi tu. GoPOS = największy i już z natywnym slotem kurierów (Wolt Drive/Stava/DeliGoo) — wejście wymaga partnerstwa „jako kolejna opcja". Połowa kategorii (POSbistro, LSI, SOGA, S4H, nomee, ABS) osiągalna JEDNĄ integracją przez Restimo. Dotykačka i Poster mają najbardziej otwarte API (samoobsługa + sandbox) — dobre na szybkie „SAMI", ale mniejszy udział rynkowy.

**A2 (POS globalne):** Dla działalności PL prawie bez znaczenia (amerykańcy gracze nieobecni). Wyjątki: Lightspeed (partner lekki, sandbox), SumUp POS (weryfikacja obecności PL), Oracle Simphony (tylko jeśli pojawi się klient sieciowy/hotelowy — ciężka certyfikacja OPN). Reszta = przez Deliverect przy ekspansji zagranicznej.

**B (silniki zamówień):** Najcenniejszy segment. Priorytet: Restaumatic (Giełda Kurierów, ~5000 restauracji, jawnie otwarte okno „kolejni dostawcy wkrótce"), UpMenu (REST API + praktyka wpinania dostawców typu Stava). Papu.io traktować jako konkurenta software'owego. GloriaFood pominąć (wygaszane). WooCommerce = najtańsza własna wtyczka (wzorzec Shipday); Shopify/Presta niski priorytet (mała baza gastro PL).

**C (agregatory):** Bez bezpośredniego API dla nas. Strategia dwutorowa: (1) restauracja na self-delivery → my jesteśmy jej flotą (niewidoczni dla agregatora, bez niczyjej zgody), (2) zlecenia z agregatorów przechwytujemy z POS/middleware. Uwaga prawna: Wolt/Uber sprzedają własne floty — konflikt interesów przy głębszej współpracy.

**D (huby):** Największa dźwignia na restaurację włożonej pracy. **Restimo = priorytet #1** (PL-native, spina nasze POS-y docelowe i 5 agregatorów; kategoria kurierska istnieje — warunki wejścia do potwierdzenia). **Deliverect Dispatch = priorytet #2** (jedyny w pełni udokumentowany, otwarty program dla dostawców logistyki: webhooki Validate/Create/Update/Cancel — to gotowa specyfikacja naszego przyszłego API konektora). Make jako tani fallback dla długiego ogona.

**E (konkurencja):** Nisza obsadzana szybko (Wolt Drive już w Deliverect; DeliGoo/Stava w GoPOS/Restimo/Restaumatic). Nasza przewaga możliwa: lokalne pokrycie (Białystok i miasta poza zasięgiem konkurentów), cena, jakość SLA, oraz szybkość wejścia do Restimo/Restaumatic zanim zrobi to Wolt Drive. Twarde stawki konkurencji = research handlowy (brief).

**F:** poza KDS (trigger wewnątrz POS) — pomijalne dla flow.

## Rekomendowana kolejność ataku (wejście do FAZY 4/5)

1. **Restimo** — kontakt partnerski (hub PL; jedna integracja → większość POS PL + agregatory).
2. **Restaumatic** — kontakt BD (Giełda Kurierów; ~5000 restauracji; okno „kolejni dostawcy").
3. **Deliverect Dispatch** — formularz become-a-partner (globalny standard; gotowa specyfikacja webhooków).
4. **UpMenu** — partner (REST API, praktyka wpinania kurierów).
5. **GoPOS** — partner (największy POS PL; wygrać slot obok Wolt Drive/Stava/DeliGoo).
6. **Dotykačka / Poster** — „SAMI" (otwarte API + sandbox) — niski koszt, średni zasięg.
7. **WooCommerce** — własna wtyczka (wzorzec Shipday) — najtańszy kanał własny.
8. Lightspeed / HubRise / Make — druga linia; Oracle Simphony — tylko pod konkretnego klienta sieciowego.
