# Security

Gonk 0.1 is built to be used by one person, on machines that person owns,
over a private network. This document says what that does and does not
protect, so that nobody (including future Marc) assumes more.

## What is being protected

| Asset | Why it matters |
| --- | --- |
| `gonksystem` | Source code, credentials, SSH keys, everything else on a main machine. |
| Device tokens | A token lets its holder call every capability that policy allows. |
| The borrowed machine | It belongs to someone else. Gonk should leave it no worse. |
| The install path | Whoever controls what `marclevin.me/gonk` serves runs code on every machine Gonk lands on. |

## Who is trusted

| Party | Trusted to | Not trusted to |
| --- | --- | --- |
| Marc, at a terminal | Do anything. Gonk is not a sandbox against its own user. | |
| The user account on `gonksystem` | Hold config, token hashes and plugins. Anyone who can write to that account already owns the machine. | |
| A paired device | Call the capabilities that policy allows. | Call anything else, or choose what runs. |
| An AI client over MCP | Call the capabilities that policy allows, with arguments it chooses. | Be careful. Assume its arguments may come from text it was tricked by. |
| The tailnet | Carry traffic privately between Marc's devices. | Authenticate a caller. Being on the tailnet is not enough; a token is still required. |
| GitHub, Astral, Anthropic, Tailscale, OS package repositories | Serve honest installers over HTTPS. | |
| The borrowed machine | Run Gonk. | Keep a secret. See "Borrowed machines" below. |

## What Gonk does about it

### The bootstrap contains no secrets and never asks for root

`lander/install.sh`, `install.ps1` and the two shims are public. They contain
URLs and nothing else, and tests check them for anything token-shaped. They
install into the home directory only, never run `sudo`, and never edit shell
profiles. The work happens in a function called on the last line, so a
download that is cut short runs nothing.

### There is no remote shell

No capability accepts a command line. This is tested: the suite fails if any
built-in capability grows a parameter called `command`, `argv`, `shell` or
similar.

- `commands.run` takes a **name**. The command line behind that name is
  written by the owner in `config.yaml` and the caller cannot add to it.
- `services.restart` and `services.status` take a service name that must
  appear in `services.allowed`.
- `projects.status` takes a project name and looks the path up itself.
- `files.read` takes a path, and is the one place a caller supplies
  something path-shaped. See below.

Every subprocess is started from a list of arguments. Nothing a caller
supplies is ever placed in a shell string.

### Everything beyond read-only is off until you switch it on

| Risk | Default | To allow |
| --- | --- | --- |
| safe | on | |
| sensitive | off | a matching entry in `policy.allow`; wildcards work |
| dangerous | off | its exact name in `policy.allow`; **wildcards never count** |

`policy.allow: ["*"]` therefore switches on every sensitive capability and
no dangerous one. `policy.deny` always wins. A capability that does not
declare its risk is treated as sensitive.

Capabilities that policy denies are not listed to clients, and calling one
gives the same answer as calling something that does not exist.

Policy decides *whether* a capability can be called. The capability decides
*on what*. Allowing `services.restart` does nothing until `services.allowed`
also names a service.

### `files.read`

1. Nothing is readable until `files.roots` lists a directory.
2. The requested path and every root are fully resolved (symlinks, `..`)
   before they are compared, so a link inside a root that points outside it
   is refused.
3. The file is opened with `O_NOFOLLOW`, in case it is swapped for a link
   between the check and the read.
4. Files that look like credentials are refused even inside a root: `.env`,
   `*.pem`, `*.key`, `id_rsa*`, `credentials*`, `.netrc`, `.npmrc` and
   similar, and anything under `.ssh`, `.gnupg`, `.aws`, `.kube`, `.docker`
   or `.git`.
5. Gonk's own config and state directories are never readable.
6. Reads are capped at `files.max_bytes`, and binary files are refused.

The list in step 4 is a safety net, not a guarantee. A secret in a file
called `notes.txt` will be read. **Open directories that hold source code,
not your home directory.**

### The agent

- **No anonymous routes.** All three routes require a device token,
  including the one used for health checks.
- **Tokens** are 256 random bits, shown once, and stored only as SHA-256
  hashes in `devices.json` (mode 0600). Comparison is constant-time.
- **Revocation** is `gonk agent token revoke <device>` and takes effect on
  that device's next request. No restart is needed.
- **Binding.** The agent listens on `127.0.0.1` by default, or on the
  machine's tailnet address with `agent.bind: tailscale`. Any other address,
  including a LAN address and `0.0.0.0`, is refused unless
  `agent.allow_public_bind` is set.
- **No added privilege.** It runs as a systemd *user* service with
  `NoNewPrivileges=yes`. It never uses `sudo`. Restarting a system service
  works only if the machine's own polkit rules allow that user to.
- **Bounded input.** Request bodies over 64 KiB are dropped while being read.
- **Errors do not leak.** If a capability crashes, the caller is told that it
  failed; the exception goes to the audit log only.

### The audit log

`~/.local/state/gonk/audit.log`, one JSON object per line, mode 0600. View it
with `gonk log`. It records:

- every capability call, with the device name, arguments and risk
- every refused call, with the reason
- every request with a missing or wrong token
- token creation and revocation, agent start, config changes, tool installs,
  and each use of `gonk home shell`

A capability call is recorded **before** it runs. If the log cannot be
written, the call does not happen. Tokens are never logged. Values are
stripped of control characters, so a caller cannot forge log lines or send
escape sequences to the terminal of whoever reads the log.

### Reaching home

Gonk never opens a port on the client, never asks for a port forward, and
never touches SSH host-key checking: whatever `~/.ssh/config` says, applies.
`home.host`, `home.user` and `home.tmux_session` are validated before they
are handed to `ssh`, so a value like `-oProxyCommand=…` is refused rather
than interpreted.

### Installing tools

`gonk land` shows every command before running any, and asks first. Package
names are validated, so a catalog entry cannot smuggle an option to `apt`.
Installer scripts are fetched only from `https://` URLs, fetched completely
before any of it runs, and the URL is passed as an argument, never pasted
into a shell string. Only system package managers use `sudo`; npm packages
install under `~/.local`.

### Plugins

A plugin is code that runs as you. Gonk refuses to load plugins from a
directory or file that is owned by another user or writable by group or
others. A plugin cannot replace a built-in capability.

## What is not protected yet

These are known, accepted for 0.1, and listed so they are not forgotten.

1. **The agent speaks plain HTTP.** Over a tailnet, WireGuard encrypts it.
   On loopback, only root can observe it. Anywhere else, the token and every
   response would cross the network in the clear. This is the reason the
   bind guard exists.
2. **One policy for everything.** The agent and a local MCP server on the
   same machine share `policy`. There is no way to give a laptop more than a
   phone, or the agent more than an AI client.
3. **Tokens do not expire** and are not bound to a device. A copied token
   works from anywhere that can reach the agent, until it is revoked.
4. **Tokens are stored in a file**, mode 0600, not in the OS keyring. Anyone
   who can read your files, or your backups, can read `credentials.yaml`.
5. **No rate limiting.** Guessing a 256-bit token is not feasible, but
   failed attempts are logged without limit, so a device on the tailnet
   could fill the disk. The log is not rotated.
6. **The install chain is trusted, not verified.** Nothing is pinned by hash
   or signed. `GONK_VERSION=v0.1.0` pins a tag, and a tag can be moved. If
   the GitHub account or the website is taken over, the next
   `curl … | bash` runs the attacker's code.
7. **Third-party installers are trusted the same way**: uv, Claude Code and
   Tailscale are installed by running their official scripts.
8. **"Safe" is not "secret".** Safe capabilities reveal hostnames, running
   services, process names, project names and installed tools to every
   paired device and to any AI client. Use `policy.deny` if that is too much.
9. **The secret-file list is a heuristic.** See `files.read` above.
10. **`commands.run` output is returned as-is.** If a named command prints a
    secret, the caller receives it.
11. **Windows.** File modes such as 0600 mean little there, and the plugin
    ownership check is skipped. The Windows installer has not been run on a
    real machine.
12. **Prompt injection is mitigated, not solved.** An AI client can be talked
    into calling any tool it has. The defence is that the tools it has are
    few, constrained, and mostly read-only. Think before allowing a
    dangerous capability on a machine an AI client can reach.

### Borrowed machines

A borrowed machine may have a keylogger, a curious administrator, or
backups you do not control. On one:

- Prefer `GONK_HOME_TOKEN=… gonk home status` over `gonk home pair`, so the
  token is never written to disk.
- Give each borrowed machine its own token (`gonk agent token create
  library-pc`) and revoke it when you leave.
- Remember that `gonk home shell` uses your SSH key or password. Gonk cannot
  protect those from the machine you type them into. A hardware key helps.
- When you are done: `uv tool uninstall gonklander` and delete
  `~/.config/gonk`.

## Before exposing Gonk to the public Internet

Do not set `agent.allow_public_bind` until all of these are true.

- [ ] **TLS**, with a certificate clients verify. Terminate it in the agent
      or in a reverse proxy on the same host.
- [ ] **Tokens that expire**, with a way to renew them, and ideally bound to
      a device key so a copied token is useless.
- [ ] **Rate limiting and lockout** on failed authentication, and log
      rotation.
- [ ] **Per-device policy**, so that each token carries only what that
      device needs.
- [ ] **A network MCP endpoint with real authorization** (OAuth, as the MCP
      specification describes), if ChatGPT or another hosted client is to
      connect. Never an open endpoint, and never the device token in a URL.
- [ ] **Verified installs**: releases pinned by hash or signed, and the
      shim installing a release rather than a branch.
- [ ] **Dangerous capabilities reviewed one by one**, assuming the caller is
      hostile.
- [ ] **A way to see and act on the audit log** without being at the
      machine: alerts on refused calls and unknown tokens.
- [ ] **Someone other than the author has tried to break it.**

The better answer for most of these is to not expose it at all. An outbound
tunnel with its own authentication in front (Tailscale Funnel with an
identity-aware proxy, or Cloudflare Tunnel with Access) keeps the agent on a
private address and puts a maintained product on the public one.

## Reporting a problem

This is a personal project. Open an issue on the repository, or for anything
sensitive, contact Marc directly rather than in public.
