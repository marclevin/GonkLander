# Writing a capability

A capability is one function. Adding one touches one file and nothing else.

## The shortest possible plugin

Create `~/.config/gonk/plugins/verascient.py`:

```python
from gonk.capabilities import Context, capability


@capability("verascient.status", risk="safe", description="Is Verascient up?")
def status(ctx: Context) -> dict:
    return {"up": True}
```

Then:

```bash
chmod 600 ~/.config/gonk/plugins/verascient.py
gonk mcp list        # verascient.status is there
```

It is now available through the agent as `verascient.status`, and over MCP as
the tool `gonk_verascient_status`. Restart the agent (`gonk agent start`) or
the MCP client to pick it up.

## The rules

**Name.** Lowercase words joined by dots, at least two: `mphil.sync_calendar`.
The first word is your namespace. Over MCP the dots become underscores and
`gonk_` goes in front.

**Risk.** Pick the one that is true:

| Risk | Means | Default |
| --- | --- | --- |
| `safe` | Only reads, and nothing it reads is private. | On |
| `sensitive` | Reads private things, or changes something harmlessly. | Off until `policy.allow` matches it |
| `dangerous` | Could break something or cost money. | Off until `policy.allow` names it exactly |

If you leave `risk` out you get `sensitive`. Forgetting is safe.

**Parameters.** The first parameter is always the `Context`. The rest must be
annotated as `str`, `int`, `float` or `bool`. A parameter with a default is
optional. Gonk builds the schema from the signature and checks every call
against it before your function runs; nothing is converted for you, so an
`int` parameter always receives an `int`.

**Return value.** Anything `json.dumps` accepts.

**Failing.** Raise `CapabilityError` with a message a person can act on:

```python
from gonk.capabilities import CapabilityError

raise CapabilityError(
    "Verascient did not answer.",
    hints=["systemctl --user status verascient"],
)
```

Any other exception is reported to the caller as "failed unexpectedly". The
details go to the audit log (`gonk log`) and are not sent to the caller.

## What the Context gives you

```python
ctx.config  # the loaded configuration
ctx.system  # run commands, find executables, probe ports
ctx.caller  # the device name, or "mcp"
```

Run commands through `ctx.system.run([...])`, with a list, not a string:

```python
result = ctx.system.run(["systemctl", "--user", "is-active", "verascient"])
if not result.ok:
    raise CapabilityError(f"verascient is {result.stdout.strip() or 'not running'}.")
```

Going through `ctx.system` is also what lets you test a capability without
running anything; see `tests/fakes.py`.

## A fuller example

```python
from gonk.capabilities import CapabilityError, Context, capability

CALENDARS = {"mphil", "personal"}


@capability(
    "mphil.sync_calendar",
    risk="sensitive",
    description="Pull the next few days of a calendar into the planner.",
)
def sync_calendar(ctx: Context, calendar: str = "mphil", days: int = 7) -> dict:
    # Never pass what the caller sent straight into a command. Check it
    # against what you expect first.
    if calendar not in CALENDARS:
        raise CapabilityError(f"Unknown calendar. Choose from: {', '.join(sorted(CALENDARS))}.")
    days = max(1, min(days, 31))

    result = ctx.system.run(["mphil-sync", "--calendar", calendar, "--days", str(days)], timeout=60)
    if not result.ok:
        raise CapabilityError(f"The sync failed: {result.text}")
    return {"calendar": calendar, "days": days, "output": result.stdout.strip()}
```

Switch it on:

```bash
gonk config set policy.allow "mphil.*"
```

## Writing one safely

The caller may be an AI model acting on text it read somewhere. Treat every
argument as if a stranger typed it.

- Take a **name**, and look up the real thing yourself. `projects.status`
  takes a project name and finds the path in its own listing; it never
  accepts a path.
- Check arguments against a **fixed set** where you can.
- **Never build a shell string.** Always `ctx.system.run([...])`.
- Return what is **needed**, not everything you have. `machine.processes`
  returns process names and leaves out command lines, because command lines
  contain passwords more often than anyone would like.

## Where plugins are loaded from

`~/.config/gonk/plugins/*.py`, in alphabetical order. Files starting with `_`
are skipped, so `_helpers.py` can hold shared code.

A plugin is code that runs as you. Gonk will not load a plugin file, or
anything from a plugin directory, that is owned by someone else or writable
by group or others. `gonk doctor` reports plugins that were skipped or that
failed to load; a broken plugin never stops the others.

A plugin cannot replace a built-in capability. Two capabilities with the same
name is an error, and the built-in one wins.
