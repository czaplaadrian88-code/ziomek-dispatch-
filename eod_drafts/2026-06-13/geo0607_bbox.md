# GEO-06/07: dwa bboxy Białegostoku — werdykt

**Data:** 2026-06-13 | **Sesja:** auton/geo-districts

## Zarzut audytu
Dwa niespójne bboxy Białegostoku (różne definicje granic w różnych plikach) → ujednolicić do JEDNEJ kanonicznej.

## Inwentaryzacja — wszystkie definicje bbox
| Stała | Wartość (lat / lon) | Plik | Zadanie |
|---|---|---|---|
| `BIALYSTOK_BBOX_LAT/LON` | (52.6–53.7) / (22.3–24.1) | `common.py:286` | **METROPOLIA ±55 km** — filtr trucizny OSRM/GPS (`coords_in_bialystok_bbox`); odrzuca (0,0)/cross-country, przyjmuje Wasilków/Zabłudów/Łapy do routingu |
| `GEOCODE_BBOX_LAT_MIN/MAX` + `LON_MIN/MAX` | (52.85–53.35) / (22.85–23.45) | `common.py:737` | **OBSZAR OBSŁUGI +~28 km** — filtr akceptacji geokodu (`geocoding.py:_in_service_bbox`); odrzuca geokody poza realnym zasięgiem |

Inne „bbox-podobne" stałe to NIE bboxy granic miasta, lecz pojedyncze punkty-centra: `BIALYSTOK_CENTER`/`_BBOX_CENTER`/`RYNEK_KOSCUSZKI`/`FIRMOWE_KONTO_FALLBACK_COORDS` (placeholdery dla nieprawidłowych współrzędnych). Nie wchodzą w zakres.

## Analiza: to NIE jest błąd, to dwie warstwy
Dwa bboxy mają **rozłączne zadania** i **udokumentowaną intencję w komentarzach**:
- Metropolia MUSI być szeroka (routing do podmiejskich zleceń).
- Geocode-acceptance MUSI być węższy (geokod poza obszarem obsługi = prawie zawsze błąd).

„Ujednolicenie do jednej" byłoby **regresją**: zwężenie metropolii → OSRM odrzucałby legalne podmiejskie zlecenia; rozszerzenie geocode-bbox → osłabienie filtra trucizny geokodu. Celowa różnica.

## Realny inwariant (którego brakowało jako test)
Jedyna sensowna spójność: **GEOCODE_BBOX ⊂ BIALYSTOK_BBOX** (ścisłe podzbiór). Inaczej geocoding mógłby zaakceptować punkt, który OSRM zaraz odrzuci jako truciznę → wewnętrzna sprzeczność.

**Zweryfikowane 2026-06-13:**
- Inwariant podzbioru **ZACHODZI** (52.6≤52.85, 53.35≤53.7, 22.3≤22.85, 23.45≤24.1). ✅
- Wszystkie realne miejscowości dispatchu mieszczą się w OBU bboxach (żaden realny adres nie jest błędnie wycinany):

| Miejscowość | w metropolii | w geocode-bbox |
|---|---|---|
| Wasilków, Choroszcz, Zabłudów, Łapy, Kleosin, Supraśl, Ignatki, Centrum BI | ✅ | ✅ |

## WERDYKT
- **NIE unifikować** — dwa bboxy są poprawne i celowe.
- **Kanoniczność osiągnięta przez dokumentację + test inwariantu**, nie przez scalanie:
  1. Dodano blok komentarza „GEO-06/07" przy `BIALYSTOK_BBOX_LAT` cross-referujący oba bboxy i nazywający inwariant kanoniczny.
  2. Nowy test `tests/test_geo_bbox_consistency.py` pilnuje: (a) GEOCODE_BBOX ⊂ BIALYSTOK_BBOX, (b) realne miejscowości w obu, (c) (0,0) i cross-country odrzucone przez metropolię.

## ⚠ Uczciwość
Współrzędne miejscowości testowych (Wasilków/Łapy/…) to publiczne centra geograficzne — wystarczające do sprawdzenia inwariantu i pokrycia, ale nie obejmują skrajnych adresów na obrzeżach (np. dalekie przysiółki). Inwariant podzbioru gwarantuje jednak, że cokolwiek przejdzie geocode-bbox, przejdzie też metropolię — więc skrajne adresy nie wywołają niespójności OSRM↔geocoding.
