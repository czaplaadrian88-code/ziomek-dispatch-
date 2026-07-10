"""Courier info helpers — PIN lookup + GPS instrukcja onboarding.

Used przez `telegram_approver.handle_message` dla komend /pin i /instrukcja_gps
(plus naturalny fallback "pin <imie>" / "instrukcja gps [imie]").

Pure functions, zero I/O side-effects poza fresh JSON read per call (mtime
nie cachowany — niski qps, Adrian/Bartek manualnie kilka razy/dzien).

Naming convention post-V3.25 (24.04.2026): canonical = dotless.
Source-of-truth: Grafik sheet. Secondary aliases w kurier_ids.json
(per Lekcja #78 — defense-in-depth dla resolve_cid ambiguity 07.05.2026).
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from dispatch_v2.identity.normalize import norm

PINY_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_piny.json"
IDS_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"

APK_URL = "https://gps.nadajesz.pl/apk/courier.apk"
PANEL_URL = "https://gps.nadajesz.pl"
ADMIN_PANEL_URL = "https://gps.nadajesz.pl/panel"


def _norm(s: str) -> str:
    # Delegates to the single canonical contract (Z-P1-05 Faza B).
    return norm(s)


def _load_json_safe(path: str) -> dict:
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_pin_to_name() -> Dict[str, str]:
    return {str(k): str(v) for k, v in _load_json_safe(PINY_PATH).items()}


def _load_name_to_cid() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for k, v in _load_json_safe(IDS_PATH).items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def resolve_courier_query(
    query: str,
) -> Tuple[Optional[str], Optional[int], Optional[str], List[str]]:
    """Resolve query → (canonical_name, cid, pin, ambiguous_matches).

    Strategia (priority order):
    1. Pure digits 3-7 chars → traktowane jako cid → reverse w name→cid map.
    2. Exact normalized match name (po `_norm`) w kurier_ids.json keys.
    3. Substring match (norm) — jesli >1 hit → zwroc ambiguous_matches list.

    Returns:
        (name, cid, pin, ambig)
        - All 4 None gdy zero match.
        - Gdy ambig non-empty → name=None (caller pokaze listę do uściślenia).
        - Gdy name found ale brak PIN-a w piny.json → pin=None (rzadki edge,
          np. nowy kurier dopisany ale PIN nie zsyncowany).
    """
    q_raw = (query or "").strip()
    if not q_raw:
        return None, None, None, []

    pin_to_name = _load_pin_to_name()
    name_to_cid = _load_name_to_cid()
    name_to_pin: Dict[str, str] = {}
    for pin, name in pin_to_name.items():
        nn = _norm(name)
        if nn:
            name_to_pin[nn] = pin

    if q_raw.isdigit() and 3 <= len(q_raw) <= 7:
        try:
            cid_int = int(q_raw)
        except ValueError:
            cid_int = None
        if cid_int is not None:
            for name_key, cid in name_to_cid.items():
                if cid == cid_int:
                    canonical = name_key
                    pin = name_to_pin.get(_norm(canonical))
                    return canonical, cid_int, pin, []

    qn = _norm(q_raw)
    for name_key in name_to_cid.keys():
        if _norm(name_key) == qn:
            cid = name_to_cid[name_key]
            pin = name_to_pin.get(_norm(name_key))
            return name_key, cid, pin, []

    substrs: List[str] = []
    for name_key in name_to_cid.keys():
        nn = _norm(name_key)
        if qn in nn or nn.startswith(qn):
            substrs.append(name_key)

    seen_cids = set()
    dedup: List[str] = []
    for n in substrs:
        cid = name_to_cid.get(n)
        if cid in seen_cids:
            continue
        seen_cids.add(cid)
        dedup.append(n)

    if len(dedup) == 1:
        name_key = dedup[0]
        cid = name_to_cid[name_key]
        pin = name_to_pin.get(_norm(name_key))
        return name_key, cid, pin, []

    if len(dedup) > 1:
        return None, None, None, dedup

    return None, None, None, []


def format_pin_response(name: str, cid: int, pin: Optional[str]) -> str:
    if pin is None:
        return (
            f"⚠️ {name} (cid={cid}) — brak PIN-a w kurier_piny.json.\n"
            f"Sprawdź sync: kurier_ids.json zawiera, ale piny nie."
        )
    return (
        f"🔑 {name} (cid={cid})\n"
        f"PIN: {pin}\n\n"
        f"Aplikacja: {APK_URL}\n"
        f"Pełna instrukcja: /instrukcja_gps {name.split()[0]}"
    )


def format_ambiguous_response(query: str, matches: List[str]) -> str:
    listing = "\n".join(f"  • {n}" for n in matches[:10])
    return (
        f"❓ '{query}' pasuje do kilku kurierów — uściślij:\n{listing}\n\n"
        f"Możesz też podać cid (np. /pin 393)."
    )


def format_not_found_response(query: str) -> str:
    return (
        f"❌ Nie znaleziono kuriera '{query}'.\n"
        f"Sprawdź pisownię (canonical = dotless, np. 'Bartek O' nie 'Bartek O.')\n"
        f"lub podaj cid (np. /pin 393)."
    )


def format_gps_instruction(
    name: Optional[str] = None, pin: Optional[str] = None
) -> str:
    """Pełna instrukcja onboardingu PL — 5 kroków + 3 ROM-y + test.

    Z imieniem+PIN-em → spersonalizowany header (do bezpośredniego forwardu
    kurierowi). Bez → template ogólny (Adrian/Bartek wstawia ręcznie).
    """
    if name and pin:
        header = (
            f"🚀 INSTRUKCJA APLIKACJI KURIERA — {name}\n"
            f"Twój PIN: {pin}\n"
        )
    else:
        header = (
            "🚀 INSTRUKCJA APLIKACJI KURIERA — NadajeSz GPS\n"
            "Twój PIN: [WSTAW PIN]\n"
        )

    return (
        f"{header}"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📥 KROK 1 — POBIERZ APLIKACJĘ\n"
        "Otwórz na telefonie w przeglądarce:\n"
        f"{APK_URL}\n\n"
        "Jeśli telefon zablokuje pobieranie:\n"
        "Ustawienia → Aplikacje → Chrome (lub przeglądarka której używasz) →\n"
        "'Instaluj nieznane aplikacje' → WŁĄCZ.\n"
        "Wróć do pobierania, kliknij plik courier.apk → Zainstaluj.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔐 KROK 2 — ZALOGUJ SIĘ\n"
        "Otwórz aplikację 'Nadajesz Kurier'.\n"
        "Wpisz swój 4-cyfrowy PIN → 'Zaloguj'.\n"
        "Sesja trwa 30 dni (90 min bezczynności = automatyczne wylogowanie).\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ KROK 3 — UPRAWNIENIA (KRYTYCZNE!)\n"
        "Telefon zapyta o 3 rzeczy — odpowiedz:\n"
        "▪ Lokalizacja → 'ZEZWALAJ ZAWSZE'\n"
        "  (NIE 'tylko podczas używania' — wtedy GPS nie działa w tle!)\n"
        "▪ Powiadomienia → 'Zezwalaj'\n"
        "▪ Działanie w tle / autostart → 'Zezwalaj'\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔋 KROK 4 — OSZCZĘDZANIE BATERII (NAJWAŻNIEJSZE)\n"
        "Bez tego telefon zabija aplikację po 20-30 min\n"
        "i Adrian przestaje widzieć Twoją lokalizację.\n\n"
        "📱 XIAOMI / REDMI / POCO (MIUI / HyperOS):\n"
        "1. Ustawienia → Aplikacje → Zarządzaj aplikacjami →\n"
        "   znajdź 'Nadajesz Kurier'\n"
        "2. 'Oszczędzanie baterii' → wybierz 'BEZ OGRANICZEŃ'\n"
        "3. 'Autostart' (Autouruchamianie) → WŁĄCZ\n"
        "4. 'Wyświetlanie wyskakujących okienek' → WŁĄCZ\n"
        "5. Otwórz listę ostatnich aplikacji (kwadrat dolny przycisk),\n"
        "   znajdź 'Nadajesz Kurier', przeciągnij KARTĘ W DÓŁ →\n"
        "   pojawi się KŁÓDKA — kliknij ją (zablokowane w pamięci).\n"
        "6. Ustawienia → Bateria → Tryb wydajności → 'ZWYKŁY'\n"
        "   (NIE 'oszczędzanie energii').\n\n"
        "📱 REALME / OPPO / ONEPLUS (ColorOS):\n"
        "1. Ustawienia → Bateria → 'Optymalizacja zużycia baterii' →\n"
        "   'Nadajesz Kurier' → 'NIE OPTYMALIZUJ'\n"
        "2. Ustawienia → Aplikacje → 'Nadajesz Kurier' →\n"
        "   'Auto-uruchamianie' → WŁĄCZ\n"
        "3. Aplikacje → 'Nadajesz Kurier' → 'Zezwól na działanie w tle' →\n"
        "   ZAZNACZ wszystkie trzy: Auto-launch / Secondary launch /\n"
        "   Run in background\n"
        "4. 'Tryb gier' / 'Ultra-energooszczędny' → WYŁĄCZ globalnie\n\n"
        "📱 HUAWEI / HONOR (EMUI / MagicOS, bez Google Play):\n"
        "1. Ustawienia → Aplikacje → 'Nadajesz Kurier' → Bateria →\n"
        "   'Uruchamianie aplikacji' → WYŁĄCZ przełącznik 'Zarządzaj automatycznie'\n"
        "2. Włącz wszystkie 3 ręcznie:\n"
        "   • Auto-uruchamianie\n"
        "   • Uruchamianie pomocnicze\n"
        "   • Działanie w tle\n"
        "3. Ustawienia → Bateria → 'Optymalizacja zużycia' →\n"
        "   'Nadajesz Kurier' → 'NIE OPTYMALIZUJ'\n"
        "4. Bateria → 'Więcej ustawień baterii' →\n"
        "   'Pozostań połączony, gdy ekran wyłączony' → WŁĄCZ\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛰 KROK 5 — TEST GPS\n"
        "1. Włącz GPS w telefonie (Lokalizacja ON, tryb 'Wysoka dokładność').\n"
        "2. Otwórz aplikację — powinieneś zobaczyć status 'Online'.\n"
        "3. Wyjdź z domu, przejedź 200-300 m.\n"
        "4. Adrian sprawdza panel — powinien widzieć Twoją kropkę na mapie.\n\n"
        "⚠️ JEŚLI ADRIAN NIE WIDZI:\n"
        "• Sprawdź czy GPS w telefonie WŁĄCZONY (rozwijane menu góra ekranu)\n"
        "• Sprawdź czy mobilny internet / WiFi działa\n"
        "• Aplikacja ma pokazywać 'Online' — jeśli 'Offline', zaloguj ponownie\n"
        "• Zabij aplikację z listy ostatnich i otwórz znowu\n"
        "• Zrestartuj telefon\n\n"
        "Pytania → DM Adrian. Powodzenia! 💪"
    )
