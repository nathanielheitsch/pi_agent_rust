#!/usr/bin/env bash
# scripts/verify_local.sh — the fork's authoritative local quality gate.
#
# Replaces `dsr quality --tool pi_agent_rust` for this fork. DSR is upstream
# (Dicklesworthstone) private release infrastructure: it lives outside the
# repository, points at the upstream GitHub repo, and drives builds through a
# remote RCH worker fleet that does not exist on this host, so four of its
# seven lanes refuse with exit 103. See bd-rch-dsr-remote-refusal-hcjgg.
#
# Every lane here is the same command the upstream recipe registered, minus the
# `rch exec` transport, so the evidence is equivalent — only executed locally.
#
# Usage:
#   scripts/verify_local.sh              # all lanes, fail-closed
#   scripts/verify_local.sh --fast       # skip the two slow lanes (full test, installer)
#   scripts/verify_local.sh --strict-clippy  # run clippy -D warnings unconditionally
#   scripts/verify_local.sh --json       # machine-readable per-lane result
#
# Notes:
#   * `--keep-going` enumerates whole-tree breakage in one pass instead of one
#     error at a time (AGENTS.md "Compiler and Test Checks").
#   * PI_* variables inherited from a parent Pi session are scrubbed: they leak
#     into lib tests and change which fixtures pass.
#   * RUST_MIN_STACK mirrors .cargo/config.toml [env] so the gate is also correct
#     when invoked from a shell that ignores that config.
#   * PI_PROVIDER_REPLAY_GIT_COMMIT mirrors the upstream recipe's substitution so
#     provider replay-lineage checks resolve an authoritative commit
#     (bd-0ngnq, tests/provider_streaming.rs).
#   * The toolchain pinned in rust-toolchain.toml is put first on PATH. Homebrew's
#     cargo/rustc shadow rustup's proxies on this host and are stable, which
#     rejects the `-Z threads=4` rustflags in .cargo/config.toml with "the option
#     `Z` is only accepted on the nightly compiler" — every compiler lane then
#     fails for a reason unrelated to the source.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# Resolve the pinned toolchain named by rust-toolchain.toml and prefer it over
# whatever cargo/rustc happen to be first on PATH.
CHANNEL=""
if [ -f rust-toolchain.toml ]; then
  CHANNEL="$(grep -m1 '^channel' rust-toolchain.toml | cut -d'"' -f2)"
fi
TOOLCHAIN_BIN=""
if [ -n "$CHANNEL" ]; then
  for candidate in "${HOME}/.rustup/toolchains/${CHANNEL}-aarch64-apple-darwin/bin" \
                   "${HOME}/.rustup/toolchains/${CHANNEL}/bin"; do
    if [ -d "$candidate" ]; then TOOLCHAIN_BIN="$candidate"; break; fi
  done
fi
if [ -n "$TOOLCHAIN_BIN" ]; then
  export PATH="$TOOLCHAIN_BIN:$PATH"
elif command -v rustup >/dev/null 2>&1; then
  printf '[gate] note: pinned toolchain %s not under ~/.rustup; using rustup resolution\n' "$CHANNEL" >&2
else
  printf '[gate] WARNING: no pinned toolchain and no rustup; using PATH cargo (%s), not the pinned nightly\n' \
    "$(command -v cargo || echo none)" >&2
fi

FAST=0
JSON=0
STRICT_CLIPPY=0
for arg in "$@"; do
  case "$arg" in
    --fast) FAST=1 ;;
    --json) JSON=1 ;;
    --strict-clippy) STRICT_CLIPPY=1 ;;
    -h | --help)
      sed -n '2,34p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      printf 'unknown argument: %s\n' "$arg" >&2
      exit 2
      ;;
  esac
done

# Deterministic environment: scrub inherited Pi session variables.
ENV=(env -u PI_MODEL -u PI_PROVIDER -u PI_CODING_AGENT -u PI_SESSION_FILE
     -u PI_SESSION_ID -u PI_MANAGED_INSTALL_ROOT -u PI_REASONING_LEVEL)
ENV+=(RUST_MIN_STACK=16777216 TMPDIR=/tmp CARGO_INCREMENTAL=0)
if COMMIT="$(git rev-parse HEAD 2>/dev/null)"; then
  ENV+=(PI_PROVIDER_REPLAY_GIT_COMMIT="$COMMIT")
fi

LANE_NAMES=()
LANE_EXITS=()
LANE_MS=()
FAILURES=0

record_lane() {
  LANE_NAMES+=("$1")
  LANE_EXITS+=("$2")
  LANE_MS+=("$3")
  [ "$2" -ne 0 ] && FAILURES=$((FAILURES + 1))
}

run_lane() {
  local name="$1"
  shift
  local started_ms ended_ms exit_code log
  started_ms=$(($(date +%s) * 1000))
  log="$(mktemp)"
  printf '[gate] %s\n' "$name"
  "${ENV[@]}" "$@" >"$log" 2>&1
  exit_code=$?
  ended_ms=$(($(date +%s) * 1000))
  if [ "$exit_code" -ne 0 ]; then
    printf '[gate]   ✗ %s (exit %d)\n' "$name" "$exit_code"
    tail -25 "$log"
  else
    printf '[gate]   ✓ %s\n' "$name"
  fi
  rm -f "$log"
  record_lane "$name" "$exit_code" "$((ended_ms - started_ms))"
}

# Clippy. `-- -D warnings` promotes every pedantic/nursery lint to an error, and
# this tree carries ~628 inherited warnings from the upstream toolchain pin
# (bd-clippy-baseline, recorded rather than silenced). Unconditionally strict
# clippy therefore fails on findings no patch introduced and discriminates
# nothing. The default lane fails on clippy ERRORS and on warnings that land on
# lines this checkout changed — the same shape as check_ubs_staged_delta.py.
# --strict-clippy runs the unforgiving form and reports what it admits.
run_clippy_lane() {
  local name="clippy (errors, plus warnings on changed lines)"
  local started_ms ended_ms exit_code log errlog lane_exit=0
  started_ms=$(($(date +%s) * 1000))
  log="$(mktemp)"
  errlog="$(mktemp)"
  printf '[gate] %s\n' "$name"
  # JSON messages carry per-diagnostic spans, which is what makes changed-line
  # attribution possible; the plain-text log has no machine-readable location.
  # `--keep-going` still enumerates the whole tree in one pass.
  if [ "$STRICT_CLIPPY" -eq 1 ]; then
    "${ENV[@]}" cargo clippy --locked --all-targets --keep-going --message-format=json -- -D warnings >"$log" 2>"$errlog"
  else
    "${ENV[@]}" cargo clippy --locked --all-targets --keep-going --message-format=json >"$log" 2>"$errlog"
  fi
  exit_code=$?
  ended_ms=$(($(date +%s) * 1000))

  if [ "$STRICT_CLIPPY" -eq 1 ]; then
    # -D warnings already decides the verdict; the attribution report is context.
    CLIPPY_LOG="$log" python3 scripts/check_clippy_changed_lines.py 2>&1 | sed 's/^/[gate]   /' | head -30 || true
    if [ "$exit_code" -ne 0 ]; then
      lane_exit=1
      printf '[gate]   ✗ clippy --strict (-D warnings) failed\n'
    else
      printf '[gate]   ✓ clippy --strict: 0 warnings\n'
    fi
  else
    if CLIPPY_LOG="$log" python3 scripts/check_clippy_changed_lines.py >"$errlog.clippy" 2>>"$errlog"; then
      sed 's/^/[gate]   /' "$errlog" | head -8
      printf '[gate]   ✓ clippy: no errors, no new warnings on changed lines\n'
    else
      lane_exit=1
      sed 's/^/[gate]   /' "$errlog" | head -8
      sed 's/^/[gate]   /' "$errlog.clippy" | head -40
      printf '[gate]   ✗ clippy: errors, or new warnings on changed lines\n'
    fi
  fi
  rm -f "$log" "$errlog" "$errlog.clippy"
  record_lane "$name" "$lane_exit" "$((ended_ms - started_ms))"
}

# 1-3. Formatting, compiler, and lint gates.
run_lane "cargo fmt --check" cargo fmt --check
run_lane "cargo check --locked --all-targets" \
  cargo check --locked --all-targets --keep-going --message-format short
run_clippy_lane

# 4. Full test suite.
if [ "$FAST" -eq 0 ]; then
  run_lane "cargo test --locked --all-targets" \
    cargo test --locked --all-targets --no-fail-fast
fi

# 5-7. Repository gates that caught real defects and must stay mandatory.
run_lane "module reachability" python3 scripts/check_module_reachability.py
run_lane "fixture read patience" python3 scripts/check_fixture_read_patience.py
if [ "$FAST" -eq 0 ]; then
  run_lane "installer regression" bash tests/installer_regression.sh
fi

if [ "$JSON" -eq 1 ]; then
  printf '{"source":"%s","failures":%d,"lanes":[' "$(git rev-parse HEAD 2>/dev/null)" "$FAILURES"
  for i in "${!LANE_NAMES[@]}"; do
    [ "$i" -gt 0 ] && printf ','
    printf '{"name":%s,"exit":%d,"elapsed_ms":%d}' \
      "\"${LANE_NAMES[$i]}\"" "${LANE_EXITS[$i]}" "${LANE_MS[$i]}"
  done
  printf ']}\n'
fi

if [ "$FAILURES" -ne 0 ]; then
  printf '[gate] FAILED: %d lane(s) of %d\n' "$FAILURES" "${#LANE_NAMES[@]}" >&2
  exit 1
fi
printf '[gate] all %d lanes passed\n' "${#LANE_NAMES[@]}"
