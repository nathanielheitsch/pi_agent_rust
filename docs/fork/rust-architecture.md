# Rust port architecture (fork reference)

Map of the surfaces a fork touches, with the anchors verified in this checkout (nightly 2024
edition, `#![forbid(unsafe_code)]`, `#![recursion_limit = "256"]` — `src/lib.rs:20`/`src/lib.rs:33`).

## Entry points

- `src/main.rs:224` — `fn main()`; Windows share-job and structured-read worker shortcuts run
  before `main_impl()` (`src/main.rs:228`, `:232`).
- `src/cli.rs` — clap surface. Extension loading is flag-driven: `--extension <path>` and
  `--extension-policy {safe|balanced|permissive}`. The `--extension-policy` parse tests sit at
  `src/cli.rs:2020-2033`; CLI wiring feeds `Config.extension_policy`.
- `src/lib.rs:66` — `extern crate self as pi;` is why binaries and tests resolve `pi::…` paths.

## Tool registry and tiers

- `src/tools.rs:5694` — `KNOWN_TOOL_NAMES`, the registry inventory. 26 tools built inside the
  registry, plus three host-coupled (`ask`, `todo`, `submit_plan`, `src/tools.rs:5723-5725`) that
  `main.rs` / `sdk.rs` join after construction. Do not describe those three as registry-built.
- Tiers live in `src/xdev.rs`:
  - `src/xdev.rs:59-78` `ESSENTIAL_DEFAULTS` — always in the schema: `read write edit bash grep
    find ls hashline_edit ask todo xdev web_search submit_plan current_time`.
  - `src/xdev.rs:82` `OPT_IN_ONLY = ["subagent"]` — never default, never discoverable.
  - `src/xdev.rs:86` `default_tier()` — anything else is `LoadMode::Discoverable`, reachable via
    `xdev` (`ast_grep`, `ast_edit`, `lsp`, `debug`, `manage_skill`, memory-bank tools).
  - Per-tool override key: `tools.loadMode.<name>` in settings (`src/xdev.rs:96-109`, `tier_for`).
- Consistency checks: `src/xdev.rs:392-406` requires every name tiered in `ESSENTIAL_DEFAULTS`/`OPT_IN_ONLY`
  to appear in `KNOWN_TOOL_NAMES` or `TOOLS_NOT_SELECTED_BY_FLAG`; the unit test
  `tool_registry_builds_every_listed_name` (`src/tools.rs`) proves the registry builds each listed name.
  A tool name added without a registry arm breaks the build, not just the docs.

## Extension runtime and capability policy

- `docs/extension-architecture.md` is the maintained design doc; this file only locates it.
- Two runtime modes (`docs/extension-architecture.md:11-17`): TS/JS entrypoints run in embedded
  QuickJS; `*.native.json` descriptor entrypoints run natively. Selection is automatic from entry
  type — a fork that hardbakes a TS extension needs no conversion step.
- `src/extensions_js.rs` — QuickJS (rquickjs) bridge. The `pi` global, `pi.registerTool`,
  `pi.registerCommand` are JS-side validators in that file:
  - `src/extensions_js.rs:21546-21578` `__pi_register_tool` — accepts
    `name`, `description`, `parameters`, `label`, `execute`; collision check per extension id.
  - `src/extensions_js.rs:21592-21629` `__pi_register_command` — accepts `handler` or `fn`
    (PiCommand compat), collision check. Slash commands are natively supported.
- `ExtensionManager` (`src/extensions.rs`, table at `docs/extension-architecture.md:36-52`) owns
  `ui_sender: Option<mpsc::Sender<ExtensionUiRequest>>` — the channel into the TUI — plus
  `active_tools`, `permission_store`, `policy_prompt_cache`, `extension_budget`.
- Policy: `PolicyProfile::Safe|Standard|Permissive` → `"safe"|"balanced"|"permissive"` at
  `src/config.rs:731-737`. Quota and risk scoring in `src/extensions/extension_manager_impl.rs:606`
  (quota), `:1301` (`runtime_risk_base_score`), `:1394` (`runtime_risk_is_dangerous`).
- Runtime capability inventory is `docs/extension-compatibility-matrix.md` (module shims, e.g.
  `node:child_process` Full at `:24`, `node:net` Stub at `:29`). It is a *runtime-capability*
  matrix, not a per-extension decision table.

## Config and settings

- `src/config.rs` — global + project settings merge (`Config::load()` `src/config.rs:741`,
  `SettingsScope::Global|Project` `src/config.rs:725-728`).
- Feature settings groups near `src/config.rs:92-102`: plan mode, read-tool URL reads, bash
  mediation, memory bank, media tools, computer tool.
- Docs: `docs/settings.md`, `docs/models.md`, `docs/themes.md`, `docs/keybindings.md`.

## Session persistence

- `src/session.rs` — JSONL v3 tree with branching; line validation and limits at
  `src/session.rs:131-226`.
- Adjacent modules: `src/session_index.rs`, `src/session_sqlite.rs` (default-enabled
  `sqlite-sessions` feature), `src/session_store_v2.rs`, `src/session_control.rs`,
  `src/session_import/`, `src/session_workdir/`.
- Docs: `docs/session.md`.

## Surfaces

- `src/interactive_ftui.rs` — FrankenTUI, default (`ftui` feature on).
- `src/interactive.rs` — classic charmed_rust TUI (`--classic`), and
  `InteractiveExtensionSession` glue that gives extension extensions a TUI.
- `src/rpc.rs` — RPC/stdin server mode (`--mode rpc`, `--rpc`); `docs/rpc.md`.
- `src/sdk.rs` — the stable library-facing surface (`src/lib.rs:13` policy note); everything else
  is unstable for embedders. `docs/sdk.md`.
- Notable subsystem modules (`src/lib.rs`): `advisor:90`, `agent_hub:96`, `approval:99`,
  `ask:103`, `ast_tools:105`, `auth:107`, `browser:113`, `compaction:123`,
  `compaction_snap:125`, `compaction_worker:127`, `mcp` (see `src/mcp.rs`, `src/mcp/`),
  `subagents/`, `hub.rs`, `worktree_iso.rs`, `validation_broker.rs`.

## Fork-relevant native capabilities already present

| Module | Anchor | What exists natively |
|---|---|---|
| browser | `src/browser.rs:1-6`, `src/browser.rs:39-54` | CDP-driven Chromium, `BrowserElementRef{ref_id,tag,role,text,selector}`, `BrowserSnapshot{url,title,elements,summary}` — a snapshot/ref model |
| compaction | `src/compaction.rs`, `compaction_snap.rs`, `compaction_worker.rs` | context compaction pipeline |
| mcp | `src/mcp.rs`, `src/mcp/`, `docs/mcp-progress.md` | native MCP server support |
| advisor | `src/advisor.rs` | advisor/executor flow |
| subagent | `src/subagents.rs`, `docs/subagent-*.md` | opt-in native child-agent delegation |
| url reads | `src/url_read.rs`, `src/url_router.rs` | the `read` tool accepts URLs |
| skills | `src/resources.rs`, `docs/skills.md:9-11` | global/project/package skill discovery |
