# Zależności, upgrady i EOL

## Werdykt

Reprodukowalność jest niepełna. Dispatcher ma wydzielony venv i częściowy manifest,
ale nie ma jednego lock/constraints obejmującego runtime, testy, ML, panel oraz API.
Audyt nie znalazł bezpiecznej podstawy do rekomendowania upgrade’u konkretnego
pakietu w tej samej turze.

## Minimalny program

1. Wygenerować SBOM per proces/venv, bez sekretów i danych runtime.
2. Porównać SBOM z jawnie utrzymywanym constraints/lock.
3. Uruchamiać `pip check`, import smoke, licencje i skan CVE w CI.
4. Upgrade’ować jedną rodzinę naraz w izolowanym worktree.
5. Dla OR-Tools/NumPy/LightGBM wykonać paired replay i benchmark deterministyczny.
6. Dla FastAPI/Pydantic/uvicorn przejść kontrakty API i auth fault cases.
7. Zapisać politykę EOL Pythona i systemu operacyjnego, z kwartalnym review.

## Czego nie twierdzimy

Brak skanu nie oznacza braku CVE. Sama nowsza wersja nie oznacza bezpiecznego
upgrade’u. Raport nie wykorzystuje niezweryfikowanych list podatności ani
automatycznego `pip install -U`.
