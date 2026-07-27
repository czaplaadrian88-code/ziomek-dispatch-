"""Exact imports for legacy source that physically neighbours dispatch_v2.

Direct entrypoints must not reopen ``sys.path`` from an environment-selected
scripts root after the package bootstrap.  A small amount of legacy code still
lives beside the package in the owner-managed scripts tree.  This loader binds
only the exact physical sibling of the already-attested ``dispatch_v2``
package, without adding its parent to the general import search path.

Every component of ``relative_path`` is opened with ``O_NOFOLLOW`` relative to
a descriptor of the attested parent directory, and the module source executes
from bytes read off the final descriptor: an intermediate symlink cannot
redirect the walk.  ``O_NOFOLLOW`` rejects only a *symlink* substitution, so a
plain directory swapped under the same name after load would otherwise be
walked; the loader therefore RETAINS an open descriptor to the attested
package directory captured at load, and resolves every descendant relative to
that descriptor — never re-resolving the package directory by name.  An inode
number can be recycled by ``rename``+``rmdir``+``mkdir``, but a retained
descriptor cannot be reattached to a different directory, so a post-load path
swap cannot redirect descendant bytes.  (Replacing a *deeper* sub-directory
*inside* the retained directory needs write access to the attested,
owner-managed tree — the same access that replaces the package root or
``dispatch_v2.common`` — outside this loader's threat model; the real
consumers import only direct children.)  Repeated requests for a name this
loader already bound to the
same physical source return the existing module; any other preloaded module
under that name is a hard conflict — also when the optional physical source
is absent, so ``required=False`` never bypasses the name contract.

Descendants of a package loaded here resolve through the same descriptor
walk: a meta-path finder owns descendant *resolution* — every
``<package>.<child>`` import that reaches ``sys.meta_path`` — so a pathname
swapped after the package load cannot hand descendant execution to ordinary
text-path resolution, and a missing descendant fails closed instead of
falling back to ``PathFinder``.  This ownership is bounded by CPython import
semantics: ``sys.modules`` is consulted before ``sys.meta_path``, so a
descendant name already bound in ``sys.modules`` is returned from that cache
before any finder runs.  That is not specific to this loader — the same
in-process injection would shadow ``dispatch_v2.common`` itself — and is
outside this loader's threat model, which is a poisoned filesystem / import
search path, not arbitrary in-process ``sys.modules`` mutation.  The *root*
reuse decision remains owned in every case by ``_adopt_or_reject_preloaded``.
"""
from __future__ import annotations

import errno
import gc
import importlib.util
import os
import stat
import sys
import threading
from pathlib import Path
from types import ModuleType

import dispatch_v2

# JEDEN lock serializuje kompozytowe operacje na współdzielonym stanie loadera
# (mapy tras/descendant/owner + uchwyty fd), tak by check-then-act nie mógł się
# przeplatać między wątkami: (1) adopt→publikacja jest atomowym CAS pod nazwę
# (dwa równoległe legalne loady tej samej nazwy nie zapiszą konkurencyjnych
# tożsamości); (2) odczyt `handle.fd`+`dup` jest atomowy wobec `close`
# w retirement (recykling numeru fd nie przekieruje otwarcia potomka);
# (3) reset/rollback trasy nie przeplata się z rozwiązywaniem potomka.
# Lock NIE jest trzymany podczas `exec` modułu (kod użytkownika), więc nie
# wprowadza nowego porządku blokad z lockami importu CPythona — rdzenie
# `_retire_package_route`/`_purge_owned_module_subtree` są lock-free i to
# CALLERZY spoza sekcji krytycznej biorą lock (brak zagnieżdżenia/deadlocku).
#
# FINALIZER POD LOCKIEM — ROOT-FIX = FAIL-FAST RE-ENTRANCJI (Candidate57/Sol C56
# #1, poprawka Candidate56/Sol C55 #1). Każda ALOKACJA obiektu śledzonego przez
# GC pod lockiem (np. `tuple(sys.modules)`, budowa list w purge) może przekroczyć
# próg cyklicznego GC. Cykliczny GC biegnie SYNCHRONICZNIE w wątku, który
# alokował — czyli w POSIADACZU locka — i odpala DOWOLNE finalizery (`__del__` =
# kod użytkownika). Groźny skutek jest JEDEN: taki finalizer mógłby re-entrować
# loader i na nie-reentrantnym `threading.Lock` ZADEADLOCKOWAĆ (ten sam wątek
# czeka na lock, który sam trzyma).
#
# `gc.disable()` NIE jest tu gwarancją: flaga GC jest PROCESOWA i globalna, więc
# RÓWNOLEGŁY (nawet benign) `gc.enable()` z innego wątku znosi pauzę i cykliczny
# GC znów biegnie pod lockiem (zreprodukowane: 50 obcych finalizerów w wątku-
# posiadaczu przy współbieżnym `gc.enable()`). CPython NIE ma per-wątkowego
# wyłączania GC, więc pauzy nie da się utwardzić na poziomie samego locka.
#
# GWARANCJA (sound, NIEZALEŻNA od globalnego stanu GC): lock jest nie-reentrantny
# i FAIL-FAST — ponowne `acquire()` przez wątek, który JUŻ go trzyma (jedyne
# źródło: finalizer/audit-hook/sygnał odpalony w środku naszej sekcji
# krytycznej), PODNOSI `RuntimeError` zamiast deadlockować. Detekcja re-entrancji
# opiera się na `RLock._is_owned()` — własności ustawianej C-ATOMOWO WEWNĄTRZ
# `acquire` (a nie osobną instrukcją Pythona na zmiennej `_owner`, jak wcześniej).
# Poprzednia wersja miała DWA sub-instrukcyjne okna wyścigu (Candidate59/Sol C58
# #1): między `self._lock.acquire()` a `self._owner=get_ident()` ORAZ między
# `self._owner=None` a `self._lock.release()`; finalizer/hook odpalony w tych
# oknach NIE widział jeszcze/już własności i mijał fail-fast → deadlock na
# nie-reentrantnym `threading.Lock`. `RLock` ustawia własność atomowo w C w chwili
# nabycia i czyści atomowo przy zwolnieniu, więc `_is_owned()` odzwierciedla
# PRAWDZIWĄ własność w DOWOLNYM punkcie, w którym trzymamy lock — bez okna. RLock
# sam DOPUSZCZAŁby re-entrancję (licznik), więc egzekwujemy nie-reentrancję
# RĘCZNIE: `acquire` posiadacza PODNOSI RuntimeError ZANIM dotknie RLocka, więc
# licznik nigdy nie rośnie powyżej 1 i krytyczna sekcja pozostaje nie-reentrantna.
# Operacja intruza (finalizera) kończy się czysto wyjątkiem (połkniętym przez
# maszynerię GC), a pierwotna sekcja krytyczna wznawia się nietknięta. To domyka
# realny tryb awarii (deadlock), którego `gc.disable()` był tylko kruchym proxy.
#
# WARSTWY OBRONY (uzupełniające, NIE gwarancje same w sobie):
#  * `gc.disable()` pod lockiem = BEST-EFFORT: w benign (bez współbieżnego
#    `gc.enable()`) redukuje częstość odpalania cyklicznych finalizerów pod
#    lockiem; przywracany na wyjściu (tylko cofamy WŁASNE wyłączenie);
#  * deferred-deref (Sol C54 #1) pokrywa finalizery wyzwalane REFCOUNTEM;
#  * pełne wyeliminowanie odpalania obcych finalizerów w wątku-posiadaczu
#    wymagałoby usunięcia WSZYSTKICH alokacji śledzonych przez GC z sekcji
#    krytycznych (approach a) — to warstwa purge/rollback (koordynacja osobno),
#    poza zakresem samego locka; niepotrzebne dla POPRAWNOŚCI dzięki fail-fast.
class _GcPausingLock:
    """Nie-reentrantny lock: FAIL-FAST na re-entrancji + best-effort pauza GC.

    Prymitywem jest ``threading.RLock``, ale kontrakt pozostaje NIE-REENTRANTNY:
    re-entrancja posiadacza podnosi ``RuntimeError`` (nie zwiększa licznika
    RLocka).  ``RLock._is_owned()`` daje C-ATOMOWĄ detekcję własności bez
    sub-instrukcyjnego okna, którego nie dało się domknąć osobną zmienną
    ``_owner`` (Candidate59/Sol C58 #1).
    """

    __slots__ = ("_lock", "_gc_was_enabled")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._gc_was_enabled = False

    def acquire(self, *args, **kwargs) -> bool:
        # C-ATOMOWA detekcja re-entrancji: `_is_owned()` jest prawdziwe
        # NATYCHMIAST po nabyciu RLocka (własność ustawiana w C wewnątrz acquire),
        # więc finalizer/hook odpalony w DOWOLNYM punkcie, w którym trzymamy lock,
        # zobaczy własność — bez okna z poprzedniej wersji na zmiennej `_owner`.
        # Podnosimy PRZED dotknięciem RLocka, więc licznik reentrancji nigdy nie
        # rośnie i krytyczna sekcja pozostaje nie-reentrantna mimo prymitywu RLock.
        if self._lock._is_owned():
            raise RuntimeError(
                "_PHYSICAL_LOAD_LOCK re-entered by the holding thread "
                "(finalizer/hook under the loader critical section)"
            )
        acquired = self._lock.acquire(*args, **kwargs)
        if acquired:
            self._gc_was_enabled = gc.isenabled()
            if self._gc_was_enabled:
                gc.disable()
        return acquired

    def release(self) -> None:
        # Restore GC (tylko cofamy WŁASNE wyłączenie) PRZED zwolnieniem realnego
        # locka; własność RLocka czyści się C-atomowo w `release` — brak osobnej
        # zmiennej ownera i jej okna. Dopóki nie zwolniliśmy locka, re-entrancja
        # (np. finalizer po `gc.enable()`) wciąż widzi `_is_owned()` i fail-fastuje.
        if self._gc_was_enabled:
            self._gc_was_enabled = False
            gc.enable()
        self._lock.release()

    def __enter__(self) -> "_GcPausingLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    def locked(self) -> bool:
        # Własność BIEŻĄCEGO wątku — dokładnie warunek re-entrancji/deadlocku,
        # który sprawdza finalizer w teście deferred-deref (widzi lock zwolniony
        # względem SIEBIE, bo deref biegnie poza sekcją krytyczną).
        return bool(self._lock._is_owned())


_PHYSICAL_LOAD_LOCK = _GcPausingLock()

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY_WALK_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _O_CLOEXEC
)
_SOURCE_OPEN_FLAGS = (
    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _O_CLOEXEC
)
_LOADED_PHYSICAL_SIBLINGS: dict[str, tuple[str, ModuleType]] = {}
class _RetainedDirFd:
    """Owned handle for the attested package directory descriptor.

    The route pins a directory descriptor for the package lifetime; a raw
    ``int`` has no owner, so dropping the route would leak the fd.  The fd is
    closed by exactly one of:

    * explicit canonical retirement — :func:`_retire_package_route` pops the
      route entry and calls :meth:`close` (used by the loader's rollback and
      by any caller retiring a package);
    * re-registration — reloading the same package name overwrites the route
      entry.  While the module is still bound in ``sys.modules`` the reuse gate
      (``_adopt_or_reject_preloaded``) returns the existing module and no
      reload happens; but if the module was first removed from ``sys.modules``
      a reload DOES proceed and overwrites the route with a fresh handle.  The
      previous handle, having lost its last reference, is then closed by the GC
      finalizer below;
    * a GC finalizer backstop (:meth:`__del__`) — for handles dropped without
      explicit retirement (route dict replaced, or overwritten as above).

    Removing the *module* from ``sys.modules`` does NOT immediately close the
    fd (the route still holds the handle), but descendant resolution then
    FAILS CLOSED rather than continuing: the finder's identity check requires
    ``sys.modules[root] is route[2]``, which no longer holds once the module is
    gone, so descendant imports raise instead of executing.
    """

    __slots__ = ("fd",)

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def close(self) -> None:
        fd = self.fd
        if fd is not None:
            self.fd = None
            os.close(fd)

    def __del__(self) -> None:  # finalizer backstop (nie zastępuje close)
        try:
            self.close()
        except Exception:
            pass


_PHYSICAL_PACKAGE_ROUTES: dict[
    str, tuple[str, str, ModuleType, "_RetainedDirFd"]
] = {}
_PHYSICAL_DESCENDANT_MODULES: dict[str, ModuleType] = {}

# Nazwy pakietów AKTUALNIE inicjalizowane przez loader (opublikowane w
# `sys.modules`, ale `exec __init__.py` jeszcze trwa) → ident wątku-inicjalizatora.
# FAIL-CLOSED przeciw publikacji częściowego modułu (Candidate59/Sol C58 #2):
# równoległy load tej samej nazwy w trakcie inicjalizacji NIE dostaje częściowego
# modułu jako sukcesu — dostaje `RuntimeError`. Re-entrancja z TEGO SAMEGO wątku
# (samo-import w `__init__`) przechodzi dalej do ownera, który zwróci częściowy
# moduł zgodnie z semantyką cyklu importowego CPythona. To NIE jest chroniony
# rejestr reuse (owner-registry pozostaje jedynym źródłem decyzji reuse) — tylko
# marker „w toku", więc nie zmienia kontraktu jednego ownera.
_PHYSICAL_INITIALIZING: dict[str, tuple] = {}

# Liczniki roota będącego w oknie retirement/re-registration (Candidate59/Sol C58
# #3). Finder FAIL-CLOSUJE `import <root>` o tej nazwie zamiast przepuścić go do
# `PathFindera`, gdy purge zdjął root z `sys.modules`, a trasa jeszcze się zwija —
# inaczej obce bajty roota o nazwie kolidującej z zatrutym `sys.path` zostałyby
# wykonane.  Licznik (nie set) komponuje ZAGNIEŻDŻONE okna: wewnętrzny retire w
# środku okna re-registration loadera trzyma marker aż do ponownej publikacji.
_ROOTS_UNDER_RETIREMENT: dict[str, int] = {}


def _mark_root_retiring(module_name: str) -> None:
    """Wejdź w okno, w którym `import <module_name>` (root) fail-closuje."""
    _ROOTS_UNDER_RETIREMENT[module_name] = (
        _ROOTS_UNDER_RETIREMENT.get(module_name, 0) + 1
    )


def _unmark_root_retiring(module_name: str) -> None:
    """Wyjdź z okna; zdejmij klucz dopiero gdy licznik zejdzie do zera."""
    remaining = _ROOTS_UNDER_RETIREMENT.get(module_name, 0) - 1
    if remaining > 0:
        _ROOTS_UNDER_RETIREMENT[module_name] = remaining
    else:
        _ROOTS_UNDER_RETIREMENT.pop(module_name, None)


def _purge_owned_module_subtree(name: str) -> list:
    """Canonical purge of an owned subtree from ALL owned registries.

    Single source of the purge policy: drops ``name`` and every ``name.*`` from
    ``sys.modules``, from ``_PHYSICAL_DESCENDANT_MODULES`` and from the owner
    reuse registry ``_LOADED_PHYSICAL_SIBLINGS``.  This is the ONLY place that
    pops the descendant identity registry, so package retirement, non-package
    rollback and descendant-exec rollback share one implementation (no competing
    writers).  Popping the owner registry here makes rollback complete: a failed
    load leaves NO dangling reuse evidence for a dead module object.

    Lock-free core: callers OUTSIDE the loader's critical section acquire
    ``_PHYSICAL_LOAD_LOCK`` before calling; callers already holding it (loader
    publish rollback, ``_retire_package_route``) call it directly.

    ZWRACA listę usuniętych obiektów modułów, by ich DESTRUKCJA (`__del__` =
    KOD UŻYTKOWNIKA) nastąpiła POZA lockiem (Candidate55/Sol C54): gdyby ostatnia
    referencja do modułu spadła pod nie-reentrantnym `_PHYSICAL_LOAD_LOCK`, jego
    finalizer mógłby re-entrować loader i zadeadlockować. Caller trzyma zwróconą
    listę do wyjścia z sekcji krytycznej. Pop-y są bare-mutacjami (wynik NIE jest
    używany do reuse); przechwycenie do listy-derefów jest jawnie autoryzowanym
    odczytem rejestrów wyłącznie w tym kanonicznym purge.
    """
    doomed: list = []
    prefix = name + "."
    # PRZECHWYĆ obiekty do listy-derefów PRZED popami, z OWNED rejestrów
    # (descendant + owner) — bez odczytu WARTOŚCI z `sys.modules`, więc pętla
    # cleanup po `sys.modules` pozostaje bare-pop (whitelist C46 nietknięty).
    # Każdy obiekt z poddrzewa jest naszą własnością zarejestrowaną tu.
    for entry in tuple(_PHYSICAL_DESCENDANT_MODULES):
        if entry == name or entry.startswith(prefix):
            doomed.append(_PHYSICAL_DESCENDANT_MODULES.get(entry))
    recorded = _LOADED_PHYSICAL_SIBLINGS.get(name)
    if recorded is not None:
        doomed.append(recorded[1])
    # POP-y (bare mutacje — wynik NIE używany):
    for entry in tuple(sys.modules):
        if entry == name or entry.startswith(prefix):
            sys.modules.pop(entry, None)
    for entry in tuple(_PHYSICAL_DESCENDANT_MODULES):
        if entry == name or entry.startswith(prefix):
            _PHYSICAL_DESCENDANT_MODULES.pop(entry, None)
    # Owner reuse registry jest kluczowana DOKŁADNĄ nazwą top-level.
    _LOADED_PHYSICAL_SIBLINGS.pop(name, None)
    return doomed


def _retire_package_route(module_name: str) -> None:
    """Canonically retire a package: PURGE subtree, then pop route + CLOSE fd.

    Full teardown of a package's owned state in one place: the owned subtree
    (``_purge_owned_module_subtree``) AND the route entry with its retained
    directory descriptor.  Called by the loader's rollback and by any explicit
    retirement.

    Lock-free core (callers outside the critical section acquire
    ``_PHYSICAL_LOAD_LOCK``).

    ORDER MATTERS (Candidate50/Sol C49 — retire-side twin of the publish order):
    the subtree purge (which removes the root from ``sys.modules``) runs BEFORE
    the route is popped.  Otherwise there is a window — root still bound in
    ``sys.modules`` but its route already gone — in which a concurrent
    ``<root>.<child>`` import finds the finder's route absent, returns ``None``,
    and falls through to ``PathFinder`` / another finder that executes foreign
    descendant bytes off the textual ``__path__``.  Purging first makes every
    intermediate state fail-closed: while the root is still published the route
    still exists (so ``find_spec`` either owns the descendant or raises on the
    ancestor-identity mismatch), and once the root is gone a descendant import
    fails at the missing root rather than escaping to text-path resolution.
    Because the purge runs first and unconditionally, a raising ``handle.close``
    (e.g. ``os.close`` failing) can never leave ``sys.modules`` / the registries
    populated.

    ZWRACA listę usuniętych obiektów modułów (poddrzewo + obiekt pakietu z
    trasy), by ich `__del__` (kod użytkownika) nastąpił POZA lockiem — patrz
    `_purge_owned_module_subtree` (Candidate55/Sol C54).
    """
    # TOMBSTONE roota na CAŁE okno retire (Candidate59/Sol C58 #3): oznacz PRZED
    # purge (który zdejmuje root z `sys.modules`) i zdejmij DOPIERO po pełnym
    # zwinięciu trasy + zamknięciu fd. W tym oknie `import <root>` fail-closuje w
    # finderze zamiast zejść do `PathFindera` i wykonać obcy root z `sys.path`.
    _mark_root_retiring(module_name)
    # `purge` + `pop` NIE mogą rzucić (retire biegnie pod load-lockiem → GC
    # wyłączony, brak finalizerów; `dict.pop(..., None)` nie podnosi) — jedynym
    # źródłem wyjątku jest `handle.close()` (os.close EINTR), więc TYLKO ono jest
    # objęte try/finally, aby tombstone został zdjęty także na wyjątku close.
    # (Dzięki temu purge/pop zachowują pierwotne wcięcie — mniejszy blast-radius.)
    doomed = _purge_owned_module_subtree(module_name)
    route = _PHYSICAL_PACKAGE_ROUTES.pop(module_name, None)
    try:
        if route is not None:
            handle = route[3]
            if isinstance(handle, _RetainedDirFd):
                handle.close()
            doomed.append(route[2])  # obiekt pakietu — deref POZA lockiem
        return doomed
    finally:
        _unmark_root_retiring(module_name)


def _current_generation_is(module_name: str, module: ModuleType) -> bool:
    """Czy NASZA generacja (``module``) wciąż WŁADA nazwą w OWNED rejestrach.

    Tożsamość generacji sprawdzana wobec WŁASNYCH rejestrów (trasa dla pakietu,
    owner-registry dla modułu), NIE wobec ``sys.modules`` (Candidate54/Sol C53).
    Dzięki temu rollback:
      * SPRZĄTA własną trasę/owner/fd nawet gdy root usunięto już z samego
        ``sys.modules`` (trasa/fd nadal nasze → brak wycieku); ORAZ
      * POMIJA sprzątanie, gdy nowsza generacja przejęła nazwę (trasa/owner
        wskazują inny obiekt → nie usuwamy cudzej, poprawnej generacji).
    Kontrolowane porównanie TOŻSAMOŚCI (nie zwrot obiektu → nie fast-path reuse);
    jawnie autoryzowany w ratchecie jako jedyny — poza ownerem/finderem/retire —
    czytelnik trasy oraz owner-registry.
    """
    # OR po WSZYSTKICH owned stanach: nasza generacja „włada" nazwą, jeśli
    # którykolwiek z (sys.modules, trasa, owner-registry) nadal wskazuje NASZ
    # obiekt. Pokrywa każdy stan pośredni: publikacja częściowa (sys.modules
    # ustawione, trasa jeszcze nie), usunięcie roota z samego sys.modules
    # (trasa/fd nadal nasze → sprzątamy, brak wycieku), oraz przejęcie nazwy
    # przez nowszą generację (żaden stan nie wskazuje nas → pomijamy).
    if sys.modules.get(module_name) is module:
        return True
    route = _PHYSICAL_PACKAGE_ROUTES.get(module_name)
    if route is not None and route[2] is module:
        return True
    recorded = _LOADED_PHYSICAL_SIBLINGS.get(module_name)
    if recorded is not None and recorded[1] is module:
        return True
    return False


def _descendant_generation_current(
    fullname: str, retained_handle: "_RetainedDirFd"
) -> bool:
    """Czy trasa roota potomka WCIĄŻ trzyma DOKŁADNIE ten retained handle.

    Między `find_spec` (który wziął handle z trasy generacji G) a `exec_module`
    root mógł zostać zretirowany i zastąpiony nową generacją (inny handle) —
    stary `exec_module` NIE może wtedy zapisać potomka do rejestru nowej
    generacji (Candidate55/Sol C54 #3). Kontrolowany odczyt trasy (porównanie
    tożsamości handle), jawnie autoryzowany w ratchecie.
    """
    root = fullname.partition(".")[0]
    route = _PHYSICAL_PACKAGE_ROUTES.get(root)
    return route is not None and route[3] is retained_handle


class PhysicalSiblingBoundaryError(RuntimeError):
    """A relative component is not a physical entry of the attested parent."""


def attest_physical_scripts_dir(path_value: str | Path) -> Path:
    """Require an environment snapshot to own this physical package."""
    package_file = Path(dispatch_v2.__file__).resolve()
    expected = package_file.parent.parent
    candidate = Path(path_value)
    try:
        matches = (
            candidate.is_dir()
            and expected.is_dir()
            and candidate.samefile(expected)
        )
    except OSError as exc:
        raise RuntimeError(
            "cannot attest physical scripts directory"
        ) from exc
    if not matches:
        raise RuntimeError(
            "scripts directory does not own physical dispatch_v2 package"
        )
    return expected


def _attested_package_parent() -> Path:
    package_file = Path(dispatch_v2.__file__).resolve()
    if not package_file.is_file() or package_file.name != "__init__.py":
        raise RuntimeError("cannot attest physical dispatch_v2 package")
    return package_file.parent.parent


def _close_fd_quietly(fd: int) -> None:
    """Close ``fd`` swallowing ``OSError`` (Candidate54/Sol C53).

    Używane tam, gdzie zamykamy WIELE deskryptorów w ścieżce sprzątającej:
    wyjątek z jednego ``os.close`` (np. EINTR — na Linuksie deskryptor i tak
    jest zamknięty, POSIX zabrania ponownego close) NIE może pominąć zamknięcia
    pozostałych ani osierocić już otwartego deskryptora.
    """
    try:
        os.close(fd)
    except OSError:
        pass


def _dup_cloexec(fd: int) -> int:
    """Duplicate ``fd`` with close-on-exec set (a fresh, owned descriptor)."""
    new_fd = os.dup(fd)
    try:
        os.set_inheritable(new_fd, False)
    except BaseException:
        os.close(new_fd)
        raise
    return new_fd


def _dup_retained_under_lock(handle: "_RetainedDirFd") -> int:
    """Atomically read a retained descriptor and ``dup`` it, fail-closed.

    The raw fd number and the ``dup`` happen under ``_PHYSICAL_LOAD_LOCK`` so a
    concurrent ``_retire_package_route`` cannot close the handle (and let the
    kernel recycle its fd number) BETWEEN the read and the ``dup`` — which would
    otherwise redirect a descendant open to an unrelated directory.  If the
    handle was already retired (``fd is None``) the descendant fails closed
    instead of dup-ing a stale/absent descriptor.
    """
    with _PHYSICAL_LOAD_LOCK:
        fd = handle.fd
        if fd is None:
            raise ModuleNotFoundError(
                "physical sibling package descriptor was retired"
            )
        return _dup_cloexec(fd)


def _walk_components_from_fd(fd: int, directory_parts: tuple[str, ...]) -> int:
    """Walk ``directory_parts`` with ``O_NOFOLLOW`` from an OWNED start fd.

    Takes ownership of ``fd`` (closes it while descending); returns the final
    directory fd.  A symlink component raises ``PhysicalSiblingBoundaryError``.
    """
    try:
        for part in directory_parts:
            try:
                next_fd = os.open(part, _DIRECTORY_WALK_FLAGS, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ENOTDIR, errno.ELOOP):
                    try:
                        component_mode = os.stat(
                            part,
                            dir_fd=fd,
                            follow_symlinks=False,
                        ).st_mode
                    except OSError:
                        component_mode = None
                    if component_mode is not None and stat.S_ISLNK(
                        component_mode
                    ):
                        raise PhysicalSiblingBoundaryError(
                            "physical sibling component is a symlink: "
                            f"{part!r}"
                        ) from exc
                raise
            # Transfer własności EXCEPTION-SAFE (Candidate53/Sol C52): adoptuj
            # `next_fd` do `fd` PRZED zamknięciem starego, więc pojedynczy
            # posiadacz (`fd`) jest zawsze aktualny; wyjątek z `os.close(old)`
            # (np. EINTR — na Linuksie deskryptor i tak jest zamknięty, POSIX
            # zabrania ponownego close) jest połykany, by NIE zamknąć ponownie
            # zrecyklingowanego numeru ani nie wyciec `next_fd`.
            old_fd = fd
            fd = next_fd
            try:
                os.close(old_fd)
            except OSError:
                pass
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return fd


def _walk_directory_descriptor(
    parent_dir: Path,
    directory_parts: tuple[str, ...],
) -> int:
    """Open every directory component with ``O_NOFOLLOW`` off the parent fd."""
    fd = os.open(str(parent_dir), os.O_RDONLY | os.O_DIRECTORY | _O_CLOEXEC)
    return _walk_components_from_fd(fd, directory_parts)


def _open_descendant_source_fd(
    retained_handle: "_RetainedDirFd",
    remainder_parts: tuple[str, ...],
    *,
    package: bool,
) -> int:
    """Open a descendant's source fd RELATIVE to the retained package fd.

    ``retained_handle`` owns the open descriptor of the attested package
    directory captured at load.  Descendants are resolved by walking the
    remaining components from a DUP of it — taken atomically under the load lock
    (fail-closed if the handle was retired) — with ``O_NOFOLLOW`` at every step,
    so the attested directory is never re-resolved by name: a path swapped after
    load — even one whose new directory recycled the old inode number — cannot
    redirect the open, because the descriptor still refers to the original
    directory.  This also closes the find_spec→exec_module window, since the
    byte-open uses the same pinned descriptor rather than a fresh walk.
    """
    if package:
        directory_parts = remainder_parts
        file_name = "__init__.py"
    else:
        directory_parts = remainder_parts[:-1]
        file_name = remainder_parts[-1] + ".py"
    # Walk od DUP-a utrzymanego fd (walk przejmuje i zamyka dup); dup wzięty
    # atomowo wobec close w retirement.
    source_dir_fd = _walk_components_from_fd(
        _dup_retained_under_lock(retained_handle), directory_parts
    )
    try:
        file_fd = os.open(file_name, _SOURCE_OPEN_FLAGS, dir_fd=source_dir_fd)
    finally:
        # Zamknięcie katalogu-fd NIE może wypropagować błędu (osierociłby już
        # otwarty `file_fd`) — Candidate54/Sol C53.
        _close_fd_quietly(source_dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise RuntimeError(
                "physical sibling source is not a regular file"
            )
    except BaseException:
        _close_fd_quietly(file_fd)
        raise
    return file_fd


def _open_physical_source_fd(
    parent_dir: Path,
    relative: Path,
    *,
    package: bool,
) -> tuple[int, int]:
    """Walk with ``O_NOFOLLOW``; return ``(source_fd, containing_dir_fd)``.

    BOTH descriptors are returned OPEN and owned by the caller.  The directory
    descriptor is the SAME one that opens the source file; retaining it (rather
    than a recyclable ``(st_dev, st_ino)`` pair) gives a stable, non-reusable
    handle to the attested directory — a later ``rename``+``rmdir``+``mkdir``
    can recycle an inode number but cannot reattach this open descriptor to a
    different directory, so descendants opened relative to it always resolve
    inside the originally attested directory.
    """
    directory_parts = relative.parts if package else relative.parts[:-1]
    file_name = "__init__.py" if package else relative.parts[-1]
    dir_fd = _walk_directory_descriptor(parent_dir, directory_parts)
    try:
        file_fd = os.open(file_name, _SOURCE_OPEN_FLAGS, dir_fd=dir_fd)
    except BaseException:
        _close_fd_quietly(dir_fd)
        raise
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise RuntimeError(
                "physical sibling source is not a regular file"
            )
    except BaseException:
        # OBA deskryptory zamknięte NIEZALEŻNIE (Candidate54/Sol C53): wyjątek
        # z jednego close nie może pominąć drugiego.
        _close_fd_quietly(file_fd)
        _close_fd_quietly(dir_fd)
        raise
    return file_fd, dir_fd


def _read_all_from_fd(source_fd: int) -> bytes:
    chunks = []
    while True:
        chunk = os.read(source_fd, 1 << 20)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _stat_entry_no_follow(name: str, dir_fd: int) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _descendant_is_package(
    retained_handle: "_RetainedDirFd",
    remainder_parts: tuple[str, ...],
) -> bool:
    """Classify a descendant, walking from the retained package fd, fail-closed.

    Returns ``True`` for a physical subpackage (directory with a regular
    ``__init__.py``), ``False`` for a physical module file.  Resolves from a
    DUP of the retained attested-directory descriptor (taken atomically under
    the load lock, fail-closed if the handle was retired), so classification and
    the later byte-open share the same pinned directory identity.  A symlinked
    component is a boundary violation and anything else is a hard
    ``ModuleNotFoundError`` — never a fall-through to text-path resolution.
    """
    directory_fd = _walk_components_from_fd(
        _dup_retained_under_lock(retained_handle), remainder_parts[:-1]
    )
    try:
        last = remainder_parts[-1]
        entry_stat = _stat_entry_no_follow(last, directory_fd)
        if entry_stat is not None:
            if stat.S_ISLNK(entry_stat.st_mode):
                raise PhysicalSiblingBoundaryError(
                    f"physical sibling component is a symlink: {last!r}"
                )
            if stat.S_ISDIR(entry_stat.st_mode):
                package_fd = os.open(
                    last, _DIRECTORY_WALK_FLAGS, dir_fd=directory_fd
                )
                try:
                    init_stat = _stat_entry_no_follow(
                        "__init__.py", package_fd
                    )
                finally:
                    os.close(package_fd)
                if init_stat is not None and stat.S_ISREG(init_stat.st_mode):
                    return True
        module_stat = _stat_entry_no_follow(last + ".py", directory_fd)
        if module_stat is not None:
            if stat.S_ISLNK(module_stat.st_mode):
                raise PhysicalSiblingBoundaryError(
                    "physical sibling component is a symlink: "
                    f"{last + '.py'!r}"
                )
            if stat.S_ISREG(module_stat.st_mode):
                return False
        raise ModuleNotFoundError(
            "trusted physical sibling descendant unavailable: "
            + ".".join(remainder_parts)
        )
    finally:
        os.close(directory_fd)


class _PhysicalSiblingDescendantLoader:
    """Execute one descendant from bytes read off the walked descriptor."""

    def __init__(
        self,
        fullname: str,
        parent_dir: Path,
        root_relative: Path,
        remainder_parts: tuple[str, ...],
        is_package: bool,
        retained_handle: "_RetainedDirFd",
    ) -> None:
        self._fullname = fullname
        self._parent_dir = parent_dir
        self._root_relative = root_relative
        self._remainder_parts = remainder_parts
        self._is_package = is_package
        self._retained_handle = retained_handle

    def create_module(self, spec):  # standard: defer to default machinery
        return None

    def exec_module(self, module: ModuleType) -> None:
        if self._is_package:
            source = (
                self._parent_dir
                / self._root_relative
                / Path(*self._remainder_parts)
                / "__init__.py"
            )
        else:
            source = (
                self._parent_dir
                / self._root_relative
                / Path(*self._remainder_parts[:-1])
                / (self._remainder_parts[-1] + ".py")
            )
        # Bajty otwierane RELATYWNIE do UTRZYMANEGO deskryptora atestowanego
        # katalogu (route[3]) — nie po nazwie — więc podmiana ścieżki (nawet
        # z recyklingiem inode) jej nie przekieruje; domyka find_spec→exec.
        source_fd = _open_descendant_source_fd(
            self._retained_handle,
            self._remainder_parts,
            package=self._is_package,
        )
        try:
            source_bytes = _read_all_from_fd(source_fd)
        finally:
            os.close(source_fd)
        code = compile(source_bytes, str(source), "exec", dont_inherit=True)
        # Rejestruj TOZSAMOSC potomka PRZED exec (dzieci importowane w init
        # widza swojego rodzica jako naszego, zapisanego ownera), rollback na
        # bledzie exec. Publikacja tozsamosci pod lockiem (atomowo wobec
        # rownoleglego adopt/retire), exec BEZ locka (kod uzytkownika).
        # GENERATION-CHECK (Candidate55/Sol C54 #3): między find_spec a tym
        # zapisem root mógł zostać zretirowany i zastąpiony NOWĄ generacją.
        # Zapisujemy tożsamość potomka TYLKO gdy nasz retained handle to WCIĄŻ
        # aktualna trasa roota — inaczej fail-closed, by NIE zanieczyścić
        # rejestru nowej generacji obiektem starej.
        with _PHYSICAL_LOAD_LOCK:
            if not _descendant_generation_current(
                self._fullname, self._retained_handle
            ):
                raise ModuleNotFoundError(
                    "physical sibling root generation retired during descendant "
                    f"load: {self._fullname}"
                )
            _PHYSICAL_DESCENDANT_MODULES[self._fullname] = module
        try:
            exec(code, module.__dict__)
        except BaseException:
            # Pełny rollback PODDRZEWA tego potomka: gdy jego __init__.py
            # zaimportował własne dzieci (`from . import leaf`) i dopiero potem
            # padł, te wnuki są w sys.modules i w rejestrze — kanoniczny purge
            # czyści je RAZEM z tym potomkiem (symetrycznie do retirement roota).
            # GENERATION-SAFE (Candidate57/Sol C56 #2): czyścimy TYLKO gdy nasz
            # retained handle to wciąż aktualna trasa roota; jeśli równoległy
            # retire+gen2 przejął nazwę w trakcie exec potomka, NIE wolno usunąć
            # poddrzewa cudzej, poprawnej generacji. Purge pod lockiem; doomed
            # moduły deref POZA lockiem (#1).
            _dd: list = []
            with _PHYSICAL_LOAD_LOCK:
                if _descendant_generation_current(
                    self._fullname, self._retained_handle
                ):
                    _dd = _purge_owned_module_subtree(self._fullname)
            del _dd[:]
            raise


class _PhysicalSiblingDescendantFinder:
    """Own descendant *resolution* for a package this loader bound.

    Installed ahead of ``PathFinder`` so ``submodule_search_locations``
    (kept only for read-only introspection) never resolves descendant
    imports; each descendant is opened relative to the RETAINED attested
    package descriptor (``O_NOFOLLOW`` at every component), so a post-load
    path swap — even one recycling the old inode number — cannot redirect
    the bytes.  Ownership covers every descendant import reaching ``sys.meta_path``;
    it does not override CPython's ``sys.modules``-before-finders lookup, so a
    descendant name already bound in ``sys.modules`` is served from that cache
    without any finder — the same limit that lets in-process injection shadow
    ``dispatch_v2.common`` itself, and outside this loader's threat model.
    """

    def find_spec(self, fullname, path=None, target=None):
        root, separator, remainder = fullname.partition(".")
        if not separator:
            # Import roota (bez kropki) normalnie NIE należy do tego findera —
            # zwracamy None i przepuszczamy do PathFindera. DWA wyjątki
            # fail-closed, oba dla nazwy KOLIDUJĄCEJ z zatrutym `sys.path`/stdlib:
            #
            # (a) OKNO retirement/re-registration roota (purge zdjął go z
            #     `sys.modules`, trasa się zwija): przepuszczenie pozwoliłoby
            #     `PathFinderowi` wykonać OBCE bajty roota (Candidate59/Sol C58
            #     #3). Tombstone `_ROOTS_UNDER_RETIREMENT` fail-closuje to okno.
            #
            # (b) NAZWA, KTÓRĄ TRWALE POSIADAMY — root jest w naszej trasie
            #     pakietu LUB owner-registry siblinga (Candidate63/Sol C62).
            #     `importlib.reload(<nasz sibling>)` NIE używa `m.__spec__.loader`
            #     — re-rozwiązuje spec przez `sys.meta_path`
            #     (`_bootstrap._find_spec(name, target=m)`); dla nazwy kolidującej
            #     ze stdlib/sys.path (np. sibling `geometry.py` załadowany pod
            #     nazwą `calendar`) zwrócenie `None` zeszłoby do `PathFindera` i
            #     WYKONAŁO obcy plik z sys.path DO naszego zaufanego obiektu.
            #     To samo dotyczy re-importu po `del sys.modules[root]`. Normalny
            #     `import <root>` załadowanego siblinga NIE konsultuje findera
            #     (CPython: `sys.modules`-first → zwraca nasz obiekt z cache),
            #     więc ten fail-close dotyka WYŁĄCZNIE reload / re-import —
            #     dokładnie tam, gdzie chcemy fail-closed. Nazw, których NIE
            #     posiadamy (json, calendar-gdy-nie-nasze itd.), NIE ma w żadnym
            #     rejestrze → `return None`, `PathFinder` rozwiązuje je normalnie.
            #     Odczyt rejestrów jest membership-testem (atomowy pod GIL,
            #     lock-free — jak istniejący odczyt tombstone/trasy niżej), więc
            #     nie wprowadza porządku blokad ani deadlocku.
            if (
                fullname in _ROOTS_UNDER_RETIREMENT
                or fullname in _PHYSICAL_PACKAGE_ROUTES
                or fullname in _LOADED_PHYSICAL_SIBLINGS
            ):
                raise ModuleNotFoundError(
                    "physical sibling root is owned/retiring; refusing text-path "
                    f"resolution of a colliding name: {fullname}"
                )
            return None
        route = _PHYSICAL_PACKAGE_ROUTES.get(root)
        if route is None:
            # Nazwa DOTTED, której ROOT nie jest naszym pakietem, więc to NIE
            # jest descendant owned-pakietu. ALE sama pełna nazwa dotted może
            # być NASZYM standalone siblingiem, załadowanym pod nazwą kropkowaną
            # KOLIDUJĄCĄ ze stdlib/`sys.path` (np. `email.utils`, package=False,
            # gdzie `email` NIE jest naszym pakietem) LUB być w jej oknie
            # retirement/re-registration. To WARIANT DOTTED tego samego defektu
            # co root-level (Candidate64/Sol C63, domykający Candidate63/Sol C62):
            # `importlib.reload(<nasz dotted sibling>)` re-rozwiązuje spec przez
            # `sys.meta_path` (`_bootstrap._find_spec(name, parent.__path__,
            # target=m)`), a re-import po `del sys.modules[name]` też trafia
            # findera. Zwrócenie `None` zeszłoby do `PathFindera`, który po
            # `parent.__path__` (stdlib/`sys.path`) WYKONAŁby OBCY plik dziecka
            # DO naszego zaufanego obiektu. Fail-closujemy TĄ SAMĄ regułą
            # owned-name co gałąź root-level wyżej — nazwa w NASZEJ trasie,
            # owner-registry siblingów LUB oknie retirement. LEGALNY descendant
            # owned-PAKIETU tu NIE trafia: dla niego `route is not None`, więc
            # rozwiązuje go ścieżka descendant NIŻEJ (retained fd) — a descendanty
            # NIE są w `_LOADED_PHYSICAL_SIBLINGS` (tam wyłącznie top-level/
            # standalone rejestrowane przez owner-registry), więc ta reguła ich
            # nie dotyka (kolejność: descendant-route dla owned-pakietu MA
            # pierwszeństwo, dopiero potem owned-standalone fail-close).
            # Normalny `import <dotted>` załadowanego siblinga NIE konsultuje
            # findera (CPython `sys.modules`-first → zwraca nasz obiekt z cache),
            # więc fail-close dotyka WYŁĄCZNIE reload / re-import. Nazw CUDZYCH
            # (json, `email.utils`-gdy-nie-nasze) NIE ma w żadnym rejestrze →
            # `return None`, `PathFinder` rozwiązuje je normalnie. Odczyt
            # rejestrów jest membership-testem (atomowy pod GIL, lock-free — jak
            # gałąź root-level), więc nie wprowadza porządku blokad ani deadlocku.
            if (
                fullname in _ROOTS_UNDER_RETIREMENT
                or fullname in _PHYSICAL_PACKAGE_ROUTES
                or fullname in _LOADED_PHYSICAL_SIBLINGS
            ):
                raise ModuleNotFoundError(
                    "physical sibling name is owned/retiring; refusing text-path "
                    f"resolution of a colliding dotted name: {fullname}"
                )
            return None
        if sys.modules.get(root) is not route[2]:
            # Trasa jest zwiazana z TOZSAMOSCIA zapisanego obiektu pakietu:
            # obcy modul pod nazwa root nie moze przejac fizycznych potomkow
            # ani przekazac ich zwyklemu rozwiazywaniu sciezki tekstowej.
            raise RuntimeError(
                f"conflicting preloaded physical sibling package: {root}"
            )
        # Tozsamosc CALEGO lancucha przodkow, nie tylko roota: kazdy posredni
        # pakiet miedzy rootem a potomkiem MUSI byc dokladnie tym obiektem,
        # ktory nasz loader wykonal. Obcy ModuleType wstawiony pod nazwe
        # posredniego pakietu (z wlasnym __path__) nie moze przejac
        # fizycznego lisca ani doczepic go do siebie.
        ancestors = fullname.split(".")
        prefix = root
        for part in ancestors[1:-1]:
            prefix = prefix + "." + part
            recorded = _PHYSICAL_DESCENDANT_MODULES.get(prefix)
            if recorded is None or sys.modules.get(prefix) is not recorded:
                raise RuntimeError(
                    "conflicting preloaded physical sibling package: "
                    f"{prefix}"
                )
        parent_dir = Path(route[0])
        root_relative = Path(route[1])
        retained_handle = route[3]
        # Rozwiazanie potomka WYLACZNIE wzgledem UTRZYMANEGO deskryptora
        # atestowanego katalogu (route[3]) — nigdy ponownie po nazwie. Handle
        # (nie surowy numer fd) przenoszony dalej, a dup brany atomowo pod
        # lockiem w punkcie uzycia: podmiana sciezki (nawet z recyklingiem
        # numeru inode) jest nieistotna, a rownolegly retire konczy sie
        # fail-closed zamiast dup-a zrecyklingowanego deskryptora. To takze
        # zamyka okno find_spec->exec_module (ta sama pinowana tozsamosc).
        remainder_parts = tuple(remainder.split("."))
        relative = root_relative.joinpath(*remainder_parts)
        try:
            is_package = _descendant_is_package(
                retained_handle, remainder_parts
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ModuleNotFoundError(
                f"trusted physical sibling descendant unavailable: {fullname}"
            ) from exc
        if is_package:
            origin = parent_dir / relative / "__init__.py"
        else:
            origin = parent_dir / relative.parent / (relative.name + ".py")
        loader = _PhysicalSiblingDescendantLoader(
            fullname,
            parent_dir,
            root_relative,
            remainder_parts,
            is_package,
            retained_handle,
        )
        spec = importlib.util.spec_from_loader(
            fullname,
            loader,
            origin=str(origin),
            is_package=is_package,
        )
        if spec is None:
            raise RuntimeError(
                f"cannot attest physical sibling descendant: {fullname}"
            )
        # __path__ potomnego subpakietu pozostaje PUSTE (Candidate51/Sol C50):
        # jego wnuki rozwiązuje TEN finder z utrzymanego deskryptora roota, nie
        # z tekstowej ścieżki, więc nie dokładamy fizycznego katalogu do
        # `submodule_search_locations` — inaczej `PathFinder` mógłby wykonać
        # bajty wnuka z dysku, gdyby finder kiedyś zwrócił None. Fail-closed jest
        # jedyną ścieżką dla nie-rozpoznanego wnuka.
        spec.has_location = True
        return spec


_DESCENDANT_FINDER = _PhysicalSiblingDescendantFinder()


def _ensure_descendant_finder_installed() -> None:
    if not any(
        entry is _DESCENDANT_FINDER for entry in sys.meta_path
    ):
        sys.meta_path.insert(0, _DESCENDANT_FINDER)


# INSTALACJA FINDERA PRZY IMPORCIE MODUŁU — ROOT-FIX fresh-root okna load
# (Candidate61/Sol C60 #A). Tombstone `_ROOTS_UNDER_RETIREMENT` (ustawiany na
# SAMYM POCZĄTKU sekcji krytycznej load od C60) fail-closuje `import <root>`
# WYŁĄCZNIE przez tego findera. Gdy finder instalował się dopiero w PUBLIKACJI
# (`_ensure_descendant_finder_installed`, PO oknie), root ładowany PIERWSZY raz
# w procesie NIE miał findera na `sys.meta_path` w oknie pre-publish: `import
# <root>` kolidującej nazwy schodził wprost do `PathFindera` i WYKONYWAŁ obce
# `__init__.py` mimo ustawionego tombstone'u (świeży blind Sol/max na C60,
# POTWIERDZONY: zapis pliku na dysk). Finder-singleton jest INERTNY, dopóki mapy
# tras/tombstone są puste (`find_spec` roota bez wpisu zwraca None → normalne
# importy nietknięte), więc obecność na `meta_path` od importu modułu jest
# bezpieczna, a JEDYNYM aktywnym mechanizmem pozostaje atomowy tombstone. Finder
# czyta tombstone LOCK-FREE (nie bierze `_PHYSICAL_LOAD_LOCK`), więc obecność od
# importu nie wprowadza porządku blokad ani deadlocku. Publikacyjne
# `_ensure_descendant_finder_installed()` pozostaje jako IDEMPOTENTNY kanoniczny
# punkt instalacji (no-op, gdy finder już obecny) — jeden owner polityki, brak
# konkurencyjnego writera.
_ensure_descendant_finder_installed()


def _adopt_or_reject_preloaded(
    module_name: str,
    source: Path,
) -> ModuleType | None:
    """Accept only the exact object this loader recorded for this source.

    Module metadata such as ``__file__`` is mutable and proves nothing about
    which bytes executed; the private registry entry written after a
    successful descriptor-bound exec is the only admissible reuse evidence.
    This is the single owner of the reuse/conflict decision and it runs on
    every load request — also when the physical source is absent, so an
    optional sibling can never silently coexist with a foreign module under
    the same name.
    """
    if not any(
        name == module_name or name.startswith(module_name + ".")
        for name in sys.modules
    ):
        return None
    existing = sys.modules.get(module_name)
    if existing is not None:
        recorded = _LOADED_PHYSICAL_SIBLINGS.get(module_name)
        if (
            recorded is not None
            and recorded[1] is existing
            and recorded[0] == str(source)
        ):
            return existing
    raise RuntimeError(
        f"conflicting preloaded physical sibling package: {module_name}"
    )


def load_physical_scripts_sibling(
    module_name: str,
    relative_path: str,
    *,
    package: bool = False,
    required: bool = True,
) -> ModuleType | None:
    """Load one exact module/package beside physical ``dispatch_v2``.

    ``relative_path`` is resolved below the physical parent of the package,
    never below ``SCRIPTS_DIR`` or another environment-selected root.  The
    reuse/conflict decision always goes through the single owner; the stub
    ``None`` is returned only for a truly empty, conflict-free optional
    request.
    """
    relative = Path(relative_path)
    if (
        not module_name
        or relative.is_absolute()
        or not relative.parts
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise RuntimeError("invalid physical sibling import request")

    parent_dir = _attested_package_parent()
    target = parent_dir / relative
    source = target / "__init__.py" if package else target

    # TOMBSTONE load-window OD PRZED ODCZYTEM ŹRÓDŁA Z DYSKU (Candidate62/Sol C61
    # #okno-pre-lock): oznacz root ZANIM ruszy ODCZYT Z DYSKU — otwarcie
    # (`_open_physical_source_fd`) ORAZ pełny read bajtów (`_read_all_from_fd` w
    # budowie niżej) — i zdejmij DOPIERO w `finally` obejmującym CAŁY blok load —
    # na KAŻDEJ ścieżce wyjścia (sukces, wyjątek, early-return `required=False→None`,
    # supersede-raise). C61 stawiał tombstone dopiero w sekcji krytycznej
    # `with _PHYSICAL_LOAD_LOCK` (PO odczycie), więc okno PRE-LOCK szerokiego odczytu
    # z dysku było otwarte: równoległy `import <root>` nazwy KOLIDUJĄCEJ z zatrutym
    # `sys.path` (root jeszcze nie w `sys.modules`) schodził w finderze do `None` →
    # `PathFinder` i WYKONYWAŁ obce `__init__.py` (świeży blind Sol/max, POTWIERDZONY:
    # foreign_loaded_during_prelock_read=True, foreign_source_executed=True,
    # tombstone_present=False). Ten OUTER mark jest KANONICZNYM ownerem tombstone'u
    # load-window; sekcja krytyczna niżej marku­je jeszcze raz WYŁĄCZNIE jako
    # zagnieżdżony składnik licznika (bilansowany w jej `finally`) — tombstone i tak
    # trzyma outer aż do wyjścia z bloku, także PRZEZ `exec`. Mutacja licznika
    # (get→+1→set) biegnie pod `_PHYSICAL_LOAD_LOCK` TYLKO na czas mark / unmark
    # (atomowo wobec równoległego load-a tej samej nazwy i wewnętrznego retire, które
    # też komponuje licznik) — lock NIE jest trzymany podczas odczytu z dysku ani
    # `exec` (kontrakt: lock nie w I-O/exec). Finder czyta tombstone LOCK-FREE, więc
    # obecność markera nie wprowadza porządku blokad ani deadlocku. RESIDUAL: okno od
    # wejścia funkcji do tego mark obejmuje TYLKO walidację argumentów i
    # `_attested_package_parent` (pojedynczy stat WŁASNEGO `dispatch_v2.__file__`, nie
    # nazwy kolidującej) — floor nieporównanie węższy niż zamknięty tu szeroki read
    # źródła; brak pracy na nazwie atakującego.
    with _PHYSICAL_LOAD_LOCK:
        _mark_root_retiring(module_name)

    publish_started = False
    module = None
    _superseded = False  # nowsza generacja przejęła nazwę w trakcie exec (#B)
    _deferred_derefs: list = []  # obiekty modułów do deref POZA lockiem (#1)
    _pending_handle: "_RetainedDirFd | None" = None  # handle przed publikacją (#2)
    source_missing: OSError | None = None
    source_fd: int | None = None
    source_dir_fd: int | None = None
    try:
        try:
            source_fd, source_dir_fd = _open_physical_source_fd(
                parent_dir,
                relative,
                package=package,
            )
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as exc:
            source_missing = exc
        except OSError as exc:
            raise RuntimeError(
                "physical sibling component walk violates the attested "
                f"parent boundary: {module_name}"
            ) from exc

        # source_dir_fd (deskryptor atestowanego katalogu) jest przenoszony do
        # owned handle w trasie dla pakietu (własność += finalizer); w każdym
        # innym wyjściu source_dir_fd pozostaje ustawiony i finally go zamyka
        # (bez wycieku).
        #
        # TRANSAKCJA stanu PER-PAKIET pod JEDNYM rollbackiem: adopt + PUBLIKACJA
        # (trasa + finder + tożsamość sys.modules + owner-registry) biegną pod
        # `_PHYSICAL_LOAD_LOCK` jako atomowy CAS pod nazwę (dwa równoległe legalne
        # loady tej samej nazwy: pierwszy publikuje, drugi widzi preloaded i go
        # zwraca — nigdy konkurencyjne tożsamości). `exec __init__.py` biegnie POZA
        # lockiem (kod użytkownika, brak nowego porządku blokad z lockami importu).
        # Częściowa publikacja (np. `_ensure_descendant_finder_installed` padnie po
        # transferze fd) ORAZ błąd exec są wycofywane TĄ SAMĄ ścieżką rollbacku
        # (idempotentny retire/purge), pod warunkiem `publish_started` — dzięki
        # czemu konflikt-adopt (obcy moduł pod nazwą) NIE kasuje cudzego wpisu.
        # UWAGA (świadomie): singleton findera w `sys.meta_path` NIE jest usuwany
        # przy rollbacku — idempotentna, INERT infrastruktura (przy pustej mapie
        # tras `find_spec` zwraca None; usuwanie ryzykowne przy żywych trasach).
        # BUDOWA POZA lockiem (Candidate53/Sol C52): odczyt bajtów, spec,
        # module_from_spec i `compile()` NIE dotykają współdzielonego stanu i
        # NIE mogą biec pod `_PHYSICAL_LOAD_LOCK` — `compile()` emituje zdarzenie
        # audytu, którego hook to KOD UŻYTKOWNIKA i może re-entrować loader; pod
        # nie-reentrantnym lockiem dałoby to deadlock. Budujemy przed sekcją
        # krytyczną; jeśli adopt wykaże preload, gotowy obiekt jest odrzucany.
        code = None
        if source_fd is not None:
            source_bytes = _read_all_from_fd(source_fd)
            # `submodule_search_locations=[]` dla pakietu: __path__ jest PUSTE,
            # więc `PathFinder` NIGDY nie rozwiąże potomka z tekstowej ścieżki —
            # każdy `<root>.<child>` idzie WYŁĄCZNIE przez naszego findera
            # (rozwiązuje z utrzymanego deskryptora, nie z __path__); potomek
            # nierozpoznany = fail-closed.
            spec = importlib.util.spec_from_file_location(
                module_name,
                source,
                submodule_search_locations=([] if package else None),
            )
            if spec is None or spec.loader is None:
                raise RuntimeError(
                    f"cannot attest physical sibling package: {module_name}"
                )
            if package:
                # `spec_from_file_location([])` auto-uzupełnia pustą listę
                # katalogiem źródła; wymuszamy z powrotem PUSTE `__path__`.
                spec.submodule_search_locations = []
            module = importlib.util.module_from_spec(spec)
            code = compile(
                source_bytes, str(source), "exec", dont_inherit=True
            )
        try:
            with _PHYSICAL_LOAD_LOCK:
                # TOMBSTONE load-window JEST USTAWIONY NA WEJŚCIU FUNKCJI
                # (Candidate62/Sol C61 #okno-pre-lock): outer mark stoi już PRZED
                # odczytem z dysku i trzyma się w obejmującym `finally` CAŁEJ funkcji
                # (także przez `exec`). Ten INNER mark jest jego ZAGNIEŻDŻONYM
                # składnikiem — licznik `_ROOTS_UNDER_RETIREMENT` komponuje okna, więc
                # inner mark/unmark bilansuje się WEWNĄTRZ tej sekcji, a tombstone NIE
                # spada do zera dopóki nie zdejmie go outer `finally`; root pozostaje
                # oznaczony BEZ PRZERWY od wejścia funkcji, przez publikację, aż przez
                # `exec`. (Historycznie C59/C60: inner mark był JEDYNYM ownerem i
                # zdejmował tombstone PRZED `exec`; C62 podniósł owner do outer scope,
                # by zamknąć okno PRE-LOCK odczytu z dysku, a inner został jako spójny
                # nested składnik licznika — tombstone przez `exec` jest teraz strictly
                # safer: gdyby równoległy retire zdjął `sys.modules[root]` w exec,
                # samo-import roota fail-closuje zamiast wykonać obce bajty; normalny
                # samo-import i tak idzie z cache'u `sys.modules`, konsultowanego przed
                # finderami, więc tombstone go nie blokuje.) Tombstone dotyczy WYŁĄCZNIE
                # findera (fail-close po NAZWIE roota); `_adopt_or_reject_preloaded`
                # czyta `sys.modules` normalnie, więc semantyka reuse jest nietknięta.
                # Wewnętrzny `_retire_package_route` (re-registration niżej) też sam
                # mark/unmark — licznik komponuje WSZYSTKIE zagnieżdżenia.
                _mark_root_retiring(module_name)
                try:
                    # FAIL-CLOSED podczas inicjalizacji INNEGO wątku (Candidate59/Sol
                    # C58 #2): jeśli tę nazwę inicjalizuje właśnie inny wątek (moduł
                    # opublikowany w `sys.modules`, ale `exec __init__` jeszcze trwa),
                    # NIE wolno oddać częściowego modułu jako sukcesu. Marker niesie
                    # (ident, module) — blokujemy TYLKO gdy inicjalizująca generacja
                    # WCIĄŻ włada nazwą (`_current_generation_is`): marker po
                    # generacji już zretirowanej/zastąpionej jest STALE i go mijamy
                    # (inaczej blokowalibyśmy legalne przejęcie nazwy po external
                    # retire — gen1 w exec, gen2 ładowany na innym wątku). Re-entrancja
                    # z TEGO SAMEGO wątku (samo-import w `__init__`) przechodzi dalej
                    # do ownera — zwróci częściowy moduł zgodnie z semantyką cyklu
                    # importowego CPythona (i unika samo-deadlocku).
                    _initializer = _PHYSICAL_INITIALIZING.get(module_name)
                    if (
                        _initializer is not None
                        and _initializer[0] != threading.get_ident()
                        and _current_generation_is(module_name, _initializer[1])
                    ):
                        raise RuntimeError(
                            "physical sibling package is initializing concurrently "
                            f"on another thread: {module_name}"
                        )
                    preloaded = _adopt_or_reject_preloaded(module_name, source)
                    if preloaded is not None:
                        return preloaded
                    if source_fd is None:
                        if not required:
                            return None
                        raise ModuleNotFoundError(
                            f"trusted physical sibling unavailable: {module_name}"
                        ) from source_missing
                    # RE-REGISTRATION (Candidate53/Sol C52): kanonicznie retiruj
                    # ewentualną STARĄ trasę tej nazwy PRZED publikacją nowej —
                    # inaczej nadpisanie trasy przeciekłoby stary `_RetainedDirFd`
                    # i zostawiło stare wpisy descendant registry. Wywołanie jest
                    # bezwarunkowe i IDEMPOTENTNE (pop z domyślnym None gdy brak
                    # trasy), więc nie potrzeba odczytu tabeli tras w loaderze.
                    # Doomed moduły starej generacji trzymamy do wyjścia z sekcji
                    # krytycznej (deref `__del__` POZA lockiem — #1/Sol C54). Retire
                    # sam mark/unmark tombstone — licznik komponuje z markiem wejścia.
                    _deferred_derefs.extend(_retire_package_route(module_name))
                    # Od tego punktu WE własność stanu nazwy — rollback dozwolony.
                    publish_started = True
                    # MARKER INICJALIZACJI (Candidate59/Sol C58 #2) ustawiony PRZED
                    # exec i zdejmowany DOPIERO po jego sukcesie/rollbacku: dzięki
                    # temu równoległy load tej nazwy w oknie exec fail-closuje
                    # (patrz wyżej), zamiast zaadoptować częściowy moduł. Niesie
                    # (ident, module) tej generacji, więc czyszczenie jest
                    # generation-safe (zdejmuje TYLKO nasz wpis).
                    _PHYSICAL_INITIALIZING[module_name] = (
                        threading.get_ident(), module
                    )
                    # ROOT-CLAIM (Candidate51/Sol C50): `sys.modules[name]`
                    # ustawiane PRZED trasą/finderem/owner-registry (CPython
                    # sys.modules-first; równoległy `import <name>` dostaje NASZ
                    # obiekt). Potomki chronione pustym `__path__`. Owner-registry
                    # pod TYM SAMYM lockiem (atomowość adopt/publikacji → brak
                    # fałszywego konfliktu).
                    sys.modules[module_name] = module
                    if package:
                        # `_RetainedDirFd.__init__` infallible → handle ZAWSZE
                        # przejmuje source_dir_fd bez except-close; source_dir_fd =
                        # None, więc `finally` go nie tknie. `_pending_handle`
                        # czyni zamknięcie DETERMINISTYCZNYM (#2/Sol C54): jeśli
                        # budowa krotki lub zapis trasy padnie PO utworzeniu
                        # handle, rollback zamyka fd od razu (nie czeka na GC).
                        handle = _RetainedDirFd(source_dir_fd)
                        source_dir_fd = None
                        _pending_handle = handle
                        _PHYSICAL_PACKAGE_ROUTES[module_name] = (
                            str(parent_dir),
                            str(relative),
                            module,
                            handle,
                        )
                        _pending_handle = None  # trasa jest właścicielem handle
                        _ensure_descendant_finder_installed()
                    _LOADED_PHYSICAL_SIBLINGS[module_name] = (
                        str(source), module
                    )
                finally:
                    # Zdejmij INNER mark (nested składnik licznika) na KAŻDEJ ścieżce
                    # wyjścia z sekcji (reuse-return / source-None-return / #2-raise /
                    # publikacja / wyjątek). Tombstone NIE spada do zera — outer mark
                    # (z wejścia funkcji) trzyma go dalej, także przez `exec` POZA
                    # `with`. Licznik: ten unmark (−1) bilansuje inner mark (+1);
                    # ewentualny wewnętrzny retire już zbilansował własny mark/unmark.
                    _unmark_root_retiring(module_name)
            exec(code, module.__dict__)
            # Inicjalizacja ZAKOŃCZONA sukcesem: zdejmij marker DOPIERO teraz — od
            # tej chwili równoległy load tej nazwy zaadoptuje KOMPLETNY moduł.
            # GENERATION-SAFE: zdejmij tylko NASZ wpis (inna generacja mogła po
            # external retire przejąć nazwę i ustawić własny marker).
            # SUPERSEDED-CHECK (Candidate61/Sol C60 #B): `return module` był
            # BEZWARUNKOWY po exec. Gdy NASZA generacja tkwiła w `exec`, a
            # równoległy retire + gen2 przejął nazwę w rejestrach (`sys.modules`/
            # trasa/owner wskazują gen2), bezwarunkowy return oddawał callerowi
            # STARY (zretirowany) moduł, choć aktualną generacją jest gen2 (świeży
            # blind Sol/max na C60, POTWIERDZONY kodem). Odczytujemy `_superseded`
            # POD LOCKIEM (spójny odczyt owned rejestrów), RAZEM ze zdjęciem markera
            # inicjalizacji; RAISE dopiero PO wyjściu z inner try (niżej), by nie
            # wpaść w rollback except — gen2 poprawnie włada nazwą i NIE wolno jej
            # tknąć. Nasza generacja aktualna (normalny przypadek ORAZ samo-import
            # roota — serwowany z cache'u `sys.modules`, nie przez ten path) →
            # `_superseded=False` → return bez zmian.
            with _PHYSICAL_LOAD_LOCK:
                _existing_init = _PHYSICAL_INITIALIZING.get(module_name)
                if _existing_init is not None and _existing_init[1] is module:
                    _PHYSICAL_INITIALIZING.pop(module_name, None)
                _superseded = not _current_generation_is(module_name, module)
        except BaseException:
            if publish_started:
                with _PHYSICAL_LOAD_LOCK:
                    # ROLLBACK ZWIĄZANY Z GENERACJĄ (Candidate53/Sol C52):
                    # wycofuj TYLKO gdy nasza generacja jest WCIĄŻ opublikowana.
                    # Jeśli równoległa nowsza generacja podmieniła nazwę (i przy
                    # re-registration zretirowała naszą trasę), NIE wolno usunąć
                    # cudzej, poprawnie zarejestrowanej generacji.
                    if _current_generation_is(module_name, module):
                        if package:
                            _deferred_derefs.extend(
                                _retire_package_route(module_name)
                            )
                        else:
                            _deferred_derefs.extend(
                                _purge_owned_module_subtree(module_name)
                            )
                    # Marker inicjalizacji zdjęty na rollbacku — GENERATION-SAFE:
                    # tylko NASZ wpis. Jeśli po external retire nowsza generacja
                    # przejęła nazwę i ustawiła własny marker, NIE wolno go zdjąć
                    # (inaczej odblokowalibyśmy adopt jej częściowego modułu).
                    _existing_init = _PHYSICAL_INITIALIZING.get(module_name)
                    if _existing_init is not None and _existing_init[1] is module:
                        _PHYSICAL_INITIALIZING.pop(module_name, None)
            # Handle utworzony, ale trasa NIE opublikowana — zamknij fd
            # DETERMINISTYCZNIE (#2/Sol C54), ale PO rollbacku i QUIETLY: błąd
            # `close` (EINTR) NIE może przerwać handlera ani pominąć rollbacku
            # (Candidate56/Sol C55 #2 — wcześniej close przed rollbackiem gubił
            # sprzątanie częściowo opublikowanego modułu).
            if _pending_handle is not None:
                if _pending_handle.fd is not None:
                    _close_fd_quietly(_pending_handle.fd)
                    _pending_handle.fd = None
                _pending_handle = None
            raise
        # FAIL-CLOSED po supersede (Candidate61/Sol C60 #B): jeśli między
        # publikacją a tym zwrotem nowsza generacja przejęła nazwę, NIE zwracamy
        # stale modułu. Raise TU (poza inner try) trafia do outer `finally`
        # (zamknięcie fd + deref), a NIE do rollback-except — gen2 zostaje
        # nietknięta. `_superseded` jest zawsze przypisane, gdy tu docieramy:
        # ustawia je post-exec blok pod lockiem, wykonywany tylko gdy exec się
        # powiódł (na wyjątku exec idziemy do `except` → `raise`, pomijając ten
        # kod).
        if _superseded:
            raise RuntimeError(
                f"physical sibling superseded during load: {module_name}"
            )
        return module
    finally:
        # Zamknij OBA deskryptory NIEZALEŻNIE (Candidate53/Sol C52): wyjątek
        # z jednego `os.close` (np. EINTR) nie może pominąć drugiego ani
        # wypropagować się z udanego load-a. source_dir_fd = None gdy
        # przeniesiono do handle.
        if source_fd is not None:
            try:
                os.close(source_fd)
            except OSError:
                pass
        if source_dir_fd is not None:
            try:
                os.close(source_dir_fd)
            except OSError:
                pass
        # DEREF doomed modułów POZA lockiem (#1/Sol C54): `finally` biegnie po
        # wyjściu ze WSZYSTKICH `with _PHYSICAL_LOAD_LOCK`, więc `__del__`
        # (kod użytkownika) nie odpala pod lockiem — brak re-entrancy/deadlocku.
        del _deferred_derefs[:]
        # ZDEJMIJ tombstone load-window ustawiony na WEJŚCIU funkcji (Candidate62/
        # Sol C61): unmark na KAŻDEJ ścieżce wyjścia (sukces, wyjątek, early-return
        # `required=False→None`, supersede-raise). Wykonywany PO derefie doomed
        # modułów — deref (`__del__` = kod użytkownika) biegnie POZA lockiem, a
        # unmark bierze `_PHYSICAL_LOAD_LOCK` TYLKO na czas atomowej mutacji
        # licznika. W tym punkcie żaden `with _PHYSICAL_LOAD_LOCK` nie jest już
        # trzymany, więc krótkie ponowne wzięcie locka jest bezpieczne (lock
        # nie-reentrantny, ale nie jest wznawiany z wnętrza sekcji).
        with _PHYSICAL_LOAD_LOCK:
            _unmark_root_retiring(module_name)
