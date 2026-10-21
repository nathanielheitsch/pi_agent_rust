# IMPLEMENTATION PLAN — port all 15 TS pi extensions to native Rust

Scope: every extension in `docs/fork/extension-inventory.md:14-29` (15 packages) plus the
ponytail skill (`:29`) is ported. Nothing is dropped and nothing stays as bundled TS for
its own sake: the verdict column of `docs/fork/README.md:25-42` (A 6 · B 5 · C 5) is
re-read here as **where the behaviour lands in Rust**, not as keep-or-delete. C rows are
re-scored: they were "C" because the QuickJS bridge *could host* them; under a
port-everything mandate that hosting is a stepping stone, so each C row gets a named Rust
target and the TS is retired once the Rust lands.

Evidence discipline: every claim below carries a `file:line` anchor resolved in **this**
checkout. Anchors that the reconnaissance lanes could not resolve are marked
**UNVERIFIED** and must be re-checked before being used as an implementation target.
See `docs/fork/phase-0-native-gaps.md` for the expanded gap table.

---

## 1. Subsystem grouping (five subsystems, not fifteen ports)

| # | Subsystem | Extensions folded in | Rust substrate (verified) | Verdict |
|---|---|---|---|---|
| S1 | **context-management** | pi-fold, pi-distill (+ its i18n/tool-display render deps) | `pub mod compaction` `src/lib.rs:123`, `compaction_snap:125`, `compaction_worker:127`; session tree `src/session.rs` (`pub mod session` `src/lib.rs:303`), `src/session_index.rs:26` | gap-close, medium |
| S2 | **orchestration** | pi-crew, pi-subagents, pi-advisor-flow | `pub mod subagents` `src/lib.rs:333`, `pub mod advisor` `:90`, `pub mod agent_hub` `:96`, `pub mod hub` `:225`, `src/worktree_iso.rs` (`lib.rs:371`), `src/validation_broker.rs` (`:361`) | new modules + tier coupling |
| S3 | **peer coordination** | pi-cross-session | nothing exists — zero `AF_UNIX`/unix-socket code in `src/` (grep zero); nearest neighbour `src/session.rs:303`, `src/session_index.rs:308` | hard new port (blocker documented) |
| S4 | **provider / model / presentation layer** | pi-model-request, pi-extensions-i18n, pi-extensions-tool-display | `pub mod auth` `src/lib.rs:107`, `pub mod secrets` `:295`, `src/providers/`, settings merge `src/config.rs:741`, `src/tui.rs`, `src/interactive_ftui.rs:7979` | settings + renderer layer |
| S5 | **already-native gap-closes** | pi-hashline-edit, pi-plan-mode, pi-ast-grep, pi-web-access, pi-agent-browser-native, pi-mcp-adapter | see `docs/fork/phase-0-native-gaps.md` rows | small fixes, high risk-if-skipped |

One-line verdicts (what each subsystem must *do*, not what it is):

- **S1** — do not add a JS fold pass. `src/compaction.rs:148`/`196`/`207`
  (`ResolvedCompactionSettings`, `CompactionDetails`, `CompactionResult`) already model
  compaction; pi-fold's contract ("lossless, only oldest context changes",
  `pi-fold/extensions/settings.ts:664` `fold-settings` command) is a *policy* on top of
  that pipeline. Land fold as a settings band + fold-mode selection inside the compactor,
  and land distill as an entry type on the session tree, not as a renderer shim.
- **S2** — the three orchestration extensions collapse into one native role model.
  `src/advisor.rs:26` `VerdictLevel` / `:68` `AdvisorVerdict` / `:75` `TurnDigest` is
  already the advisor side; `src/subagents.rs` is the delegation side. Missing pieces:
  pi-crew's team/work-graph/lease machinery (no Rust equivalent — `src/agent_hub.rs:96`
  and `src/hub.rs:225` are adjacent, not equivalent), and pi-subagents' tool-grant
  semantics (`pi-subagents/src/shared/extension-context.js:7` `MODEL_ONLY_TOOL = { exposure:
  "model-only" }`, consumed at `pi-subagents/src/runs/shared/mcp-direct-tool-allowlist.js:96`; the
  bridge drops it — `grep -c exposure` is 0 in `src/extensions_js.rs`, `src/tools.rs`,
  `src/extensions.rs`). `src/xdev.rs:82` `OPT_IN_ONLY = ["subagent"]` is the only opt-in tool today;
  council roles become roles inside that subsystem, not new JS tools.
- **S3** — the only honest outcome is a Rust peer channel. `docs/extension-compatibility-matrix.md:29`
  records `node:net` as **Stub** with `createServer` throwing; `:254` says server listeners
  are "Not applicable" and `:255` QuickJS is single-threaded. A Rust
  `session-to-session` channel over a per-instance socket, keyed by
  `SessionMeta{path,id,cwd,...}` (`src/session_index.rs:26-35`), replaces
  `list_pi`/`send_pi_message`/`peer_goal`.
- **S4** — these three are one layer, not three: auth/model resolution
  (`src/auth.rs`, `src/secrets`), the settings surface, and result rendering.
  i18n is a catalog concern of that layer, not an extension; tool-display's job "belongs
  to the Rust renderers" (`docs/fork/README.md:35`). pi-model-request's
  "resolve auth, add provider session headers, call completion"
  (`docs/fork/extension-inventory.md:53`) is exactly what `src/auth.rs:107` +
  `src/providers/` do natively.
- **S5** — six rows are already native; the work is the gap list in
  `docs/fork/phase-0-native-gaps.md`, ordered by risk-if-skipped descending.

Coupling note preserved from `docs/fork/README.md:44-48`: pi-distill declares
i18n + tool-display as siblings (`docs/fork/extension-inventory.md:15`), so under this
plan the native renderer (S4) must land **before** the distill entry renderer (S1), and
the `pi.extensions` sibling path is deleted rather than re-tested.

---

## 2. Caveats (one line each, with evidence)

1. `node:net` is a Stub and `createServer` throws — any IPC/peer/socket extension is unhostable, forcing a Rust transport. Evidence: `docs/extension-compatibility-matrix.md:29`.
2. `linkedom` `parseHTML()` is a DOM-parser stub — pi-web-access's DOM-selector paths cannot run on the bridge. Evidence: `docs/extension-compatibility-matrix.md:157`; the TS side imports it at `pi-web-access/extract.ts:85` and `:1471`, `pi-web-access/duckduckgo.ts:72`.
3. `adm-zip` is a zip-handling stub — archive paths need a Rust equivalent. Evidence: `docs/extension-compatibility-matrix.md:156`.
4. `@napi-rs/keyring` is a native addon and can never load in QuickJS; MCP OAuth storage must move to the Rust secrets layer. Evidence: `pi-mcp-adapter/package.json` dependency list; Rust anchors `pub mod secrets` `src/lib.rs:295`, `pub mod auth` `:107` — **UNVERIFIED** whether either already stores MCP OAuth tokens (grep `oauth`/`keyring` in `src/mcp.rs` returned zero).
5. Capability policy is a three-value profile, not per-extension grants: `PolicyProfile::Safe|Standard|Permissive` → `"safe"|"balanced"|"permissive"` at `src/config.rs:731`. Any ported feature that needs a hostcall must state its profile or it fails at runtime, not at build.
6. The extension bridge forwards exactly five tool-spec keys — `name`, `description`, `parameters`, `label`, `execute` — built at `src/extensions_js.rs:21559-21566` (validator `__pi_register_tool` `:21546-21578`). `promptSnippet`/`promptGuidelines` (`pi-ast-grep/src/index.ts:17`/`:18`) and `exposure` (`pi-subagents/src/shared/extension-context.js:7`) are dropped — grep for all three across `src/` returned zero. Every port must re-home these into Rust tool metadata.
7. Tier/registry coupling is enforced by tests, not by review: `src/xdev.rs:390-414` (`every_tiered_name_is_a_name_the_tools_flag_knows_about`) plus the registry test around `src/tools.rs:25063-25085`. A new tool name must appear in `ESSENTIAL_DEFAULTS`/`OPT_IN_ONLY` (`src/xdev.rs:59-82`) **and** `KNOWN_TOOL_NAMES` (`src/tools.rs:5694`) **or** `TOOLS_NOT_SELECTED_BY_FLAG`, **and** get a registry arm (`src/tools.rs:5822` pattern).
8. `recursion_limit` is per crate and set to 256 in `src/lib.rs:33` — it is **not** inherited by the binary, examples, or integration tests. Deep async chains in S1/S2/S3 (asupersync futures) will hit it per crate; raise it in each affected crate, not just `lib.rs`.
9. `#![forbid(unsafe_code)]` `src/lib.rs:20`: no FFI shortcut for keyring, DOM, or socket plumbing; everything must be pure Rust or a dependency crate.
10. Session format is JSONL v3 with line validation/limits at `src/session.rs:131-226`. New entry types (distill audit, fold markers, peer messages) must pass that validator; a new entry type is a session-format change, and old sessions carrying unknown types must fail closed rather than be dropped silently.
11. Surface split: the TUI is `src/interactive_ftui.rs` (default, `ftui` feature) and `src/interactive.rs` (`--classic`), while RPC/stdin is `src/rpc.rs`. Extension UI goes through `ui_sender: Option<mpsc::Sender<ExtensionUiRequest>>` (`src/extensions.rs:20878`) and **fails closed when unset** (`src/extensions.rs:20880`). Any ported UI (pi-crew panels, pi-fold bars, pi-extensions-tool-display) needs an explicit RPC-surface behaviour, otherwise it silently no-ops in `--mode rpc`.
12. `pi-plan-mode` pins an older namespace (`@mariozechner/pi-coding-agent ^0.63.0`, `docs/fork/extension-inventory.md:61-62`) — its behaviour predates the current host, so its Rust port is specified against the *current* Rust contracts, not against the TS.
13. Rust mediation is a danger **classifier**, not an allowlist: `src/bash_mediation.rs:255 assess`, `:218`/`:232` `DangerousCommandClass`, `.dcg.toml` `allow_patterns` `:310`, token-anchored match `:348-394`, `SHELL_CHAIN_METACHARS` `:397`. plan-mode's read-only allowlist `plan-mode.ts:11` (23 patterns) + git-mutation blocklist `:36` + `REDIRECT_PATTERN :51` + `hasUnsafePipe :66` is a stricter, different contract — grep for `SAFE_COMMAND_PATTERNS`/`MUTATING_GIT_COMMANDS` in Rust returned zero.
14. No settings slot exists for hashline defaults (`grep -c -i hashline src/config.rs` = 0) or for MCP (`grep -n 'pub mcp' src/config.rs` = 0) — both need new config keys, which means new merge functions modelled on `merge_plan` `src/config.rs:2226` / `merge_browser` `:2120`.
15. pi-crew relaunches child **TS** pi processes with `--extension` paths (`docs/fork/extension-inventory.md:69`); a native port must relaunch the Rust binary and re-define the lease/work-graph contract, because the TS child's semantics do not exist natively.
16. UNVERIFIED lane anchors: `src/extensions/extension_manager_impl.rs:606` / `:1301` / `:1394` (quoted by `docs/fork/rust-architecture.md:48-49`) did not resolve; quota/risk evidence found at `:391` (poll/cost quota, deadline), `:413-:460` (risk ledger + hostcall telemetry), `:160` (hostcall compat kill switch). Do not cite the packet anchors in implementation work.

---

## 3. Phased implementation order

Ordering rule: **gate-coupled surfaces first** (anything touching tiers/registry or
config merge is cheap and unlocks the tests), **then substrate modules**, **then the
subsystems that consume them**. Dependencies are stated because the alternative —
landing S3 before the session index is index-aware, or landing distill before the
native renderer — produces dead code the reachability gate will flag.

### Phase 0A — safe local gap-closes (no new subsystem ownership)
- **Gaps in scope:** prompt-spec forwarding (`promptSnippet`/`promptGuidelines` in
  `src/extensions_js.rs`), hashline default settings (`src/config.rs`), plan-mode
  allowlist gating (`src/plan.rs` + `src/bash_mediation.rs`), MCP/`mcp` settings slot
  + merge fns (`src/config.rs`, modelled on `merge_plan` `:2226`).
- **Target files:** `src/extensions_js.rs`, `src/ast_tools.rs`, `src/config.rs`,
  `src/plan.rs`, `src/bash_mediation.rs`.
- **Owned surface:** config + forwarding only; **no new tool names, no new subsystems**.
- **Depends on:** nothing.
- **Acceptance:** `dsr quality --tool pi_agent_rust` green; targeted tests per gap.
- **Bead granularity:** one bead per gap row here, each with user-visible acceptance
  criteria and blockers linked when beads touch the same file.

### Phase 0B — subsystem-hosted gaps (deferred into owning phases, NOT a small-fix phase)
The remaining Phase-0 rows from `docs/fork/phase-0-native-gaps.md` create new
subsystem ownership and must land with the subsystem that will own them:
- Gaps 1.1–1.4 (web-search breadth, PDF, YouTube, DOM-selector extraction) →
  **Phase 3** alongside S1's context/web surface (`src/web_search.rs`, `src/url_read.rs`).
- Gaps 2.1–2.3 (MCP unix-socket, OAuth storage, settings slot) → **Phase 2**
  transport/secrets work (`src/mcp.rs`, `src/mcp/transport.rs`, `src/secrets.rs`).
- Gaps 4.1–4.2 (browser doctor, Electron hosting) → **Phase 1** surface work
  (`src/browser.rs` + the native renderer).
Rationale (advisor consultation 2026-10-21): a "small gap" phase that carries
subsystem-sized gaps produces throwaway plumbing later phases restructure.

### Phase 1 — S4 presentation/settings layer (pi-model-request, pi-extensions-i18n, pi-extensions-tool-display)
- **Target files:** settings merge + a native result-renderer module reachable from
  `src/tui.rs` / `src/interactive_ftui.rs` / `src/rpc.rs`; auth/session-header reuse via
  `src/auth.rs`, `src/providers/`, `src/secrets`.
- **Owned surface:** config keys (`locale`, tool-display verbosity) + renderer
  dispatch. **No new tool names.**
- **Depends on:** Phase 0 (config merge patterns established).
- **Must precede:** distill entry rendering (S1) and pi-crew panel UI (S2) —
  `docs/fork/README.md:44-48` coupling inversion.
- **Acceptance:** render-protocol test (same input tool result → identical rendered
  block across ftui/classic/rpc); i18n catalog test asserting the selected locale;
  `dsr quality` green; module-reachability script green (the new renderer module needs a
  non-test call site — `scripts/check_module_reachability.py`).

### Phase 2 — S2 orchestration substrate (pi-crew work-graph/leases, pi-subagents grants, pi-advisor-flow roles)
- **Target files:** extend `src/subagents.rs`, `src/advisor.rs`, `src/agent_hub.rs`
  /`src/hub.rs`; worktree leases via `src/worktree_iso.rs` (`lib.rs:371`, snapshot
  helpers `src/worktree_iso/`), gate semantics via
  `src/validation_broker.rs` (`lib.rs:361`, `src/validation_broker/doctor.rs`).
- **Owned surface:** new tool names here (team/gate-style tools) → **must** update
  `src/xdev.rs:59-82` + `src/tools.rs:5694` + a registry arm in the same commit
  (`src/tools.rs:5822`-style) or `src/xdev.rs:390-414` and
  `src/tools.rs:25063-25085` fail.
- **Depends on:** Phase 1 (panels render through the native renderer).
- **Acceptance:** `dsr quality`; team-subsystem integration tests (work-graph cycles
  stay empty, lease conflict refuses, gate verdict closes on artifact path + source
  reference); grant-model test asserting hidden/direct exposure semantics carried as
  Rust tool metadata (replacing the dropped `exposure` key).

### Phase 3 — S1 context-management (pi-fold lossless fold + pi-distill entry type)
- **Target files:** `src/compaction.rs` / `compaction_snap.rs` / `compaction_worker.rs`;
  session entry types in `src/session.rs` (validator `:131-226`); budget hooks.
- **Owned surface:** new session entry types + fold settings band (modelled on
  `merge_plan` `src/config.rs:2226`). No new tool names.
- **Depends on:** Phase 1 (renderer) and Phase 2 (advisor verdicts feed fold ranking).
- **Acceptance:** fold property test — oldest-context-only, byte-lossless round-trip;
  distill audit entry survives `src/session.rs` validation and replays; compaction
  budget test asserts fold-aware budgeting (S1 synergy).

### Phase 4 — S3 peer coordination (pi-cross-session)
- **Target files:** a new module (peer/session-index channel) plus tool names
  `list_pi`, `send_pi_message`, `peer_goal` → tier + `KNOWN_TOOL_NAMES` + registry arm
  in the same commit.
- **Owned surface:** the socket transport itself (absent today: grep `AF_UNIX`
  in `src/` = 0), session-index discovery (`src/session_index.rs:26-35`), and
  `src/session.rs` peer-message entry type.
- **Depends on:** Phase 1 (rendering of peer messages) and Phase 3 (session entry
  types), because the channel's payload is a session entry.
- **Acceptance:** two-process fixture: create two session dirs, register peers, exchange
  a message, assert both sides' index sees it; refusal test for a peer whose session is
  locked.

### Phase 5 — retire the TS once each Rust target is live
- **Target files:** docs only (`docs/fork/README.md` rows) plus the extension bundle
  manifest — never delete package sources (AGENTS.md Rule 1).
- **Depends on:** every row's Rust target being reachable (not merely compiling) —
  `scripts/check_module_reachability.py` is the instrument for exactly this
  ("a module only its own tests reference is not a shipped feature").

---

## 4. Synergy (how the hardbaked features compose better than 15 TS extensions)

Each claim names the mechanism and the anchor that makes it possible; none is
implemented yet.

1. **Fold-aware distillation budgeting.** Distill's summary tokens are paid once.
   With fold living inside the compaction pipeline (`src/compaction.rs:196`
   `CompactionDetails`, `:207` `CompactionResult`), the fold plan can charge the distill
   pass against the *same* budget object instead of a separate JS counter — the fold
   band and the distill band become one ledger, so a fold that would evict a distilled
   region can reprice the distill rather than duplicate it.
2. **Advisor council feeding orchestrator role selection.** `src/advisor.rs:26`
   `VerdictLevel` + `:57` `includes()` + `:68` `AdvisorVerdict` + `:75` `TurnDigest`
   is already a typed verdict lattice. pi-advisor-flow's executor/advisor pair, pi-crew's
   team, and pi-subagents' children can all consume the **same** verdict type, so an
   advisor's verdict selects which role continues — the pause switch already exists as
   process-wide state (`src/agent.rs:7038` `crate::advisor::ADVISOR_PAUSED`, gating
   `may_continue`). A council is then N verdicts over one digest, not N JS bundles.
3. **Cross-session peers sharing the session index.** `src/session_index.rs:26`
   `SessionMeta{path,id,cwd,timestamp,message_count,last_modified_ms,size_bytes}` is
   machine-wide. Peer discovery becomes an index query instead of a socket-directory
   scan, and `peer_goal` reads the peer's tree through the same validator as the local
   session — one format, two views (`src/session.rs:303`, `src/session_index.rs:308`).
4. **Fold/distill and compaction share the snap+worker stages.** `compaction_snap`
   (`src/lib.rs:125`) and `compaction_worker` (`:127`) are already split stages; fold
   and distill slot in as policies over those stages rather than as pre/post JS passes,
   so folding reuses snapshots already taken for compaction — no second traversal.
5. **Plan mode + bash mediation as one state machine.** `src/plan.rs:211` `PlanMode
   {Off, Planning, PendingApproval, Approved}` with "mutations are blocked" under
   Planning, and the agent gate at `src/agent.rs:7038` matching only
   `Off | Approved`, plus `src/bash_mediation.rs` danger tiers: pi-plan-mode's
   read-only-allowlist becomes a tier of the existing classifier instead of a parallel
   regex table that can drift from it.
6. **Renderer layer as the single place i18n, tool-display and distill print.** With
   the S4 renderer native (Phase 1), i18n catalogs are data, tool-display is dispatch,
   and distill is an entry type — the three-way `pi.extensions` sibling coupling
   (`docs/fork/extension-inventory.md:15`, `:27`) disappears along with its half-load
   failure mode (`docs/fork/extension-inventory.md:31-33`).
7. **MCP transports become one trait.** `src/mcp.rs:14` `pub mod transport` already
   implements `impl McpTransport` for `StdioTransport` (`src/mcp/transport.rs:1283`)
   and `HttpTransport` (`:3091`); a unix-socket transport is a third impl of the same
   trait, which then also serves S3's peer channel plumbing (same socket code path,
   different framing).

---

## 5. Gate (checks every fix in any phase must pass)

Authoritative, in order — all four must be green before a slice is committable:

1. **`dsr quality --tool pi_agent_rust`** — the only quality/build/release authority
   (AGENTS.md). Do **not** run bare `cargo`/`clippy`/`cargo test` as a quality path; do
   not substitute a hand-picked subset.
2. **Tier/registry coupling** — a new tool name lands simultaneously in
   `src/xdev.rs:59-82` (`ESSENTIAL_DEFAULTS`/`OPT_IN_ONLY`) and `src/tools.rs:5694`
   (`KNOWN_TOOL_NAMES`) or `TOOLS_NOT_SELECTED_BY_FLAG`, plus a registry arm
   (`src/tools.rs:5822`-style). Enforced by `src/xdev.rs:390-414` and
   `src/tools.rs:25063-25085`; part of the DSR recipe, listed separately because it is
   the check that fails *most often* here.
3. **Module reachability** — `python3 scripts/check_module_reachability.py` exit 0.
   Existence verified in this checkout. A new subsystem module with only test callers
   fails this gate by design.
4. **UBS changed-line gate** — `ubs --staged --only=rust .` exit 0; when whole-file
   baseline noise dominates, `python3 scripts/check_ubs_staged_delta.py` (existence
   verified) fails only on findings over added/modified lines.

Docs-only phases (this document and `phase-0-native-gaps.md`) are not code, so 2–4 do
not apply; they must not be cited as met for code phases.
