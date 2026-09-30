# GonkLander

> Land on an unfamiliar machine, run one command, and make it Gonk-compatible.

GonkLander is a small personal infrastructure project. The command is `gonk`.

It does four things:

1. **Lands** — one public script installs the `gonk` CLI on a new machine.
2. **Equips** — `gonk land dev` installs the tools you expect, and skips
   whatever is already there.
3. **Phones home** — `gonk home shell` drops you into your real development
   machine over a private network.
4. **Talks to agents** — `gonk mcp serve` gives Claude, ChatGPT and friends a
   small, allow-listed set of tools instead of a shell.

## Install

From a checkout:

```bash
./lander/install.sh
```

From anywhere, once the stable URL is set up (see [docs/website.md](docs/website.md)):

```bash
curl -fsSL https://marclevin.me/gonk | bash
```

```powershell
irm https://marclevin.me/gonk.ps1 | iex
```

The installer needs `curl` and nothing else, not even Python. It does not
need root, it does not edit your shell profile, and it is safe to run again.

Platform status: Linux is tested. macOS uses the same installer and should
work but has not been tried. The Windows installer is written and has not
been run on a real machine.

## First five minutes

```bash
gonk doctor              # what is this machine, and what is missing?
gonk land dev --dry-run  # what would be installed?
gonk land dev            # install it
gonk tools list
gonk status
```

## Reaching home

On the machine you are borrowing:

```bash
gonk config set home.host gonksystem
gonk config set home.user marc
gonk home status         # checks each layer and says which one is broken
gonk home shell          # ssh + tmux on gonksystem
gonk home code           # VS Code Remote-SSH against gonksystem
```

## The agent

On a machine you own:

```bash
gonk agent token create laptop   # prints a token, once
gonk agent install               # systemd user service
gonk agent start
gonk agent status
```

On the client:

```bash
gonk home pair                   # paste the token
gonk home status                 # now includes the agent
```

## MCP

```bash
gonk mcp list            # tools, their risk, and whether policy allows them
gonk mcp serve           # stdio MCP server, local tools
gonk mcp serve --home    # stdio MCP server, tools run on gonksystem
gonk mcp install         # how to register it with Claude Code / Claude Desktop
```

Safe, read-only tools are available by default. Anything that reads files or
changes state has to be switched on in `config.yaml`:

```yaml
policy:
  allow:
    - files.read
    - services.restart   # dangerous: must be named exactly, wildcards do not count
```

## Adding your own tool

Drop a file in `~/.config/gonk/plugins/`:

```python
from gonk.capabilities import Context, capability


@capability("verascient.status", risk="safe", description="Is Verascient up?")
def status(ctx: Context) -> dict:
    return {"up": True}
```

It shows up in `gonk mcp list` and, if policy allows it, as the MCP tool
`gonk_verascient_status`. See [docs/plugins.md](docs/plugins.md).

## Documentation

| Document | What is in it |
| --- | --- |
| [SPEC.md](SPEC.md) | Architecture and the reasons behind it |
| [PLAN.md](PLAN.md) | What is done, what is not |
| [docs/security.md](docs/security.md) | Threat model and current limitations |
| [docs/website.md](docs/website.md) | Hosting `marclevin.me/gonk` |
| [docs/plugins.md](docs/plugins.md) | Writing capabilities |
| [config.example.yaml](src/gonk/data/config.example.yaml) | Every setting, annotated |

## Everyday extras

```bash
gonk                     # short status
gonk profiles            # what `gonk land` can install
gonk config show         # settings in effect
gonk config set home.host 100.64.0.7
gonk log                 # audit log: who called what, and what was refused
gonk update              # upgrade gonk itself
```

Every setting can also be an environment variable, which is handy on a
machine you do not want to leave a config file on:

```bash
GONK_HOME_HOST=gonksystem GONK_HOME_TOKEN=gonk_... gonk home status
```

## Development

```bash
uv sync
./scripts/check.sh       # format, lint, types, tests, shellcheck
uv run gonk doctor
```

Tests never touch your real machine: every system call goes through one
object that the tests replace.
