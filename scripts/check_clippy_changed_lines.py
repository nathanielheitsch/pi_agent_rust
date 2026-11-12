#!/usr/bin/env python3
"""Reduce a clippy JSON log to what this checkout is actually responsible for.

`cargo clippy --all-targets -- -D warnings` promotes every pedantic/nursery lint
to an error. This fork inherited that configuration along with the ~628
warnings the upstream toolchain pin produced (bd-clippy-baseline: the pin at
e403485b3 strengthened the lint groups without touching source, and the owner
chose a documented crate-wide `allow` over 78 per-module suppressions). Running
unconditionally strict clippy here therefore fails on findings no patch
introduced, which makes it useless as a gate — it says the same thing whether or
not your change is clean.

So this gate keeps clippy in the loop and reduces its result to attribution, the
same way scripts/check_ubs_staged_delta.py does for UBS:

  * clippy/rustc ERRORS always fail. They are compile-level breakage and no
    baseline argument applies to them.
  * warnings are counted and reported to stderr as inventory, but only a warning
    that lands on a line this checkout changed fails the lane. That is what
    "do not add new lint debt" means operationally.
  * diagnostics whose span is outside the tree (a dependency, a build script)
    are counted as inventory only and never attributed to a patch.

Line attribution is positional, so reformatting a file moves its existing
backlog onto "changed" lines. scripts/check_ubs_staged_delta.py waives exactly
that case (bd-ubs-delta-vs-fmt-conflict) by comparing the staged content against
rustfmt applied to the HEAD blob. This gate does the same check, so a `cargo fmt`
repair does not inherit a module's whole lint backlog.

Usage:
  cargo clippy --locked --all-targets --keep-going --message-format=json >log.json
  CLIPPY_LOG=log.json python3 scripts/check_clippy_changed_lines.py

Exit 0 with empty stdout means no errors and no new warnings on changed lines.
Exit 1 with findings on stdout means the patch owes work. Stats go to stderr,
always, so the inherited count is visible rather than silently tolerated.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Diagnostic:
    level: str
    lint: str
    path: str
    line: int
    summary: str


def git(*args: str) -> str:
    """Run git in the repo, returning stdout. Failure means 'no diff', not an error."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def base_ref() -> str:
    """Diff against HEAD when the tree carries work; against HEAD~1 when it is clean.

    `git diff HEAD` covers staged and unstaged changes in one pass, which is what
    a lane running before a commit needs. When nothing differs from HEAD the gate
    still has to attribute something, so the most recent commit is the subject.
    """
    if git("diff", "--name-only", "HEAD").strip():
        return "HEAD"
    return "HEAD~1"


HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines(ref: str) -> dict[str, set[int]]:
    """Added/modified line numbers per file, from a context-free unified diff."""
    diff = git("diff", "--no-color", "-U0", ref)
    changed: dict[str, set[int]] = {}
    path: str | None = None
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            path = None if target == "/dev/null" else _strip_prefix(target)
        elif raw.startswith("@@") and path is not None:
            match = HUNK_RE.match(raw)
            if not match:
                continue
            start = int(match.group(1))
            count = int(match.group(2) or 1)
            bucket = changed.setdefault(path, set())
            bucket.update(range(start, start + count))
    return changed


def _strip_prefix(target: str) -> str | None:
    if target.startswith("a/"):
        target = target[2:]
    elif target.startswith("b/"):
        target = target[2:]
    return target if target else None


HEAD_SUFFIX_RE = re.compile(r"^HEAD(\d+)$")


def line_numbers(message: dict) -> list[tuple[str, int]]:
    """Tree-relative (path, line) pairs for one compiler message.

    Cargo's JSON spans carry `line_start`/`line_end` (and byte offsets), not a
    `line_number` field; only the primary span of a clippy diagnostic names the
    offending line, so non-primary spans are skipped rather than widened to the
    whole `line_start..line_end` range.
    """
    spots: list[tuple[str, int]] = []
    for span in message.get("spans") or []:
        if not span.get("is_primary", True):
            continue
        line = span.get("line_start")
        if not isinstance(line, int):
            continue
        resolved = _relative(span.get("file_name", ""))
        if resolved is not None:
            spots.append((resolved, line))
    return spots


def _relative(file_name: str) -> str | None:
    """Keep only paths inside this checkout; a dependency's path is not ours to fix."""
    if file_name.startswith("/"):
        try:
            return str(Path(file_name).resolve().relative_to(ROOT))
        except ValueError:
            return None
    if file_name.startswith("dependencies/") or file_name.startswith("registry/"):
        return None
    # `<target>/build/...` and macro-expanded paths are not source lines.
    if file_name.startswith("target/") or "/" in file_name and file_name.split("/")[0] == "target":
        return None
    if (ROOT / file_name).exists():
        return file_name
    return None


def content_is_pure_rustfmt(path: str) -> bool:
    """True when the working content is exactly rustfmt over the HEAD blob.

    rustfmt moves lines without changing code, so position-based attribution
    would blame a reformat for the module's whole lint backlog. Checking that the
    formatting output and the current file are byte-identical makes the premise
    exact rather than approximate.
    """
    head = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"HEAD:{path}"],
        capture_output=True,
    )
    if head.returncode != 0:
        return False
    formatted = subprocess.run(
        ["rustfmt", "--edition", "2024", "--stdout"],
        input=head.stdout,
        capture_output=True,
    )
    if formatted.returncode != 0:
        return False
    try:
        current = (ROOT / path).read_bytes()
    except OSError:
        return False
    return formatted.stdout == current


LINT_RE = re.compile(r"clippy::([a-z0-9_]+)")


def parse(log_path: Path) -> tuple[list[Diagnostic], int, int]:
    diagnostics: list[Diagnostic] = []
    seen: set[tuple[str, str, int]] = set()
    unparsed = 0
    with log_path.open() as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                # cargo emits non-JSON progress lines alongside the messages.
                unparsed += 1
                continue
            if record.get("reason") != "compiler-message":
                continue
            message = record.get("message") or {}
            level = message.get("level", "warning")
            code = message.get("code") or {}
            lint = str(code.get("code") or "")
            match = LINT_RE.search(lint)
            lint = match.group(1) if match else lint or "rustc"
            summary = (message.get("message") or "").strip()
            for path, line in line_numbers(message):
                key = (path, lint, line)
                if key in seen:
                    continue
                seen.add(key)
                diagnostics.append(Diagnostic(level, lint, path, line, summary))
    return diagnostics, unparsed, len(seen)


def main() -> int:
    log_path = os.environ.get("CLIPPY_LOG")
    if not log_path:
        print("CLIPPY_LOG must point at a `cargo clippy --message-format=json` log", file=sys.stderr)
        return 2
    log = Path(log_path)
    if not log.is_file():
        print(f"CLIPPY_LOG={log_path} is not a readable file", file=sys.stderr)
        return 2

    diagnostics, unparsed, _ = parse(log)
    errors = [d for d in diagnostics if d.level == "error"]
    warnings = [d for d in diagnostics if d.level != "error"]

    ref = base_ref()
    changed = changed_lines(ref)
    waived_files: set[str] = set()

    def on_changed(d: Diagnostic) -> bool:
        if d.path not in changed:
            return False
        if d.path not in waived_files:
            if content_is_pure_rustfmt(d.path):
                waived_files.add(d.path)
        if d.path in waived_files:
            return False
        return d.line in changed[d.path]

    offenders = [d for d in warnings if on_changed(d)]

    print(
        f"clippy attribution vs {ref}: {len(errors)} error(s), {len(warnings)} warning(s) "
        f"across {len(changed)} changed file(s); "
        f"{len(waived_files)} file(s) waived as pure rustfmt reformatting"
        + (f"; {unparsed} non-JSON log line(s)" if unparsed else ""),
        file=sys.stderr,
    )

    if errors:
        print(f"clippy/rustc ERRORS ({len(errors)}):")
        for d in errors[:40]:
            print(f"  {d.path}:{d.line}: {d.lint}: {d.summary}")
        if len(errors) > 40:
            print(f"  ... and {len(errors) - 40} more")

    if offenders:
        print(f"NEW clippy warnings on changed lines ({len(offenders)}):")
        for d in offenders[:40]:
            print(f"  {d.path}:{d.line}: clippy::{d.lint}: {d.summary}")
        if len(offenders) > 40:
            print(f"  ... and {len(offenders) - 40} more")

    return 1 if (errors or offenders) else 0


if __name__ == "__main__":
    sys.exit(main())
