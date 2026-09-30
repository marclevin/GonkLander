# GonkLander plan

A checklist. `[x]` means built **and** verified; `[~]` means built but not
verified in real conditions, with the reason; `[ ]` means not done.

Last updated: 2026-09-30, at version 0.1.0.

## Milestone 0 — Design

- [x] Inspect environment
- [x] `SPEC.md`
- [x] `PLAN.md`
- [x] `README.md`
- [x] Identify security-sensitive decisions (`SPEC.md` §13, `docs/security.md`)

## Milestone 1 — Core and first vertical slice

- [x] Project skeleton, uv, ruff, mypy (strict), pytest
- [x] `core`: errors, ui, paths, system runner, platform detection
- [x] `core`: configuration with env overrides
- [x] `core`: audit log
- [x] Tool catalog (22 tools) and profiles (minimal, dev, ai, full)
- [x] Providers: apt, dnf, pacman, brew, winget, npm, script
- [x] Install planner (present / upgrade / install / unavailable)
- [x] `gonk land`, `gonk tools list`, `gonk tools install`, `gonk profiles`
- [x] `gonk doctor` (and `--json`)
- [x] `gonk status`, bare `gonk`
- [x] `gonk config path|show|init|set`
- [x] `gonk update`
- [x] `gonk log`
- [x] `lander/install.sh` — run from nothing in an empty home directory,
      including installing uv; run twice
- [~] `lander/install.ps1` — written; **never run**. No Windows machine or
      PowerShell was available. CI parses it for syntax errors, which is
      all the checking it has had.

What "verified" means for installing tools: the planner and installer are
tested against a simulated machine, and `gonk land --dry-run` was run for
real. **No package was actually installed by `gonk land` during
development**, because that needs `sudo` on a real machine. The first real
`gonk land dev` is yours; see "Needs Marc".

Of the providers, only `apt` has been pointed at a real system (in dry-run).
`dnf`, `pacman`, `brew` and `winget` produce the commands their
documentation describes and have never been executed.

## Milestone 2 — Home

- [x] Transport abstraction, `tailscale` and `ssh`
- [x] `gonk home status` — every failing layer tested; the success path was
      run for real against a local agent
- [~] `gonk home shell` — the command line it builds is tested, and that it
      refuses to start when home is unreachable. **It has never opened a
      real SSH session**: the development machine is `gonksystem` itself and
      runs neither sshd nor Tailscale.
- [~] `gonk home code` — same: the command line is tested, VS Code was never
      launched by it.
- [x] `gonk home pair`
- [~] The Tailscale checks are tested against sample `tailscale status
      --json` output, not a live tailnet.

## Milestone 3 — Capabilities, agent, MCP

- [x] Capability registry and JSON schema generation
- [x] Policy layer
- [x] Built-in capabilities (12)
- [x] Plugin loading, with ownership and permission checks
- [x] Agent: HTTP service, device tokens, revocation, bind guard — run for
      real on loopback and exercised with curl
- [~] Agent as a systemd user service — the unit file passes
      `systemd-analyze verify`. **It was not installed**, because that is a
      lasting change to your machine.
- [~] Agent bound to a tailnet address — logic tested; no tailnet to try it on.
- [x] Agent client
- [x] MCP server, local mode — driven by a real MCP client over stdio
- [x] MCP server, home mode — driven by a real MCP client, through a real
      agent on loopback
- [x] `gonk mcp list|status|install|serve`
- [~] `gonk mcp install --apply` — not run, as it would change your Claude
      Code configuration.

## Milestone 4 — Quality

- [x] Tests: 377, about 2 seconds, none touch the real machine or the network
- [x] GitHub Actions CI — green on the first push, including a job that
      runs the installer twice on a clean Ubuntu runner
- [x] `docs/security.md`
- [x] `docs/website.md`
- [x] `docs/plugins.md`
- [x] End-to-end check of the first usable milestone, in an empty home directory
- [x] Self-review for security and needless complexity; findings fixed (below)
- [x] Documentation matches reality

### Found and fixed during review

| Found by | Problem | Fix |
| --- | --- | --- |
| tests | `install.sh` crashed with `GONK_PROFILE` set and no terminal: `/dev/tty` existed but could not be opened | try opening it, fall back to `--yes` |
| tests | Errors from tools served from home reached AI clients without their hints | `GonkError` renders hints as plain text |
| tests | `gonk mcp list` did not fit an 80-column terminal | narrower table, reasons listed below it |
| review | The agent read a whole request body before checking its size | size enforced while reading |
| review | A crafted capability name could put terminal escape sequences in `gonk log` | control characters stripped before logging |
| review | `files.read` could read Gonk's own tokens and audit log if `~` was opened | Gonk's directories are always refused |
| review | `files.read` had a window between checking a path and opening it | opened with `O_NOFOLLOW` |
| review | `.git/config` was readable, and can contain tokens in remote URLs | `.git` added to refused directories |
| review | A crashing plugin's exception text was sent to MCP clients | caller sees "failed unexpectedly"; details go to the audit log |
| review | Config directory was created world-readable | created 0700 |
| review | `Platform.is_distro` was unused | removed |

## Needs Marc

Nothing below could be done without your credentials, your machines, or a
decision that is yours.

1. ~~Commit and publish.~~ Done: https://github.com/marclevin/GonkLander,
   public, CI green. Installing straight from GitHub was verified:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/marclevin/GonkLander/main/lander/install.sh | bash
   ```

2. **Choose a licence.** None has been added, and the repository is public.

3. **Put the shims on marclevin.me.** Two files; see `docs/website.md`.

4. **Install on gonksystem and land for real.** This is the first time
   `gonk land` will actually install anything:

   ```bash
   ./lander/install.sh
   gonk land dev
   ```

5. **Set up the network.** Tailscale is not installed on `gonksystem`, and
   neither is an SSH server:

   ```bash
   gonk tools install tailscale && sudo tailscale up
   sudo apt-get install -y openssh-server
   ```

6. **Start the agent on gonksystem:**

   ```bash
   gonk config set agent.bind tailscale
   gonk agent token create laptop
   gonk agent install && gonk agent start
   sudo loginctl enable-linger $USER     # keeps it running when you log out
   ```

7. **Try it from a second machine.** This is the real test of Milestone 2.
   Please report what `gonk home status` prints if anything fails.

8. **Try the Windows installer**, if you care about Windows.

## Later

Roughly in order of value.

- [ ] **Per-interface and per-device policy.** Let a phone have less than a
      laptop, and an AI client less than either.
- [ ] **Tokens in the OS keyring**, with expiry.
- [ ] **`gonk context`.** Deliberately not built: see `SPEC.md` §5. Worth
      designing once there are two real contexts to compare.
- [ ] **Network MCP endpoint**, for ChatGPT and other hosted clients. Needs
      OAuth in front of it; see the checklist in `docs/security.md`.
- [ ] **Pinned, verified installs**: a `stable` branch, release tarballs
      with checksums.
- [ ] **First domain plugins**: `mphil.*`, `verascient.*`, `trustmebank.*`.
- [ ] **macOS**: run the installer and `gonk land` on a real Mac.
- [ ] **Dotfiles.** "Marc-compatible" arguably includes shell and editor
      configuration; profiles only install tools today.
- [ ] **Audit log rotation** and rate limiting on failed authentication.
- [ ] **A third transport**: an outbound tunnel, for networks that block
      Tailscale.
- [ ] **`gonk land --undo`**, or at least a record of what Gonk installed on
      a borrowed machine, so it can be left as it was found.
