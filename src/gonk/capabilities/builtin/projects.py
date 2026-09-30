"""Git repositories under the configured project roots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gonk.capabilities.registry import CapabilityError, Context, capability

# Repository config can name programs for git to run. These switch that off
# for the read-only commands used here.
GIT = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]


def _projects(ctx: Context) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root in ctx.config.projects.roots:
        directory = Path(root).expanduser()
        if not directory.is_dir():
            continue
        for child in sorted(directory.iterdir()):
            if child.is_dir() and (child / ".git").exists() and child.name not in found:
                found[child.name] = child
    return found


@capability("projects.list", risk="safe", description="Git repositories under projects.roots.")
def list_projects(ctx: Context) -> list[dict[str, str]]:
    return [{"name": name, "path": str(path)} for name, path in _projects(ctx).items()]


@capability(
    "projects.status",
    risk="safe",
    description="Branch, uncommitted changes and last commit of one project, by name.",
)
def status(ctx: Context, name: str) -> dict[str, Any]:
    # The caller gives a name, never a path; the path comes from our own listing.
    path = _projects(ctx).get(name)
    if path is None:
        raise CapabilityError(
            f"There is no project called '{name}'.",
            hints=["projects.list shows the names."],
        )
    state = ctx.system.run([*GIT, "-C", str(path), "status", "--porcelain=v1", "--branch"])
    if not state.ok:
        raise CapabilityError(f"git could not read {name}: {state.text}")
    lines = state.stdout.splitlines()
    branch = lines[0].removeprefix("## ") if lines else ""
    changes = lines[1:]

    report: dict[str, Any] = {
        "name": name,
        "path": str(path),
        "branch": branch,
        "clean": not changes,
        "changed_files": len(changes),
    }
    log = ctx.system.run([*GIT, "-C", str(path), "log", "-1", "--format=%h%x09%cr%x09%s"])
    if log.ok and log.stdout.strip():
        commit, when, subject = [*log.stdout.strip().split("\t", 2), "", ""][:3]
        report["last_commit"] = {"id": commit, "when": when, "subject": subject}
    return report
