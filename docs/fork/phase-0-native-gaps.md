# Phase 0 — native gap list (the "already native" rows)

The six A rows of `docs/fork/README.md:25-42` (pi-hashline-edit, pi-plan-mode,
pi-ast-grep, pi-agent-browser-native, pi-mcp-adapter, pi-web-access) stay native.
What remains is the **behaviour the Rust side does not have today**. Each gap below
carries: evidence anchor (resolved in this checkout unless marked), where the fix
lands, and the risk of shipping without it. Ordered by **risk-if-skipped descending**
(highest = a user-visible product capability that is currently absent or silently
degraded).

Anchors are ±1 line tolerant. Anything the reconnaissance lanes could not resolve is
marked UNVERIFIED and must not be used as a fix target until re-checked.

---

## Order of risk (1 = fix first)

1. pi-web-access — search-provider breadth and PDF/YouTube reads
2. pi-mcp-adapter — unix-socket transport + MCP OAuth token storage
3. pi-plan-mode — read-only allowlist / git-mutation block during Planning
4. pi-agent-browser-native — doctor/config surface, Electron hosting
5. pi-ast-grep — prompt guidance for the native AST tools
6. pi-hashline-edit — hashline default settings

---

## Gap 1.1 — web-search provider breadth (pi-web-access)

- **Evidence:** `src/web_search.rs:83-94` — `DEFAULT_CHAIN` names exactly 10 rungs
  (`perplexity, brave, tavily, exa, jina, kagi, youcom, duckduckgo, startpage, mojeek`).
  The extension advertises 27 comma-separated providers in its package description
  (`pi-web-access/package.json` description; 27-item count computed from that string).
  → ~17 named rungs have no Rust rung.
- **Present natively:** per-provider circuit breaking (`src/web_search.rs:6`
  comment; state at `:925` "Circuit-breaker state per rung"; open-rung note
  `:982`) and the rung table keyed off `DEFAULT_CHAIN` (`:830`, `:970`, `:975`).
- **Fix location:** extend the rung table + `DEFAULT_CHAIN` in `src/web_search.rs`;
  provider auth via `env_key` (`src/web_search.rs:96`). No new tool name
  (`web_search` already essential, `src/xdev.rs:72`).
- **Risk if skipped:** users lose most of the engines they configured. Silent — the
  tool still answers from 10 rungs, so degradation is invisible to the operator and
  only shows up as missing sources.
- **Falsifier:** a chain test asserting every advertised provider resolves to a rung
  (or is explicitly listed as unsupported), plus breaker behaviour per rung.

## Gap 1.2 — PDF extraction is opt-in, not default (pi-web-access)

- **Evidence:** `src/url_read.rs:222` `#[cfg(feature = "url-pdf")]`, `:224`
  `pdf_extract::extract_text_from_mem_by_pages`, `:236` `#[cfg(not(...))]` fallback,
  `:240` `"[PDF_NOT_COMPILED] …"`; `Cargo.toml:498` `default = ["sqlite-sessions","tui","bpe-tokens","ftui"]`
  (no `url-pdf`), `Cargo.toml:507` `url-pdf` inside `full`, `Cargo.toml:516`
  `url-pdf = ["dep:pdf-extract"]`, and the comment at `Cargo.toml:514-515` says it is
  opt-in to hold the release binary under the size budget.
- **Fix location:** decide policy in `Cargo.toml` (ship `url-pdf` in `default`, or
  keep opt-in and make `docs/url-reads`/`docs/settings.md` state it); the TS side
  ships it unconditionally (`pi-web-access/pdf-extract.ts`, `datalab-pdf-extract.ts`,
  `endpoint-pdf-extract.ts`).
- **Risk if skipped:** a PDF URL read returns a build notice string instead of text —
  an explicit user-visible failure in the default binary.
- **Cost note:** binary-size budget in AGENTS.md is 48 MiB for the release; verify the
  DSR size lane before flipping the feature.
- **Falsifier:** `--features url-pdf` fixture returning extracted text for a PDF;
  the default-feature test asserting the fallback message stays honest
  (existing test `src/url_read.rs:516` asserts `PDF_NOT_COMPILED`).

## Gap 1.3 — no YouTube handling anywhere in the Rust port (pi-web-access)

- **Evidence:** `grep -rli youtube src/` returned **0 files** (re-run in this
  checkout). The extension implements it (`pi-web-access/youtube-extract.ts`, wired at
  `pi-web-access/index.ts`, `web-tool-core.ts`, `extract.ts`).
- **Fix location:** `src/url_read.rs` content-kind classification (`:25`
  `UrlContentKind`, `:189` classifier, `:128` kind→converter) plus
  `src/url_router.rs:74` `resolve()` for URL dispatch; transcript retrieval via the
  existing provider layer (`src/providers/`) rather than scraping.
- **Risk if skipped:** "read this video" is unsupported in the product while the
  installed extension set promises it. Highest user-facing loss of the whole A set.
- **Falsifier:** a kind-classifier test for a YouTube URL + a fixture asserting a
  transcript/summary payload or an explicit unsupported-with-reasons message.

## Gap 1.4 — DOM-selector URL extraction is unhostable (pi-web-access ⇒ forces Rust)

- **Evidence:** `docs/extension-compatibility-matrix.md:157` — `linkedom` `parseHTML()`
  is a "DOM parser stub"; the TS side awaits it at `pi-web-access/extract.ts:85`
  and `:1471`, `pi-web-access/duckduckgo.ts:72`. Rust side has
  `generic_html_to_markdown` (`src/url_read.rs:284`) only.
- **Fix location:** a selector-capable HTML pass in `src/url_read.rs` (dependency crate,
  since `#![forbid(unsafe_code)]` `src/lib.rs:20` rules out FFI).
- **Risk if skipped:** selector-driven scraping ("give me the table on this page")
  degrades to markdown-of-everything; also this is the row that proves Option C was
  never viable, so the gap must be recorded before the TS is retired.
- **Falsifier:** a fixture asserting a CSS-selector-scoped extraction.

## Gap 2.1 — MCP unix-socket transport (pi-mcp-adapter)

- **Evidence:** `src/mcp.rs:14` `pub mod transport`; `src/mcp/transport.rs:1261`
  `StdioTransport`, `:1440` `HttpTransport`, with `impl McpTransport` at `:1283` and
  `:3091` — no third transport. `grep -i 'unix socket|AF_UNIX|.sock'` across
  `src/mcp.rs` + `src/mcp/` returned **0**. The TS implementation is
  `pi-mcp-adapter/unix-socket-transport.ts:1` `import { createConnection } from "node:net"`
  and `:23` `createConnection(this.socketPath)`, which cannot run in QuickJS
  (`docs/extension-compatibility-matrix.md:29` `node:net` Stub).
- **Fix location:** a third `impl McpTransport` in `src/mcp/transport.rs` + the
  server-config discriminator in `src/mcp/config` (`src/mcp/config.rs`).
- **Risk if skipped:** every MCP server configured over a unix socket (the common
  local-transport shape) fails to connect — loud, but blocking for a documented user
  configuration.
- **Falsifier:** a fixture that starts a socket server and round-trips an MCP
  handshake over it (mirror the `HeldStdioTransport` fixtures at
  `src/mcp/transport.rs:3489`/`:3619`).

## Gap 2.2 — MCP OAuth / token storage (pi-mcp-adapter)

- **Evidence:** `pi-mcp-adapter/package.json` depends on `@napi-rs/keyring` (native
  addon — unloadable in QuickJS regardless of the stub matrix); TS flow files
  `pi-mcp-adapter/mcp-auth.ts`, `mcp-auth-flow.ts`, `mcp-oauth-provider.ts`,
  `oauth-handler.ts`, `jev-key-store.ts:50` (`source: "environment" | "keyring"`).
  Rust: `grep -i 'oauth|authorization|keyring' src/mcp.rs` = **0**. Nearest surface:
  `pub mod secrets` `src/lib.rs:295`, `pub mod auth` `:107`.
- **Fix location:** token store in `src/secrets` (or `src/auth`) with the MCP layer
  consuming it; **UNVERIFIED** which module already persists provider secrets —
  confirm before choosing, do not assume.
- **Risk if skipped:** OAuth-capable MCP servers (GitHub-style) cannot be authorised at
  all; the loss is total, not partial.
- **Falsifier:** an OAuth fixture (local callback server — natively legal, unlike the
  TS `net.createServer` at `docs/extension-compatibility-matrix.md:254` "Not applicable")
  asserting a stored+reloaded token.

## Gap 2.3 — no `pub mcp` settings slot (pi-mcp-adapter config surface)

- **Evidence:** `grep -n 'pub mcp' src/config.rs` returned **0**, while browser has
  `src/config.rs:105` `pub browser: Option<crate::browser::BrowserSettings>` merged by
  `merge_browser` `src/config.rs:2120` (call `:953`), and plan has `src/config.rs:93`
  merged by `merge_plan` `:2226`.
- **Fix location:** new settings struct + merge fn modelled on `merge_plan`, plus the
  `Config::load()` path at `src/config.rs:741`.
- **Risk if skipped:** MCP servers cannot be configured declaratively in settings;
  users fall back to CLI flags/`docs/mcp-progress.md` behaviour.

## Gap 3.1 — plan-mode read-only allowlist vs Rust danger classifier (pi-plan-mode)

- **Evidence:** TS tables `pi-plan-mode/plan-mode.ts:11` `SAFE_COMMAND_PATTERNS`,
  `:36` `MUTATING_GIT_COMMANDS`, `:51` `REDIRECT_PATTERN`, `:66` `hasUnsafePipe`,
  consulted at `:73-75` and `:185`. Rust: `src/bash_mediation.rs:255` `assess`,
  `:218`/`:232` `DangerousCommandClass` tier/reason, `:310` `allow_patterns` (`.dcg.toml`),
  `:348-394` token-anchored allow matching, `:397` `SHELL_CHAIN_METACHARS`.
  Grep for `SAFE_COMMAND_PATTERNS` / `MUTATING_GIT_COMMANDS` in Rust returned **0**.
  The Rust side is a *classifier*; the TS is an *allowlist during a mode*.
- **Present natively:** the mode itself — `src/plan.rs:211` `PlanMode {Off, Planning,
  PendingApproval, Approved}` with "mutations are blocked" under Planning, and the
  agent gate `src/agent.rs:7038` continuing only on `Off | Approved`.
- **Fix location:** a plan-phase tier in `src/bash_mediation.rs` (one rule set, per its
  own comment at `:310` "maintain ONE rule set; allow_patterns win over any classifier
  hit") driven by `PlanState`/`PlanMode`, not a parallel regex table.
- **Risk if skipped:** in Planning mode a read-only-classified-but-mutating command
  (e.g. `git commit` — classifier-tiered elsewhere, not blocked by an allowlist) can
  execute. Silent permissive drift.
- **Falsifier:** a bash-mediation test asserting every mutating git command is refused
  while `PlanMode == Planning`, and the 23 read-only patterns are allowed.

## Gap 3.2 — plan-phase pipe/redirect gating (pi-plan-mode)

- **Evidence:** `pi-plan-mode/plan-mode.ts:51` `REDIRECT_PATTERN` and `:66`
  `hasUnsafePipe` gate the allowlist; Rust has `SHELL_CHAIN_METACHARS`
  `src/bash_mediation.rs:397` (partial overlap) but no phase-aware variant.
- **Fix location:** same module as Gap 3.1; make metachar rejection phase-aware.
- **Risk if skipped:** `echo x > repo/file` during Planning mutates the repo while the
  model believes it is read-only.

## Gap 4.1 — no browser doctor / config CLI surface (pi-agent-browser-native)

- **Evidence:** `grep -c -i 'doctor' src/browser.rs` = **0**; the extension ships it
  as an external CLI (`docs/fork/extension-inventory.md:24` — "3 CLI bins
  (config/convert/doctor)"). Caution: `pub mod doctor` `src/lib.rs:153` is the
  **validation-broker** doctor, unrelated.
- **Fix location:** a subcommand in the CLI surface (`src/cli.rs`, entry `src/main.rs:224`)
  reporting CDP/browser readiness, mirroring `docs/troubleshooting.md`.
- **Risk if skipped:** misconfigured browser stacks fail inside tool calls with no
  diagnosable pre-flight — user-visible but recoverable.

## Gap 4.2 — no Electron hosting in the browser subsystem

- **Evidence:** `grep -c -i electron src/browser.rs` = **0**. The TS wrapper
  (`pi-agent-browser-native`) exposes Electron launch/probe/cleanup paths.
- **Fix location:** `src/browser.rs` (`pub mod browser` `src/lib.rs:113`; ref model
  `BrowserElementRef` `:40`, `BrowserSnapshot` `:49`) needs an attach path (CDP port
  reuse) rather than a Chromium-launch path.
- **Risk if skipped:** desktop-app debugging (a first-class use of this install) is
  unsupported. Medium: it degrades to "not supported" rather than misbehaving.

## Gap 5.1 — `promptSnippet` / `promptGuidelines` are dropped by the bridge and absent natively (pi-ast-grep)

- **Evidence:** `grep` for `promptSnippet`/`promptGuidelines` across `src/` = **0**;
  the bridge builds the tool spec from only `name`, `description`, `parameters`,
  `label`, `execute` (`src/extensions_js.rs:21559-21566`, validator
  `__pi_register_tool` `:21546-21578`). TS declares them at
  `pi-ast-grep/src/index.ts:17`/`:18`.
- **Fix location:** add the keys to the Rust tool metadata consumed by
  `src/ast_tools.rs` tools (`crate::ast_tools::AstGrepTool` / `AstEditTool`
  registry arms `src/tools.rs:5846`/`:5847`), and — if any TS extension keeps
  declaring them — forward them at `src/extensions_js.rs:21559-21566`.
- **Risk if skipped:** the model loses the usage guidance that makes AST tools
  discoverable in the right moments; discoverable-tier tools (`src/xdev.rs:86`
  fall-through) then get picked blindly. Silent quality regression, not a failure.

## Gap 6.1 — no hashline default settings (pi-hashline-edit)

- **Evidence:** `grep -c -i hashline src/config.rs` = **0**. Native side is present:
  essential tier `src/xdev.rs:67`, `KNOWN_TOOL_NAMES` `src/tools.rs:5709`,
  registry arm `src/tools.rs:5822-5826` (`HashlineEditTool::new(cwd)`), opt-in schema
  keys `src/tools.rs:6920` (read) and `src/tools.rs:13297` (grep), plus guard copy at
  `:6600`, `:6720`, `:6776`. The TS `hashline.json` config key has no Rust analog.
- **Fix location:** new `pub hashline: Option<HashlineSettings>` near the other
  feature slots (`src/config.rs:92-105`) with a merge fn modelled on
  `merge_plan` `src/config.rs:2226`; consumed by the read/grep `hashline` flag
  default instead of a per-call parameter.
- **Risk if skipped:** every read must opt in per call; low-frequency annoyance rather
  than a defect. Lowest risk in the set.

---

## Cross-cutting caveats for Phase 0 fixes

- `recursion_limit` is per crate (`src/lib.rs:33` = 256, **not inherited** by the
  binary, examples, or integration tests) — an async-heavy MCP socket transport can hit
  it in `src/main.rs`'s own crate even with `lib.rs` raised.
- Capability policy is profile-valued (`src/config.rs:731`
  `effective_profile_str`); it does not gate *native* code paths, only bridge
  hostcalls — do not treat a Phase 0 native fix as policy-covered.
- UI channels fail closed off-TUI (`src/extensions.rs:20878` `ui_sender`,
  `:20880` "fails closed when unset") — anything Phase 0 adds to the browser or MCP
  panels needs an RPC-surface branch (`src/rpc.rs`).
- UNVERIFIED lane anchors (do not cite as targets):
  `src/extensions/extension_manager_impl.rs:606`/`:1301`/`:1394`; resolved
  substitutes: `:391` poll/cost quota, `:413-:460` risk ledger, `:160` hostcall
  compat kill switch.

---

## Gate — checks any Phase 0 fix must pass

Falsifiable, in order. A fix is shippable only with all four green; the docs-only
state of this run satisfies none of them.

1. **`dsr quality --tool pi_agent_rust`** — the sole quality authority (AGENTS.md):
   it owns fmt/compile/clippy/tests/conformance lanes. Never cite a bare
   `cargo`/`clippy`/`cargo test` run as the quality path; never a hand-picked subset.
2. **Tier/registry coupling** — a new tool name lands simultaneously in
   `src/xdev.rs:59-82` and `src/tools.rs:5694` (or `TOOLS_NOT_SELECTED_BY_FLAG`) with a
   registry arm; enforced by the test at `src/xdev.rs:390-414` and the registry test at
   `src/tools.rs:25063-25085`. Phase 0 as scoped adds **no** tool names — this check
   exists to catch a fix that sneaks one in.
3. **Module reachability** — `python3 scripts/check_module_reachability.py` exit 0
   (script exists in this checkout). Any new Phase 0 module needs a non-test call site;
   `tests/` references deliberately do not count.
4. **UBS changed-line gate** — `ubs --staged --only=rust .` exit 0, or
   `python3 scripts/check_ubs_staged_delta.py` when whole-file findings are baseline
   noise (script exists in this checkout).
