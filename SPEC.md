# GonkLander specification

Version 0.1. This document describes what Gonk is, how it is put together, and
why. It is kept in step with the code: if the two disagree, that is a bug.

> **Land on an unfamiliar machine, run one command, and make it Marc-compatible.**

## 1. What Gonk is

Gonk is four things that share one codebase:

| Piece | What it does | Where it runs |
| --- | --- | --- |
| **Lander** | A public, secret-free bootstrap script that installs the `gonk` CLI. | Any machine |
| **`gonk` CLI** | Diagnoses the machine, installs tool profiles, reaches home. | Any machine |
| **Gonk Agent** | Serves a fixed set of *capabilities* to authenticated devices. | Machines Marc owns (`gonksystem`) |
| **Gonk MCP** | Serves the same capabilities to AI clients over MCP. | Either side |

Non-goals for 0.1: multi-user support, a web UI, a database, public-Internet
exposure, configuration management beyond "install these tools".

## 2. Repository layout

```text
GonkLander/
├── lander/                  Public bootstrap. No Python, no secrets.
│   ├── install.sh           Linux/macOS installer
│   ├── install.ps1          Windows installer
│   └── shim/                Two tiny files to host at marclevin.me
├── src/gonk/                One Python package
│   ├── cli/                 Typer commands, one module per command group
│   ├── core/                platform, config, paths, system runner, ui, errors, audit
│   ├── tools/               tool catalog, profiles, providers, install planner
│   ├── doctor.py            the diagnostic report
│   ├── home/                transports (tailscale, ssh) and the agent client
│   ├── capabilities/        capability registry + built-in capability modules
│   ├── policy.py            the allow-list that gates every capability call
│   ├── agent/               HTTP agent, device tokens, systemd unit
│   ├── mcp/                 MCP server
│   └── data/                tools.yaml, profiles/*.yaml, config.example.yaml
├── docs/                    security.md, website.md, plugins.md
├── scripts/                 check.sh: everything CI runs
├── tests/
└── .github/workflows/       ci.yml
```

### Deviations from the suggested layout, and why

The brief suggested top-level `cli/`, `agent/`, `mcp/` and `packages/`
directories. Gonk uses **one installable Python package with sub-packages**
instead:

- The CLI, agent and MCP server share most of their code (config, policy,
  capabilities). Separate packages would mean a uv workspace, four
  `pyproject.toml` files and version skew between them, for no benefit at this
  size.
- One package means one install command, one version number, one thing to
  update. `gonk agent run` and `gonk mcp serve` are just subcommands.
- The boundaries still exist, as import rules (section 3) rather than as
  packaging.

Profiles live in `src/gonk/data/profiles/` rather than `lander/profiles/`
because they must ship *inside* the installed package: `gonk land dev` has to
work on a machine that has no checkout of this repository.

There is no top-level `config/` for the same reason. The annotated example
configuration is `src/gonk/data/config.example.yaml`, which is the file
`gonk config init` writes, so the example and the real thing cannot drift
apart. A test checks that the example parses cleanly and matches the
built-in defaults.

There is no `packages/` directory because there is only one package.

If the agent ever needs to be deployed without the CLI, splitting
`gonk.agent` out is a mechanical change because of the import rules below.

## 3. Internal boundaries

Dependencies point downwards only:

```text
            cli
   ┌─────┬───┴───┬────────┐
 doctor  mcp   agent    home
   │      └──┬───┘        │
   │    capabilities      │
   │      policy          │
   └────────┬─────────────┘
       tools, core
```

- `core` imports nothing else from Gonk.
- `capabilities` never imports `agent`, `mcp` or `cli`. A capability does not
  know who is calling it.
- `agent` and `mcp` are two thin front doors onto `capabilities` + `policy`.
  Neither implements any machine logic of its own.

## 4. The lander

`lander/install.sh` does exactly this:

1. Detect OS and architecture; refuse politely on unsupported platforms.
2. Check prerequisites (`curl` or `wget`, a writable home directory).
3. Install `uv` with the official installer if it is missing.
4. Install `gonk` as a uv tool, from a local checkout if the script is being
   run from one, otherwise from a release tarball of this repository.
5. Make sure `~/.local/bin` is reachable, and say so if it is not.
6. Optionally hand over to `gonk land <profile>` (`GONK_PROFILE=dev`).

Properties:

- **No secrets.** The script is public. It contains URLs and nothing else.
- **No root.** Everything lands in the user's home directory. Root is only
  ever requested later, by `gonk land`, for system package managers.
- **Idempotent.** Re-running upgrades or repairs the install. It never
  appends to shell rc files; it prints the line to add instead.
- **Safe against truncation.** The whole script is wrapped in a function that
  is called on the last line, so a half-downloaded script does nothing.
- **Python not required.** uv downloads a suitable Python if the machine has
  none, so the only real prerequisite is `curl`.

Knobs (all environment variables, all optional):

| Variable | Meaning | Default |
| --- | --- | --- |
| `GONK_VERSION` | Git tag or branch to install | `main` |
| `GONK_REPO` | `owner/name` on GitHub | `marclevin/GonkLander` |
| `GONK_SOURCE` | Explicit source (path or URL), overrides both above | unset |
| `GONK_PROFILE` | Profile to land after install | unset (none) |

`install.ps1` mirrors this for Windows. It is implemented but **has not been
run on a real Windows machine**; see PLAN.md.

## 5. The CLI

```text
gonk                         short status + where to go next
gonk status                  one-screen summary
gonk doctor [--json]         full diagnostic report
gonk land [profile]          install a tool profile        [--dry-run] [--yes]
gonk update                  upgrade gonk itself
gonk tools list              every known tool and whether it is present
gonk tools install <tool>    install one tool              [--dry-run] [--yes]
gonk profiles                list profiles
gonk config path|show|init|set
gonk home status|shell|code|pair
gonk agent run|install|start|stop|status|uninstall
gonk agent token create|list|revoke
gonk mcp list|status|install|serve        [--home]
gonk log                     recent audit log entries
```

A command group called on its own does the obvious thing: `gonk tools` lists,
`gonk home` and `gonk agent` and `gonk mcp` show status, `gonk config` shows
the settings in effect.

`gonk doctor` and `gonk status` keep working when `config.yaml` is broken, and
report the breakage. Every other command stops and says what is wrong with
the file: a setting that cannot be read is never silently replaced by its
default.

Commands from the brief that are **deliberately not implemented** in 0.1:

- `gonk context list|load` — the semantics are not yet justified. The likely
  meaning is "a named bundle of project + environment + MCP tools"
  (for example `mphil`), but building it before there is a second context to
  compare against would be guessing. It is listed in PLAN.md.

### Error handling

Every expected failure is raised as `GonkError(message, checks, hints)` and
rendered as:

```text
Could not reach gonksystem.

  ✓ Tailscale installed
  ✗ Tailscale not connected

Try:
    tailscale up
```

Tracebacks are shown only with `GONK_DEBUG=1`.

## 6. Tools, providers and profiles

Three declarative pieces, all YAML, all shipped in `src/gonk/data/`:

**The catalog** (`tools.yaml`) says what a tool *is*: how to detect it, what
version is acceptable, and how each provider installs it.

```yaml
jq:
  description: Command-line JSON processor
  check: { commands: [jq], min_version: "1.6" }
  install:
    apt: { packages: [jq] }
    brew: { packages: [jq] }
    winget: { id: jqlang.jq }
```

**A profile** (`profiles/dev.yaml`) is a named list of tools, optionally
extending another profile.

```yaml
name: dev
description: Everyday development tools
extends: minimal
tools: [gh, python, uv, node, pnpm, ripgrep, jq, tmux]
```

**A provider** is a small Python class that turns a catalog entry into
commands: `apt`, `dnf`, `pacman`, `brew`, `winget`, `npm`, `script`.
Providers are the only place that knows how a package manager is invoked.

### The decision

For every requested tool the planner produces exactly one decision:

| Decision | When |
| --- | --- |
| `present` | Found on PATH and the version is acceptable. **Do nothing.** |
| `upgrade` | Found, but older than `min_version`. |
| `install` | Not found, and a provider is available. |
| `unavailable` | Not found, and no provider works on this platform. |

Planning is a pure function of (catalog, platform, what is on PATH). It runs
no commands that change anything, which is what makes it testable and what
makes `--dry-run` trustworthy: dry-run prints the same plan that a real run
executes.

After an install, the tool is detected again. "The command exited 0" is not
treated as success; "the tool is now on PATH" is.

Users can add their own tools and profiles in `~/.config/gonk/tools.yaml` and
`~/.config/gonk/profiles/*.yaml`. User entries override built-in ones by name.

## 7. Home

`gonksystem` is reached through a **transport**. A transport answers three
questions: is the network path up, what address do I dial, and what should the
user do if it is not working.

| Transport | Reaches home by | Checks |
| --- | --- | --- |
| `tailscale` | MagicDNS name or tailnet IP | installed, logged in, peer online |
| `ssh` | any hostname you configure (WireGuard, LAN, a jump host in `~/.ssh/config`) | name resolves |

Everything above the transport uses plain OpenSSH:

- `gonk home status` — runs the transport checks, then an SSH port probe, then
  an authenticated call to the agent. Each step is reported separately so the
  first failing layer is obvious.
- `gonk home shell` — `ssh -t <home>`, a plain login shell. `--tmux` attaches
  to a persistent tmux session instead; it is opt-in because tmux over
  Windows' ssh client garbles the terminal.
- `gonk home code` — opens VS Code Remote-SSH against home; falls back to
  explaining what is missing.
- `gonk home pair` — stores a device token for the agent.

Gonk never opens a listening port on the client and never asks for a port to
be forwarded on a router. Adding a transport (for example an outbound tunnel)
means adding one class.

## 8. Capabilities

A capability is a named, described, typed function with a risk level:

```python
@capability("machine.status", risk="safe", description="Uptime, load, memory and disk.")
def machine_status(ctx: Context) -> dict: ...
```

Built in for 0.1:

| Capability | Risk | Notes |
| --- | --- | --- |
| `gonk.status` | safe | version, hostname, platform |
| `machine.status` | safe | uptime, load, memory, disk |
| `machine.processes` | safe | top processes; **names only, never command lines** |
| `machine.services` | safe | running systemd services |
| `tools.list` | safe | tool catalog and what is installed |
| `projects.list` | safe | git repositories under configured roots |
| `projects.status` | safe | branch, dirty state, last commit of one project |
| `services.status` | sensitive | only services named in `services.allowed` |
| `files.read` | sensitive | only under `files.roots`, size-capped, secret-looking files refused |
| `commands.list` | sensitive | the names `commands.run` accepts |
| `services.restart` | dangerous | only services named in `services.allowed` |
| `commands.run` | dangerous | runs a *named* command from config; caller supplies no arguments |

There is no capability that accepts a command line, and there will not be one.
`commands.run` takes a name that must already exist in the owner's config,
mapped to a fixed argv.

### Plugins

A plugin is one Python file in `~/.config/gonk/plugins/`. It uses the same
decorator as the built-ins. Nothing else in the application changes. See
`docs/plugins.md`.

Plugins that do not declare a risk level are treated as `sensitive`, so a new
plugin is invisible until it is explicitly allowed.

## 9. Policy

Every call, from the agent or from MCP, goes through one function:
`Policy.check(capability)`.

```yaml
policy:
  allow: ["services.status", "files.read"]
  deny: []
```

Rules, in order:

1. If a `deny` pattern matches, the answer is no.
2. `safe` capabilities are allowed.
3. `sensitive` capabilities are allowed if an `allow` pattern matches
   (wildcards permitted, e.g. `mphil.*`).
4. `dangerous` capabilities are allowed only if `allow` contains their **exact
   name**. A wildcard never grants a dangerous capability.

Denied capabilities are not listed to clients at all; an AI client cannot see
what it is not allowed to call. Calling one gives the same answer as calling
something that does not exist.

Every call passes through one object, the `Gate` (`capabilities/invoke.py`),
which does four things in order: look the capability up, ask policy, write the
audit entry, run it. If the audit entry cannot be written, the capability
does not run. If the capability crashes, the caller is told only that it
failed; the exception is recorded locally.

Policy decides *whether* a capability may be called. The capability itself
enforces *what on* (which services, which directories). Both must agree.

## 10. The agent

A small HTTP service (Starlette + uvicorn) with three routes:

```text
GET  /v1/whoami              who the token belongs to, agent version
GET  /v1/capabilities        capabilities this device may call
POST /v1/call/{name}         call one
```

- **Every route requires a device token.** There is no unauthenticated
  endpoint, including health. A `401` is itself proof that the agent is up,
  which is all a diagnostic needs.
- **Binding.** Default `127.0.0.1:4665`. `agent.bind: tailscale` binds to the
  machine's tailnet address only. Binding to a wildcard or public address is
  refused unless `agent.allow_public_bind: true` is set.
- **Device tokens.** `gonk agent token create laptop` prints a token once and
  stores only its SHA-256. `gonk agent token revoke laptop` takes effect on the
  next request.
- **Runs as a systemd user service.** No root. `gonk agent install` writes
  the unit; `start`, `stop`, `status` wrap `systemctl --user`.
- **Audit.** Every call, allowed or denied, is appended to the audit log with
  the device name. So is every request with a missing or wrong token.
- **Bounded.** Request bodies over 64 KiB are dropped while being read.

The agent speaks plain HTTP. Confidentiality comes from the transport
(WireGuard, via Tailscale). This is a stated limitation; see
`docs/security.md`.

## 11. MCP

`gonk mcp serve` runs an MCP server over stdio. Each allowed capability
becomes one MCP tool. Capability `machine.status` is exposed as the tool
`gonk_machine_status`; dots become underscores because several MCP clients
reject dots in tool names.

Two modes:

- **local** (default) — capabilities run on the machine the server runs on.
- **home** (`--home`) — the server lists and calls capabilities on the agent
  at home. The agent's policy is the one that applies. This lets an AI client
  on a laptop use tools on `gonksystem`.

Tools carry MCP annotations derived from risk (`readOnlyHint`,
`destructiveHint`) so clients can prompt appropriately.

Only stdio is implemented. A network-facing MCP endpoint (needed for ChatGPT
connectors) is a deliberate non-goal for 0.1 because it must not exist without
real authentication in front of it.

## 12. Configuration

| What | Linux | macOS | Windows |
| --- | --- | --- | --- |
| Config | `~/.config/gonk/` | `~/Library/Application Support/gonk/` | `%APPDATA%\gonk\` |
| State + audit log | `~/.local/state/gonk/` | same as config | `%LOCALAPPDATA%\gonk\` |

XDG variables are honoured. `GONK_CONFIG_DIR` and `GONK_STATE_DIR` override
everything, which is how the tests stay off the real machine.

Files in the config directory:

| File | Contents | Mode |
| --- | --- | --- |
| `config.yaml` | settings; safe to read aloud | 0644 |
| `credentials.yaml` | this device's token for home | 0600 |
| `devices.json` | hashes of tokens the agent accepts | 0600 |
| `plugins/*.py` | capability plugins | not group/world writable |

Precedence: built-in defaults < `config.yaml` < environment variables.
Any setting `section.key` can be overridden with `GONK_SECTION_KEY`
(`GONK_HOME_HOST=10.0.0.5`). The home token can be supplied as
`GONK_HOME_TOKEN` so that it never touches disk on a borrowed machine.

Unknown keys in `config.yaml` are reported as warnings by `gonk doctor`
rather than silently ignored.

## 13. Security-sensitive decisions

These are the choices where getting it wrong would matter. Each is expanded in
`docs/security.md`.

1. The bootstrap script is public and therefore contains no credentials.
2. No capability takes a command line. Execution is by name, from config.
3. Unknown and plugin capabilities fail closed (`sensitive` by default).
4. Wildcards cannot grant dangerous capabilities.
5. The agent has no unauthenticated routes and refuses public binds by default.
6. Tokens are stored hashed on the agent and mode 0600 on the client.
7. `files.read` resolves symlinks before checking roots, opens with
   `O_NOFOLLOW`, refuses files that look like secrets even inside an allowed
   root, and never reads Gonk's own config or state.
8. `machine.processes` reports process names, not command lines, because
   command lines routinely contain secrets.
9. Subprocesses are always started from argv lists. Nothing the caller
   supplies is ever interpolated into a shell string.
10. Plugins are code. Gonk refuses to load a plugin that another user could
    have written to.
11. A call that cannot be written to the audit log does not happen.
12. What callers send is stripped of control characters before it is logged.
13. Configuration errors are fatal rather than defaulted, so that a typo in
    a security setting cannot quietly turn it off.

## 14. Testing

- Unit tests cover platform detection, config loading and precedence, profile
  parsing, tool detection, install decisions, CLI parsing, policy, the
  capability guards, agent authentication and the MCP tool surface.
- All system access goes through one object, `System`, which tests replace
  with a fake. No test runs a package manager, touches the real home
  directory, or needs the network.
- The bootstrap script is tested by running it for real, inside a temporary
  home directory, against a stand-in for uv.
- The MCP server is tested through a real MCP client session, in memory.
- CI runs ruff (lint and format), mypy (strict), pytest on Python 3.11 and
  3.13, shellcheck, a PowerShell parse check, and the installer twice on a
  clean runner.
- `./scripts/check.sh` runs the same checks locally.
