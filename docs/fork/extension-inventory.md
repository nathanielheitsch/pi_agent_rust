# Extension inventory — the 15 active TS pi extensions + the ponytail skill

Source of truth for what is installed on this machine:
`/Users/nathanielheitsch/.pi/agent/npm/node_modules/<name>/package.json` (15 `pi-package`
keywords, each declaring a `pi` field) and `/Users/nathanielheitsch/.pi/agent/git/github.com/DietrichGebert/ponytail`.

Facts marked *(lane 01/02)* were verified by the read-only reconnaissance lanes against the
extension sources; facts without a marker were verified directly in this checkout.

## What each declares in its `pi` field (bundle contract)

| Extension | ver | `pi.extensions` | `pi.skills` / other |
|---|---|---|---|
| pi-hashline-edit | 0.8.3 | `./index.ts` | — |
| pi-distill | 1.10.1 | `./index.ts`, `../pi-extensions-i18n/index.ts`, `../pi-extensions-tool-display/index.ts` | `./SKILL.md` |
| pi-fold | 4.2.8 | `./extensions` (entry `./extensions/index.js`) | — |
| pi-crew | 0.11.29 | `./index.ts` | `./skills` |
| pi-subagents | 0.79.2 | `./index.js` | `./skills`, `./prompts` |
| pi-advisor-flow | 0.14.1 | `./dist/index.js` (bun-built bundle) | — |
| pi-web-access | 0.43.0 | `./dist` | bin `pi-web-access-mcp` |
| pi-mcp-adapter | 5.4.0 | `./index.ts` | `skills` (files list) |
| pi-plan-mode | 0.4.8 | `./plan-mode.ts` | — |
| pi-ast-grep | 0.1.0 | `./src/index.ts` | — |
| pi-agent-browser-native | 0.9.6 | `./dist/extensions/agent-browser/index.js` | 3 CLI bins (config/convert/doctor) |
| pi-cross-session | 2.3.0 | `./extensions` | — |
| pi-extensions-i18n | 0.9.0 | `./index.ts` | `./SKILL.md` |
| pi-extensions-tool-display | 1.3.3 | `./index.ts`, `../pi-extensions-i18n/index.ts` | `./SKILL.md` |
| pi-model-request | 0.1.1 | `./index.ts` | — |
| ponytail (skill) | dev pkg | `pi-extension/index.js` under `pi-extension/` | `skills/`, `hooks/`, `commands/` at repo root |

Three packages declare **relative sibling paths** (`../pi-extensions-i18n/index.ts`). A fork that
vendored extensions must keep the sibling directory layout or rewrite those entries, otherwise the
bundle silently loads half a package.

## Purpose and public surface

| Extension | What it does | Tools | Commands | Hooks / skills |
|---|---|---|---|---|
| pi-hashline-edit | hashline-addressed read/edit tool override (`hashline.json` config) | read, edit, + grep when enabled | — | `session_start` → `ctx.ui.notify`; env `PI_HASHLINE_DEBUG` |
| pi-distill | distills tool output, file-first config | audit entry type `pi-distill-audit` *(lane 02: src/fallback-renderer.ts:8)* | — | renderer hooks, `SKILL.md` |
| pi-fold | lossless context folding; only oldest context changes | — (none found) | `fold-settings` *(extensions/settings.ts:664)*, dynamic per-context *(active-context.ts:5430)* | events/hooks |
| pi-crew | coordinated AI teams, workflows, worktrees, async tasks | `team` *(registration/team-tool.ts:200)*, gate tool *(verify-gate-tool.ts:231)* | `team-run`, `team-invalidate`, `team-retry`, `team-respond`, `team-follow-up`, `team-api`, `team-goal` | relaunches child pi processes with `--extension` paths; inline panel UI |
| pi-subagents | single-agent delegation + scripted multi-agent | `contact_supervisor` *(herdr-pi-bridge.js:96)* | `pi-subagents-bridge` *(:152)* | `skills/`, `prompts/`, child-session command/bash wrapping |
| pi-advisor-flow | Executor/Advisor decision flow, configurable | — | — | flow state in `dist/index.js` bundle |
| pi-web-access | search/fetch/clone/PDF/YouTube across ~24 search providers | `registerTool` sites *(index.ts:1419 websearch, :1969 source-check, :1992 fetch-content)* | — | MCP CLI entry |
| pi-mcp-adapter | MCP server adapter (dynamic tools, OAuth, keyring) | dynamic `pi.registerTool` *(:1285 skills/mcp-scripting)* | setup panel | `skills` |
| pi-plan-mode | read-only exploration mode + plan-file editing, bash whitelist | mode toggles | — | blocks write/edit, `SAFE_COMMAND_PATTERNS`/`MUTATING_GIT_COMMANDS` regex tables *(plan-mode.ts:11-60)* |
| pi-ast-grep | read-only ast-grep structural search | `ast_grep` (+ `label`, `promptSnippet`, `promptGuidelines` *(src/index.ts:12-24)*) | — | — |
| pi-agent-browser-native | agent-browser as a native tool (CDP, snapshots, @refs) | browser tool surface | config/doctor as external CLIs | engines `node >=24.21.0` |
| pi-cross-session | Pi↔Pi same-machine messaging over per-instance IPC | `list_pi` *(:1006)*, `send_pi_message` *(:1021)*, `peer_goal` *(:1047)* | `peers` *(:1100)*, `list-pi` *(:1105)* | Unix socket / named pipe |
| pi-extensions-i18n | shared i18n catalog loader/translator | — | — | `SKILL.md`, locales dir |
| pi-extensions-tool-display | tool display host + shared result-render protocol | render protocol `tool-display-api-consumer.js` | — | `SKILL.md` |
| pi-model-request | one-off model request: resolve auth, add provider session headers, call completion | — | — | peer `@earendil-works/pi-ai >=0.80.0` only |
| ponytail skill | multi-harness plugin repo (`.claude-plugin/`, `.cursor/`, `gemini-extension.json`, `plugin.json`, `opencode.json`) | — | `commands/` | `skills/` + `hooks/`; `pi-extension/index.js` |

## Pi SDK surface each extension depends on

Everything above imports the TS SDK namespaces `ExtensionAPI`, `ExtensionContext`,
`ExtensionCommandContext`, `AgentToolResult`, `AgentToolUpdateCallback`, `RegisteredMcpServer`,
`ToolInfo`, `withFileMutationQueue` plus `@earendil-works/pi-ai` (`completeSimple`).
pi-plan-mode is the outlier: it imports `@mariozechner/pi-coding-agent` `^0.63.0` — an older
namespace pin.

| Extension | Host capability it leans on | Consequence for a Rust port |
|---|---|---|
| pi-hashline-edit | replaces built-in `read`/`edit`/`grep`; `process.env`; `ctx.ui.notify` | Rust already ships `hashline_edit` natively; replacement of host built-ins is the part needing care |
| pi-distill | session entry types + TUI renderers | `ExtensionUiRequest` channel exists (`docs/extension-architecture.md:45`, `src/extensions.rs`) |
| pi-fold | events/hooks, `node:fs`, TUI | overlaps `src/compaction*.rs`; needs `pi.events` not `node:*` |
| pi-crew | `node:child_process` (Full, `fork` stub), worktrees, IPC broker | relaunches pi processes — the *child* is the TS pi, not the Rust pi |
| pi-subagents | `registerTool` option `exposure: "direct"|"hidden"` *(:181, :390)*, undici, acorn | `exposure` is **not** in the forwarded key set (`src/extensions_js.rs:21559-21566`): name, description, parameters, label, execute only |
| pi-advisor-flow | pure JS + pi-ai completions | self-contained bun bundle; cheapest C candidate |
| pi-web-access | `node:http`, `linkedom`, `@mozilla/readability`, `turndown`, `undici`, `unpdf` | `docs/extension-compatibility-matrix.md:157` lists `linkedom` as a `parseHTML()` DOM stub, while `docs/extension-catalog.json:5970` records a real load failure (`Unsupported module specifier: linkedom`) |
| pi-mcp-adapter | `@modelcontextprotocol/client` (only the stub form `@modelcontextprotocol/sdk` is listed, matrix `:150`), `@napi-rs/keyring`, `unix-socket-transport.ts`, `quickjs-wasi` | `node:net` Stub + `createServer` throws (`matrix:29`) rule out its local OAuth callback server; the Rust `pub mod mcp` owns this capability |
| pi-plan-mode | mode state, tool blocking, bash filtering, `completeSimple` | mirrors `submit_plan` + plan settings + `src/bash_mediation.rs` natively |
| pi-ast-grep | `promptSnippet`, `promptGuidelines`, spawns `@ast-grep/cli` binary | `registerTool` forwards neither key (`src/extensions_js.rs:21559-21566`) → tool arrives without its prompt guidance |
| pi-agent-browser-native | `cross-spawn`, `which`, `path-key`, node ≥ 24.21 | `node:child_process` is Full, so spawn works; the node version floor is a host-process constraint, not a QuickJS one |
| pi-cross-session | `node:net` (`createConnection`, `Socket`, `createServer`) | `docs/extension-compatibility-matrix.md:29`: `node:net` **Stub**, `createServer` throws → transport cannot run |
| pi-extensions-i18n | pure JS + JSON catalogs | runtime-neutral; supports distill/tool-display |
| pi-extensions-tool-display | TUI renderers + tool-result protocol | same UI channel exists natively; Rust equivalent renderers live in `src/tui.rs` / rich_rust |
| pi-model-request | `pi-ai` auth resolution + provider session headers | Rust equivalents: `src/auth.rs`, `src/providers/`, `src/model.rs` |
| ponytail | `skills/`, `hooks/`, `commands/` in a shared plugin repo | Rust skills loader reads global/project/package skills (`docs/skills.md:9`) |
