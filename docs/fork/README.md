# Fork plan: hardbake the user's TS pi extensions into the Rust port

> **Current status (2026-11-11):** Phase 0A is partially landed and not quality-approved.
> Two gaps are closed end-to-end (`c0ca9edcf` MCP settings timeout, `74fa82820` extension
> prompt guidance, corrected by `2eca5767f`); `hashline.grep` is wired and executing
> (`d1431abd9`), `hashline.replace_text` and the plan-mode gaps remain open.
> See [branch progress and resume notes](IMPLEMENTATION_PLAN.md#6-branch-checkpoint--incomplete-implementation).
> The model-review stub was removed in `772e8275a`. The lib-test target now compiles
> (`6a6e28dbe`) and tests execute; `origin/main` was merged in (`c0d7fcf7c`). `dsr` 0.2.3
> IS installed (`~/.local/bin/dsr`) — that earlier claim was stale — but no authoritative
> quality approval exists yet: its compile/check/clippy/test lanes all refuse with exit 103
> because the recipe sets `RCH_REQUIRE_REMOTE=1` on a host with no RCH daemon, a placeholder
> worker, and no `/data/projects` canonical root (`bd-rch-dsr-remote-refusal-hcjgg`). The
> lib-test abort is now diagnosed as QuickJS heap corruption on the `eval-js-kernel` thread
> rather than ~35 flaky tests (`bd-p0a-sigtrap-4pqc0`); the failure list was a crash-cutoff
> artifact.
> The A/B/C options below are historical exploration: the current plan ports all
> fifteen extensions to native Rust, without dropping capabilities.

## Why this fork exists

The upstream Rust port is not a drop-in for TypeScript Pi, and it should not become one
(`AGENTS.md` — "Product Direction: OMP-Inspired, Not a Legacy Pi Drop-In"). What we actually want
is narrower: the fifteen extensions this machine runs should keep working, but as first-class
parts of the Rust binary rather than as third-party JS bolted on at runtime.

"Hardbake" therefore means, per extension, one of three things:

| | Verdict | Meaning in this fork |
|--|--|--|
| **A** | already native | delete the extension from the bundle; the Rust module already provides the behavior. Verify the anchor before claiming it. |
| **B** | port to Rust | the capability is product-central and the surface is small enough that a native module is cheaper to maintain than a JS runtime dependency. |
| **C** | keep as bundled TS | the surface is large or host-coupled; ship it on the existing QuickJS PiJS bridge (`src/extensions_js.rs`) and pin the capability policy it needs. |

Feasibility decides the verdict, not topical overlap: if the extension needs a host module the
runtime stubs (`docs/extension-compatibility-matrix.md`), it cannot be C.

## Decision table (15 extensions + 1 skill)

| Extension | Verdict | Rationale (one line) |
|---|---|---|
| pi-hashline-edit | **A** | `hashline_edit` is an essential built-in (`src/xdev.rs:67` in `ESSENTIAL_DEFAULTS`, `src/tools.rs:5709`). |
| pi-plan-mode | **A** | `submit_plan` is essential (`src/xdev.rs:74`) and plan settings are native (`src/config.rs:93`, `src/plan.rs`). Planning blocks mutations at tool-effect level (`PlanState::allows_effects` rejects write/append/process under `Planning`/`PendingApproval`), so the mode is enforced; what is NOT wired is per-command bash read-only filtering — `plan_mode_classes` (`src/bash_mediation.rs:436`) is called only by its own tests, so gaps 3.1/3.2 stay open. |
| pi-ast-grep | **A** | Native `ast_grep`/`ast_edit` sit in the discoverable tier (`pub mod ast_tools`, `src/lib.rs:105`; `src/tools.rs:5696-5697`). The bridge now forwards `promptSnippet`/`promptGuidelines` and they reach the system prompt (`74fa82820`, `2eca5767f`), so dropping the TS extension no longer costs that guidance — but the native tools carry no snippet of their own yet, so the guidance text itself still needs porting. |
| pi-agent-browser-native | **A** | Native CDP browser: `pub mod browser` (`src/lib.rs:113`), `BrowserSnapshot` + `BrowserElementRef{ref_id,tag,role,text,selector}` (`src/browser.rs:39-54`), `browser` in `KNOWN_TOOL_NAMES` (`src/tools.rs:5699`), settings at `src/config.rs:105`. Gap: its `doctor`/config CLIs and Electron paths have no Rust equivalent. |
| pi-mcp-adapter | **A** | Native `pub mod mcp` + `src/mcp.rs`, tracked in `docs/mcp-progress.md`. Its OAuth keyring (`@napi-rs/keyring`) and `unix-socket-transport.ts` cannot run in the sandbox anyway, so C is the weaker option. |
| pi-web-access | **A** | `web_search` is essential (`src/xdev.rs:73`); URL reads are native (`src/url_read.rs`, `src/url_router.rs`, `src/web_remote.rs`). Accepting the loss of the 27 search providers named in its description is the cheaper trade than carrying a `linkedom` load failure (`docs/extension-catalog.json:5970` vs the DOM stub claim at `docs/extension-compatibility-matrix.md:157`). |
| pi-subagents | **B** | Native opt-in `subagent` exists (`src/xdev.rs:82`, `OPT_IN_ONLY`; `src/subagents.rs`), but it does not reproduce the tool-grant model: the extension keys on `registerTool({exposure:"hidden"|"direct"})`, and the Rust `registerTool` validator accepts only `name`/`description`/`parameters`/`label`/`execute`. Port the grant semantics. |
| pi-crew | **B** | Team/work-graph/worktree/lease machinery has no Rust equivalent; `src/agent_hub.rs`, `src/hub.rs`, `src/worktree_iso.rs`, `src/validation_broker.rs` are adjacent, not equivalent. Its broker IPC is also blocked by the `node:net` stub (`docs/extension-compatibility-matrix.md:29`). |
| pi-fold | **B** | Overlaps `src/compaction.rs` / `src/compaction_snap.rs` / `src/compaction_worker.rs` (`src/lib.rs:123-127`), but lossless oldest-context-only folding is a different contract — extend the native compactor rather than keep a JS fold pass. |
| pi-cross-session | **B** | Registered tools `list_pi`/`send_pi_message`/`peer_goal` ride a Unix-socket IPC that cannot load: `node:net` is Stub and `createServer` throws (`docs/extension-compatibility-matrix.md:29`). A Rust session-to-session channel is the only honest option. |
| pi-extensions-tool-display | **B** | Its job — rendering tool results — belongs to the Rust renderers (`src/tui.rs`, rich_rust); keeping it also drags `pi-extensions-i18n` in through its `pi.extensions` sibling path. |
| pi-advisor-flow | **C** | Self-contained bun-built bundle (`pi.extensions: ["./dist/index.js"]`), no Node transport; completions go through the supported `pi-ai` host bridge (`completeSimple`/`streamSimple`, matrix `:196`/`:198`). |
| pi-distill | **C** | Needs session entry types + message renderers, both present in the extension layer (`pi.events("registerMessageRenderer")`, `docs/extension-architecture.md:177`; `ExtensionUiRequest` channel). Its `pi.extensions` also bundles i18n + tool-display — if tool-display lands as B, re-test pi-distill against the native renderer before shipping. |
| pi-extensions-i18n | **C** | Pure JS + JSON catalogs, runtime-neutral; the substrate pi-distill and tool-display need. |
| pi-model-request | **C** | Thin wrapper over the `pi-ai` SDK surface the bridge already hosts. Rust equivalents exist (`src/auth.rs`, `src/providers/`), but the extension is the smaller change until something consumes them. |
| ponytail skill | **C** | Primarily a skill bundle (`skills/`, `commands/`, `hooks/` at repo root, plus a private `pi-extension/index.js`). Rust discovers skills from global/project/package roots (`docs/skills.md:9`, `src/resources.rs`) — copy the directory, don't port it. |

Totals: **A** 6 · **B** 5 · **C** 5.

Two rows are coupled, and the fork must land them in dependency order: pi-distill (C) declares
i18n (C) and tool-display (B) in its `pi.extensions`; pi-extensions-tool-display (B) likewise
declares i18n. Porting tool-display to native renderers invalidates pi-distill's sibling path,
so pi-distill is only shippable after the native renderer exists and pi-distill re-renders against
it. Do not land pi-distill first.

## How to execute the fork (order matters)

1. **Vendor, don't npm-install.** C-class extensions become checked-in trees under a fork bundle
   dir; their `pi.extensions`/`pi.skills` entries are then passed explicitly (`--extension`,
   `src/cli.rs`). Auto-discovery of npm `packages` on the Rust side was **not** verified —
   treat explicit paths as the contract.
2. **Preserve sibling layout** for the three packages that reference `../pi-extensions-i18n/index.ts`,
   or rewrite the entries. Otherwise a bundle half-loads.
3. **Pin the policy.** Each C extension declares which hostcalls it needs; the fork records the
   minimum `PolicyProfile` (`safe|balanced|permissive`, `src/config.rs:731-737`) instead of letting
   the user discover the failure at runtime.
4. **For each A verdict, remove the extension and add a line to `docs/fork/README.md`** pointing at
   the native anchor, so the claim stays falsifiable.
5. **For each B verdict, open one bead per row** with user-visible acceptance criteria before
   writing Rust. Nothing in this document is an implementation claim.
6. Any registry change goes through the tier tables (`src/xdev.rs:59-82`) and `KNOWN_TOOL_NAMES`
   (`src/tools.rs:5694`) together — `src/xdev.rs:392-406` and the registry test fail otherwise.

## Open questions the plan cannot resolve from the code

- **Do B rows get implemented in this fork, or only declared?** The task packet says documentation,
  so this document declares them. Implementation needs a planner→worker pass per row.
- **pi-cross-session:** port a Rust IPC channel, or drop the extension and document the loss? Both
  are defensible; the fork should not pick silently.
- **pi-web-access A verdict accepts losing provider breadth.** If any of the 27 search providers
  named in its `package.json` description is a hard requirement, that row flips to B (native
  provider plugins), not C.

## Related documents

- `docs/fork/rust-architecture.md` — the surfaces this plan touches, with file anchors.
- `docs/fork/extension-inventory.md` — per-extension purpose, public surface, SDK dependencies.
- Existing runtime docs (authoritative, not duplicated here): `docs/extension-architecture.md`,
  `docs/extension-compatibility-matrix.md`, `docs/ext-compat.md`, `docs/extension-runtime-threat-model.md`,
  `docs/mcp-progress.md`, `docs/skills.md`, `docs/sdk.md`.

These are planning documents only. They certify nothing; the retired drop-in ledger
(`docs/evidence/dropin-parity-gap-ledger.json`) is historical and is not a gate for any row above.
