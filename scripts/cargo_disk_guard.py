#!/usr/bin/env python3
"""Supervise local builds without deleting artifacts (Python stdlib only).

Used as Cargo's rustc-wrapper and around the entire local quality gate. The
outer supervisor also covers build scripts, tests and other non-rustc writers.
Usage: python3 scripts/cargo_disk_guard.py -- <command> [args...]
"""

import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

GIB = 1024 ** 3
MIN_FREE = 32 * GIB
MAX_CACHE = 64 * GIB
INTERVAL = 2.0
CACHE_INTERVAL = 10.0
STOP_GRACE = 3.0
REFUSED = 75
ROOT = Path(__file__).resolve().parent.parent


class DiskPressure(Exception):
    pass


def process_table():
    """PID identity plus ancestry, without exposing command lines or secrets."""
    output = subprocess.check_output(
        ["ps", "-axo", "pid=,ppid=,pgid=,lstart="], text=True,
        env=dict(os.environ, LC_ALL="C"),
    )
    table = {}
    for line in output.splitlines():
        fields = line.split(maxsplit=3)
        if len(fields) == 4:
            table[int(fields[0])] = (int(fields[1]), int(fields[2]), fields[3])
    return table


def supervisor_roots():
    """Trust a marker only when its live, same-identity owner is an ancestor."""
    marker = os.environ.get("PI_CARGO_DISK_GUARD_PID", "")
    if os.name != "posix" or not marker.isdecimal() or int(marker) <= 1:
        return None
    owner = int(marker)
    table = process_table()
    record = table.get(owner)
    if not record or record[2] != os.environ.get("PI_CARGO_DISK_GUARD_START"):
        return None
    ancestor = os.getppid()
    seen = set()
    while ancestor in table and ancestor not in seen:
        if ancestor == owner:
            try:
                roots = json.loads(os.environ.get("PI_CARGO_DISK_GUARD_ROOTS", ""))
                if not isinstance(roots, list) or not roots:
                    return None
                if not all(isinstance(p, str) and Path(p).is_absolute() for p in roots):
                    return None
                return [Path(p).resolve() for p in roots]
            except ValueError:
                return None
        seen.add(ancestor)
        ancestor = table[ancestor][0]
    return None


def flag_value(command, flag):
    for index, arg in enumerate(command):
        if arg == flag and index + 1 < len(command):
            return command[index + 1]
        if arg.startswith(flag + "="):
            return arg[len(flag) + 1:]
    return None


def cache_roots(command):
    """Include redirected outputs as well as this checkout's existing cache."""
    roots = [ROOT / "target"]
    for name in ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR", "CARGO_BUILD_BUILD_DIR"):
        if os.environ.get(name):
            roots.append(Path(os.environ[name]).resolve())
    target = flag_value(command, "--target-dir")
    if target:
        roots.append(Path(target).resolve())
    output = flag_value(command, "--out-dir")
    if output:
        path = Path(output).resolve()
        # Match layout positions, not arbitrary ancestors named 'build'.
        if path.name in ("deps", "examples"):
            roots.append(path.parent)
        elif path.name == "out" and len(path.parents) >= 4 and path.parents[2].name == "build":
            roots.append(path.parents[3])  # Cargo 1.100 build/<crate>/<hash>/out
        elif path.name == "out" and len(path.parents) >= 3 and path.parents[1].name == "build":
            roots.append(path.parents[2])  # older build/<crate>-<hash>/out
        elif path.parent.name == "build":
            roots.append(path.parent.parent)  # older build-script executable
        else:
            roots.append(path)
    unique = sorted(set(p.resolve() for p in roots), key=lambda p: len(p.parts))
    return [p for p in unique if not any(q in p.parents for q in unique)]


def existing_parent(path):
    while not path.exists():
        parent = path.parent
        if parent == path:
            raise OSError(f"no existing ancestor for {path}")
        path = parent
    return path


def allocated_bytes(roots):
    """Count allocated blocks once per inode; never follow internal symlinks."""
    total = 0
    seen = set()
    pending = list(roots)
    while pending:
        path = pending.pop()
        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                continue
            inode = (info.st_dev, info.st_ino)
            if inode in seen:
                continue
            seen.add(inode)
            total += getattr(info, "st_blocks", (info.st_size + 511) // 512) * 512
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as entries:
                    pending.extend(Path(entry.path) for entry in entries)
        except FileNotFoundError:
            # Cargo removes/replaces intermediates during a live scan.
            continue
        if total >= MAX_CACHE:
            break
    return total


def require_covered_output(path, known_roots):
    if not any(path == root or root in path.parents for root in known_roots):
        raise DiskPressure(
            f"unregistered output root {path}; set CARGO_TARGET_DIR or "
            "CARGO_BUILD_BUILD_DIR before launching the guarded command"
        )
    try:
        devices = json.loads(os.environ.get("PI_CARGO_DISK_GUARD_DEVICES", ""))
    except ValueError as error:
        raise DiskPressure("missing monitored filesystem identities") from error
    if not isinstance(devices, list) or not all(type(dev) is int for dev in devices):
        raise DiskPressure("invalid monitored filesystem identities")
    if existing_parent(path).stat().st_dev not in devices:
        raise DiskPressure(f"{path}: output filesystem is not continuously monitored")


class Storage:
    def __init__(self, command):
        self.roots = cache_roots(command)
        self.paths = self.roots + [Path(tempfile.gettempdir()), Path.cwd()]
        self.next_cache_check = 0.0

    def check(self, scan_cache=True):
        for path in self.paths:
            free = shutil.disk_usage(existing_parent(path)).free
            if free < MIN_FREE:
                raise DiskPressure(
                    f"{path}: {free / GIB:.1f} GiB free; reserve is {MIN_FREE / GIB:.0f} GiB"
                )
        now = time.monotonic()
        if scan_cache and now >= self.next_cache_check:
            used = allocated_bytes(self.roots)
            if used >= MAX_CACHE:
                raise DiskPressure(
                    f"build caches use at least {used / GIB:.1f} GiB; "
                    f"budget is {MAX_CACHE / GIB:.0f} GiB ({', '.join(map(str, self.roots))})"
                )
            self.next_cache_check = now + CACHE_INTERVAL


def stop_group(child):
    """Stop our session and observed descendants, including separate groups."""
    if os.name != "posix":
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        child.wait()
        return

    identity = getattr(child, "_guard_identity", None)
    if identity is None:
        # Launch-time identity capture failed before wait/poll could reap this
        # child. Popen.kill checks its own child state; never guess a group.
        child.kill()
        child.wait()
        return
    tracked = {child.pid: identity}

    def refresh():
        table = process_table()
        owned = {pid for pid, start in tracked.items()
                 if pid in table and table[pid][2] == start}
        root_matches = child.pid not in table or table[child.pid][2] == identity
        while True:
            more = {pid for pid, row in table.items() if row[0] in owned} - owned
            if not more:
                break
            owned.update(more)
        for pid, row in table.items():
            try:
                in_session = root_matches and os.getsid(pid) == child.pid
            except ProcessLookupError:
                continue
            if pid in owned or in_session:
                tracked.setdefault(pid, row[2])
        # A saved PID is only signalled while its start identity still matches.
        return {pid for pid, identity in tracked.items()
                if pid in table and table[pid][2] == identity}

    def send(pids, signum):
        for pid in sorted(pids, key=lambda pid: pid == child.pid):
            try:
                os.kill(pid, signum)
            except ProcessLookupError:
                pass

    try:
        send(refresh(), signal.SIGTERM)
        deadline = time.monotonic() + STOP_GRACE
        while time.monotonic() < deadline:
            child.poll()
            alive = refresh()
            if not alive:
                break
            time.sleep(0.1)
        else:
            send(refresh(), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        # Without enumeration we cannot prove group ownership after PID reuse.
        # Only Popen's still-owned direct child is safe to stop.
        child.kill()
    child.wait()


def supervise(command, storage=None):
    storage = storage or Storage(command)
    try:
        storage.check()
    except (OSError, DiskPressure) as error:
        print(f"[cargo-disk] REFUSED: {error}. No files deleted.", file=sys.stderr)
        return REFUSED

    interrupted = []
    previous = {}

    def on_signal(signum, _frame):
        interrupted.append(signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, on_signal)
    child = None
    try:
        env = dict(os.environ, PI_CARGO_DISK_GUARD_PID=str(os.getpid()))
        if os.name == "posix":
            env["PI_CARGO_DISK_GUARD_START"] = process_table()[os.getpid()][2]
            env["PI_CARGO_DISK_GUARD_ROOTS"] = json.dumps([str(p) for p in cache_roots(command)])
            paths = cache_roots(command) + [Path(tempfile.gettempdir()), Path.cwd()]
            env["PI_CARGO_DISK_GUARD_DEVICES"] = json.dumps(
                sorted({existing_parent(p).stat().st_dev for p in paths})
            )
            # Install the Unix wrapper only inside an already guarded command;
            # native Windows Cargo remains free of shebang-only configuration.
            wrapper = str(Path(__file__).resolve())
            if env.get("RUSTC_WRAPPER") not in (None, "", wrapper):
                raise DiskPressure("custom RUSTC_WRAPPER cannot be monitored safely; refusing to bypass it")
            env["RUSTC_WRAPPER"] = wrapper
        # Preserve Cargo's inheritable jobserver descriptors through the wrapper.
        child = subprocess.Popen(command, env=env, close_fds=False,
                                 start_new_session=(os.name == "posix"))
        child._guard_identity = None
        if os.name == "posix":
            # Capture before wait/poll can reap even a very short-lived child.
            child._guard_identity = process_table()[child.pid][2]
        while True:
            if interrupted:
                stop_group(child)
                return 128 + interrupted[0]
            try:
                code = child.wait(timeout=INTERVAL)
            except subprocess.TimeoutExpired:
                storage.check()
                continue
            storage.check()
            return code if code >= 0 else 128 - code
    except (OSError, DiskPressure, subprocess.SubprocessError, KeyError) as error:
        if child is not None:
            stop_group(child)
        print(f"[cargo-disk] STOPPED: {error}. No files deleted.", file=sys.stderr)
        return REFUSED
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main():
    command = sys.argv[1:]
    try:
        known_roots = supervisor_roots()
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[cargo-disk] REFUSED: cannot verify supervisor: {error}", file=sys.stderr)
        return REFUSED
    if command == ["--is-supervised"]:
        return 0 if known_roots is not None else 1
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print(__doc__, file=sys.stderr)
        return 2
    if Path(command[0]).stem == "rustc" and not flag_value(command, "--out-dir"):
        return subprocess.call(command, close_fds=False)
    if known_roots is not None:
        # Stay in the outer supervised session. New output roots are not silently
        # accepted: the outer monitor could not sample them during this command.
        try:
            output = flag_value(command, "--out-dir")
            if output:
                # Coalescing cache roots must not hide a nested output mount.
                require_covered_output(Path(output).resolve(), known_roots)
            for path in cache_roots(command):
                require_covered_output(path, known_roots)
            Storage(command).check(scan_cache=False)
        except (OSError, DiskPressure) as error:
            print(f"[cargo-disk] REFUSED: {error}. No files deleted.", file=sys.stderr)
            return REFUSED
        os.execvp(command[0], command)
    # Invalid/stale markers never bypass monitoring or survive into re-entry.
    for name in ("PI_CARGO_DISK_GUARD_PID", "PI_CARGO_DISK_GUARD_START",
                 "PI_CARGO_DISK_GUARD_ROOTS", "PI_CARGO_DISK_GUARD_DEVICES"):
        os.environ.pop(name, None)
    return supervise(command)


if __name__ == "__main__":
    sys.exit(main())
