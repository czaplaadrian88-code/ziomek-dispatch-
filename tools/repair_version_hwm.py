#!/usr/bin/env python3
"""repair_version_hwm — diagnostyka i naprawa sidecara `.version_hwm` (A-2, N-4).

Powód istnienia (blindy A-2 iter3/iter5/iter6, finding N-4): gdy dowód ciągłości
w `courier_plans.json.version_hwm` przestaje być czytelny albo przestaje pokrywać
wydane tokeny, silnik świadomie zatrzymuje się fail-closed
(`PLAN_VERSION_RECOVERY_BLOCKED` + `PlanVersionStateError`). To jest zachowanie
POPRAWNE — ale do tej pory nie było żadnej udokumentowanej drogi wyjścia:
w `tools/` nie istniało narzędzie naprawy, a konsumenci planów łapią wyjątki, więc
flota po cichu jedzie bez planów zamiast krzyczeć. Pierwszy incydent oznaczałby
ręczne grzebanie w sidecarze w peaku.

Kontrakt tego narzędzia:

  * `--diagnose` (DOMYŚLNE) jest w 100 % READ-ONLY. Nie bierze locka, nie tworzy
    lockfile'a, nie zapisuje ani jednego bajtu. Wolno je uruchomić na produkcji
    w dowolnym momencie, także w peaku.
  * `--repair` wymaga DWÓCH świadomych aktów: przełącznika `--repair` ORAZ
    zmiennej środowiskowej `HWM_REPAIR_ACK=REPAIR-HWM-CONFIRMED`. Bez obu
    narzędzie odmawia (exit 3). Nic nie dzieje się „samo".
  * Naprawa jest FAIL-CLOSED wobec nieczytelnego maina. Dowodem, że nowe HWM
    pokrywa wszystkie wydane tokeny, jest wyłącznie CZYTELNY `courier_plans.json`
    — bo to w nim lądują tokeny wydane także w oknie z wyłączoną flagą. Bez tego
    dowodu narzędzie NIE zgaduje i wypisuje instrukcję manualną.
  * HWM jest wyłącznie PODNOSZONE. Wartość docelowa nigdy nie schodzi poniżej
    żadnej wersji zaobserwowanej w mainie, w `.prev` ani poniżej dającego się
    sparsować `last_issued`. Twardy assert przed zapisem; złamanie = odmowa.
  * Zapis idzie przez KANONICZNEGO writera silnika
    (`plan_manager._write_version_hwm`) pod wyłącznym lockiem planów. Narzędzie
    NIE ma własnego serializatora, własnego schematu ani własnej ścieżki
    atomowego zapisu — inaczej byłby to drugi writer tej samej prawdy.

Uruchomienie:

    # diagnoza (bezpieczna zawsze)
    /root/.openclaw/venvs/dispatch/bin/python tools/repair_version_hwm.py

    # naprawa (dopiero po diagnozie i po przeczytaniu runbooka)
    HWM_REPAIR_ACK=REPAIR-HWM-CONFIRMED \\
      /root/.openclaw/venvs/dispatch/bin/python tools/repair_version_hwm.py --repair

Runbook operatorski: `docs/runbooks/plan-version-hwm.md`.

Kody wyjścia:
    0 — stan zdrowy (diagnoza) albo naprawa wykonana i zweryfikowana
    1 — stan wymaga działania operatora (diagnoza)
    2 — błąd użycia
    3 — naprawa ODMÓWIONA (brak ACK, nieczytelny main, złamany warunek monotoniczności)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

def _bootstrap_pkgroot() -> None:
    """Znajdź katalog zawierający pakiet `dispatch_v2` i dołóż go do ścieżki.

    Operator uruchamia to narzędzie w incydencie, z dowolnego katalogu i często
    z worktree, a nie z kanonicznego `/root/.openclaw/workspace/scripts`. Stały
    hardkod „trzy katalogi w górę" trafia tylko w układ kanoniczny, więc idziemy
    w górę aż do pierwszego katalogu, w którym `dispatch_v2` naprawdę jest.
    """
    for parent in Path(os.path.abspath(__file__)).parents:
        if (parent / "dispatch_v2" / "__init__.py").exists():
            sys.path.insert(0, str(parent))
            return


try:
    from dispatch_v2 import plan_manager as pm
    from dispatch_v2 import state_persistence as sp
except ImportError:  # uruchomienie spoza pkgroota
    _bootstrap_pkgroot()
    from dispatch_v2 import plan_manager as pm
    from dispatch_v2 import state_persistence as sp


ACK_ENV = "HWM_REPAIR_ACK"
ACK_VALUE = "REPAIR-HWM-CONFIRMED"

# Zapas ponad najwyższą ZAOBSERWOWANĄ wersję. Token bywa zarezerwowany w HWM
# tuż przed zapisem maina; awaria między tymi dwoma krokami zostawia token
# spalony, ale niewidoczny w żadnym pliku. Zapas sprawia, że odtworzone HWM jest
# zawsze >= tego, co policzyłby sam silnik w `_validate_or_reconcile_main_epoch`,
# więc naprawa może być tylko BARDZIEJ konserwatywna niż silnik, nigdy mniej.
DEFAULT_MARGIN = 1000

# Klasyfikacja sidecara
SIDECAR_MISSING = "MISSING"
SIDECAR_PROVEN = "PROVEN"
SIDECAR_UNPROVEN = "UNPROVEN"
SIDECAR_LEGACY_NO_MARKER = "LEGACY_NO_MARKER"
SIDECAR_CONTENT_REJECTED = "CONTENT_REJECTED"
SIDECAR_IO_UNAVAILABLE = "IO_UNAVAILABLE"

# Klasyfikacja maina / poprzednika
FILE_READABLE = "READABLE"
FILE_MISSING = "MISSING"
FILE_CONTENT_REJECTED = "CONTENT_REJECTED"
FILE_IO_UNAVAILABLE = "IO_UNAVAILABLE"

# Werdykt dla operatora
VERDICT_OK = "OK"
VERDICT_SELF_HEALING = "SELF_HEALING"
VERDICT_BLOCKED = "BLOCKED"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_sidecar() -> Tuple[str, Optional[int], Optional[str]]:
    """Zwróć (status, last_issued_lub_None, opis_błędu_lub_None).

    Rozróżnienie treść-vs-I/O jest DOKŁADNIE tym samym rozróżnieniem, którego
    używa `plan_manager._invalidate_version_hwm_continuity` (N-3): bajty
    przeczytane i odrzucone to `ValueError`/`PlanVersionStateError`,
    niedostępność to `OSError`.
    """
    try:
        state = pm._read_version_hwm_state()
    except pm.PlanVersionStateError as exc:
        return SIDECAR_CONTENT_REJECTED, None, str(exc)
    except ValueError as exc:  # JSONDecodeError, JsonObjectShapeError, unicode
        return SIDECAR_CONTENT_REJECTED, None, f"{type(exc).__name__}: {exc}"
    except OSError as exc:
        return SIDECAR_IO_UNAVAILABLE, None, f"{type(exc).__name__}: {exc}"
    if state is None:
        return SIDECAR_MISSING, None, None
    if state.covers_all_issued is True:
        return SIDECAR_PROVEN, state.last_issued, None
    if state.covers_all_issued is None:
        return SIDECAR_LEGACY_NO_MARKER, state.last_issued, None
    return SIDECAR_UNPROVEN, state.last_issued, None


def classify_plan_file(path: Path) -> Tuple[str, Optional[int], Optional[str]]:
    """Zwróć (status, max_plan_version_lub_None, opis_błędu_lub_None)."""
    try:
        plans = sp.read_json_object(path).data
    except FileNotFoundError:
        return FILE_MISSING, None, None
    except ValueError as exc:
        return FILE_CONTENT_REJECTED, None, f"{type(exc).__name__}: {exc}"
    except OSError as exc:
        return FILE_IO_UNAVAILABLE, None, f"{type(exc).__name__}: {exc}"
    try:
        observed = pm._max_plan_version(plans)
    except pm.PlanVersionStateError as exc:
        return FILE_CONTENT_REJECTED, None, str(exc)
    return FILE_READABLE, observed, None


def _verdict(sidecar_status: str, main_status: str, observed: Optional[int],
             last_issued: Optional[int]) -> Tuple[str, str]:
    """Czy silnik jest teraz zablokowany i dlaczego — językiem operatora."""
    if sidecar_status in (SIDECAR_CONTENT_REJECTED, SIDECAR_IO_UNAVAILABLE):
        if sidecar_status == SIDECAR_CONTENT_REJECTED:
            return VERDICT_BLOCKED, (
                "Sidecar jest uszkodzony w treści. KAŻDY odczyt planów go "
                "odrzuca, więc silnik nie wystartuje z planami dopóki plik nie "
                "zostanie odtworzony. Zapisy planów przy WYŁĄCZONEJ fladze "
                "DZIAŁAJĄ normalnie (to jest fix N-3)."
            )
        return VERDICT_BLOCKED, (
            "Sidecara nie da się odczytać z powodu uprawnień lub błędu dysku. "
            "To blokuje TAKŻE zapisy planów przy wyłączonej fladze — świadomy "
            "koszt opisany w runbooku. Najpierw napraw dostęp do pliku."
        )
    if sidecar_status == SIDECAR_MISSING:
        if observed is not None and observed >= pm._VERSION_EPOCH_FLOOR:
            return VERDICT_BLOCKED, (
                "Brak sidecara, a main zawiera już wersje z nowej numeracji. "
                "Silnik nie ma czym udowodnić pokrycia tokenów."
            )
        return VERDICT_OK, (
            "Brak sidecara i brak wersji z nowej numeracji w mainie. To jest "
            "normalny stan systemu, na którym flaga nigdy nie była włączona."
        )
    if sidecar_status == SIDECAR_PROVEN:
        if observed is not None and last_issued is not None and observed > last_issued:
            return VERDICT_BLOCKED, (
                "Dowód ciągłości istnieje, ale main zawiera wersję wyższą niż "
                "HWM. Licznik nie pokrywa tego, co już wydano."
            )
        return VERDICT_OK, "Dowód ciągłości jest ważny i pokrywa main."
    # UNPROVEN / LEGACY_NO_MARKER
    if main_status == FILE_READABLE:
        return VERDICT_SELF_HEALING, (
            "Dowód ciągłości jest unieważniony (normalne po okresie z wyłączoną "
            "flagą), ale main jest czytelny — silnik odbuduje dowód sam przy "
            "pierwszym odczycie po włączeniu flagi. Naprawa NIE jest potrzebna."
        )
    return VERDICT_BLOCKED, (
        "Dowód ciągłości jest unieważniony, a main jest nieczytelny lub go brak. "
        "Silnik nie ma z czego odbudować dowodu i celowo zatrzymuje recovery "
        "(PLAN_VERSION_RECOVERY_BLOCKED) zamiast wydać token drugi raz."
    )


def diagnose() -> Dict[str, Any]:
    """Pełny obraz stanu. Czysty odczyt — zero zapisów, zero locka."""
    main_path = Path(pm.PLANS_FILE)
    prev_path = sp.previous_path(main_path)
    hwm_path = pm.version_hwm_path()

    sidecar_status, last_issued, sidecar_error = classify_sidecar()
    main_status, main_observed, main_error = classify_plan_file(main_path)
    prev_status, prev_observed, prev_error = classify_plan_file(prev_path)

    verdict, explanation = _verdict(
        sidecar_status, main_status, main_observed, last_issued
    )
    return {
        "checked_at": _now_iso(),
        "paths": {
            "main": str(main_path),
            "previous": str(prev_path),
            "sidecar": str(hwm_path),
        },
        "sidecar": {
            "status": sidecar_status,
            "last_issued": last_issued,
            "error": sidecar_error,
            "exists": hwm_path.exists(),
        },
        "main": {
            "status": main_status,
            "max_plan_version": main_observed,
            "error": main_error,
        },
        "previous": {
            "status": prev_status,
            "max_plan_version": prev_observed,
            "error": prev_error,
        },
        "epoch_floor": pm._VERSION_EPOCH_FLOOR,
        "verdict": verdict,
        "explanation": explanation,
        # Musi się zgadzać co do joty z warunkami wejścia `repair()` — inaczej
        # narzędzie obiecuje operatorowi naprawę, której zaraz odmówi.
        "repair_possible": (
            verdict == VERDICT_BLOCKED
            and main_status == FILE_READABLE
            and sidecar_status != SIDECAR_IO_UNAVAILABLE
        ),
    }


def _safe_target(report: Dict[str, Any], margin: int) -> int:
    """Najniższa wartość, która na pewno pokrywa wszystko, co widać na dysku."""
    candidates = [pm._VERSION_EPOCH_FLOOR]
    for section in ("main", "previous"):
        observed = report[section]["max_plan_version"]
        if observed is not None:
            candidates.append(observed)
    if report["sidecar"]["last_issued"] is not None:
        candidates.append(report["sidecar"]["last_issued"])
    return max(candidates) + margin


class RepairRefused(RuntimeError):
    """Naprawa świadomie odmówiona — nigdy nie zgaduj brakującego dowodu."""


def repair(*, margin: int = DEFAULT_MARGIN) -> Dict[str, Any]:
    """Odtwórz sidecar z dowodu, który realnie jest na dysku.

    Kolejność jest istotna: bierzemy wyłączny lock planów, dopiero POD nim
    czytamy stan po raz drugi (żeby nie działać na obrazie sprzed sekundy),
    robimy kopię zapasową starych bajtów i zapisujemy przez writera silnika.
    """
    if os.environ.get(ACK_ENV) != ACK_VALUE:
        raise RepairRefused(
            f"brak jawnego potwierdzenia: ustaw {ACK_ENV}={ACK_VALUE}"
        )

    with pm._locked(exclusive=True):
        report = diagnose()
        if report["main"]["status"] != FILE_READABLE:
            raise RepairRefused(
                "main jest nieczytelny ({}), a tylko czytelny main dowodzi, "
                "które tokeny zostały wydane. Naprawa wymaga najpierw "
                "przywrócenia courier_plans.json z kopii — patrz runbook, "
                "sekcja 'Nieczytelny main'.".format(report["main"]["status"])
            )
        if report["sidecar"]["status"] == SIDECAR_IO_UNAVAILABLE:
            raise RepairRefused(
                "sidecara nie da się odczytać z powodu uprawnień lub dysku "
                "({}); napraw dostęp do pliku ZANIM odtworzysz jego treść, "
                "inaczej nadpiszesz dowód, którego nikt nie widział.".format(
                    report["sidecar"]["error"]
                )
            )

        target = _safe_target(report, margin)

        # Twarda bariera monotoniczności: HWM wolno wyłącznie PODNIEŚĆ.
        for section in ("main", "previous"):
            observed = report[section]["max_plan_version"]
            if observed is not None and target < observed:
                raise RepairRefused(
                    f"wyliczone HWM {target} jest poniżej wersji widocznej w "
                    f"{section} ({observed}) — odmowa obniżenia licznika"
                )
        previous_hwm = report["sidecar"]["last_issued"]
        if previous_hwm is not None and target < previous_hwm:
            raise RepairRefused(
                f"wyliczone HWM {target} jest poniżej istniejącego "
                f"last_issued ({previous_hwm}) — odmowa obniżenia licznika"
            )

        hwm_path = pm.version_hwm_path()
        backup = None
        if hwm_path.exists():
            backup = hwm_path.with_name(
                hwm_path.name + ".bak-repair-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            )
            shutil.copy2(hwm_path, backup)

        # Kanoniczny writer celowo ODMAWIA nadpisania pliku, którego nie umie
        # sparsować (`state_persistence.atomic_write_json` czyta i waliduje stan
        # zastany, a domyślna polityka to `raise`). To dlatego uszkodzony
        # sidecar NIE leczy się sam i musi tu być usunięty — dopiero wtedy
        # writer widzi legalny bootstrap. Kasujemy wyłącznie bajty, których
        # kopię właśnie zrobiliśmy, i wyłącznie gdy są nieparsowalne.
        if report["sidecar"]["status"] == SIDECAR_CONTENT_REJECTED:
            hwm_path.unlink()

        try:
            # Jedyny kanoniczny writer sidecara — narzędzie nie ma własnego.
            pm._write_version_hwm(target, covers_all_issued=True)
        except Exception:
            # Nieudany zapis nie może zostawić operatora z mniejszą liczbą
            # informacji, niż zastał: przywracamy dokładnie te bajty, które
            # tu były, i podnosimy błąd dalej.
            if backup is not None and not hwm_path.exists():
                shutil.copy2(backup, hwm_path)
            raise
        pm._invalidate_plan_cache()

        after = diagnose()

    if after["sidecar"]["status"] != SIDECAR_PROVEN:
        raise RepairRefused(
            "weryfikacja po zapisie nie potwierdziła dowodu ciągłości: "
            f"{after['sidecar']['status']}"
        )
    if after["sidecar"]["last_issued"] != target:
        raise RepairRefused(
            "weryfikacja po zapisie zwróciła inną wartość niż zapisana"
        )
    return {
        "repaired_at": _now_iso(),
        "previous_last_issued": previous_hwm,
        "new_last_issued": target,
        "margin": margin,
        "backup": None if backup is None else str(backup),
        "before": report,
        "after": after,
    }


def _print_human(report: Dict[str, Any]) -> None:
    marks = {
        VERDICT_OK: "OK",
        VERDICT_SELF_HEALING: "UWAGA",
        VERDICT_BLOCKED: "BLOKADA",
    }
    print(f"HWM sidecar — diagnoza ({report['checked_at']})")
    print(f"  main     : {report['paths']['main']}")
    print(f"  sidecar  : {report['paths']['sidecar']}")
    print()
    print(f"  sidecar  : {report['sidecar']['status']}"
          + (f"  last_issued={report['sidecar']['last_issued']}"
             if report["sidecar"]["last_issued"] is not None else "")
          + (f"  ({report['sidecar']['error']})"
             if report["sidecar"]["error"] else ""))
    for section, label in (("main", "main     "), ("previous", "poprzednik")):
        row = report[section]
        print(f"  {label}: {row['status']}"
              + (f"  max_plan_version={row['max_plan_version']}"
                 if row["max_plan_version"] is not None else "")
              + (f"  ({row['error']})" if row["error"] else ""))
    print()
    print(f"  WERDYKT  : {marks[report['verdict']]} — {report['verdict']}")
    print(f"  {report['explanation']}")
    if report["repair_possible"]:
        print()
        print("  Naprawa jest możliwa tym narzędziem:")
        print(f"    {ACK_ENV}={ACK_VALUE} \\")
        print(f"      {sys.executable} {sys.argv[0]} --repair")
    elif report["verdict"] == VERDICT_BLOCKED:
        print()
        print("  Naprawa tym narzędziem NIE jest możliwa — patrz "
              "docs/runbooks/plan-version-hwm.md")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="repair_version_hwm",
        description="Diagnostyka i naprawa sidecara .version_hwm (A-2 / N-4).",
    )
    parser.add_argument(
        "--diagnose", action="store_true",
        help="tylko odczyt (domyślne, gdy nie podano --repair)",
    )
    parser.add_argument(
        "--repair", action="store_true",
        help=f"odtwórz sidecar; wymaga {ACK_ENV}={ACK_VALUE}",
    )
    parser.add_argument(
        "--margin", type=int, default=DEFAULT_MARGIN,
        help=f"zapas ponad najwyższą zaobserwowaną wersję (domyślnie {DEFAULT_MARGIN})",
    )
    parser.add_argument("--json", action="store_true", help="wypisz surowy JSON")
    args = parser.parse_args(argv)

    if args.repair and args.diagnose:
        parser.error("--diagnose i --repair wykluczają się")
    if args.margin < 0:
        parser.error("--margin nie może być ujemny")

    if not args.repair:
        report = diagnose()
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            _print_human(report)
        return 0 if report["verdict"] == VERDICT_OK else 1

    try:
        result = repair(margin=args.margin)
    except RepairRefused as exc:
        print(f"NAPRAWA ODMÓWIONA: {exc}", file=sys.stderr)
        print("Runbook: docs/runbooks/plan-version-hwm.md", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("NAPRAWA WYKONANA")
        print(f"  poprzednie last_issued : {result['previous_last_issued']}")
        print(f"  nowe last_issued       : {result['new_last_issued']}"
              f" (zapas {result['margin']})")
        print(f"  kopia starego sidecara : {result['backup']}")
        print(f"  dowód ciągłości        : {result['after']['sidecar']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
