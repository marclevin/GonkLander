"""Read-only facts about the machine."""

from __future__ import annotations

import os
from typing import Any

from gonk.capabilities.registry import CapabilityError, Context, capability

GIB = 1024**3


def _uptime_seconds(ctx: Context) -> float | None:
    text = ctx.system.read_text("/proc/uptime")
    try:
        return float(text.split()[0]) if text else None
    except (ValueError, IndexError):
        return None


def _memory(ctx: Context) -> dict[str, float] | None:
    text = ctx.system.read_text("/proc/meminfo")
    if not text:
        return None
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024
    if "MemTotal" not in values:
        return None
    total, available = values["MemTotal"], values.get("MemAvailable", 0)
    return {
        "total_gib": round(total / GIB, 1),
        "available_gib": round(available / GIB, 1),
        "used_percent": round(100 * (total - available) / total, 1),
    }


def _human_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


@capability("machine.status", risk="safe", description="Uptime, load, memory and disk usage.")
def status(ctx: Context) -> dict[str, Any]:
    report: dict[str, Any] = {"hostname": ctx.system.hostname()}
    if (uptime := _uptime_seconds(ctx)) is not None:
        report["uptime"] = _human_duration(uptime)
    if hasattr(os, "getloadavg"):
        report["load_average"] = [round(value, 2) for value in os.getloadavg()]
        report["cpu_count"] = os.cpu_count()
    if memory := _memory(ctx):
        report["memory"] = memory
    disks = {}
    for label, path in (("root", "/"), ("home", str(ctx.system.home()))):
        if usage := ctx.system.disk_usage(path):
            total, free = usage
            disks[label] = {
                "path": path,
                "total_gib": round(total / GIB, 1),
                "free_gib": round(free / GIB, 1),
                "used_percent": round(100 * (total - free) / total, 1) if total else 0,
            }
    report["disk"] = disks
    return report


@capability(
    "machine.processes",
    risk="safe",
    description=(
        "The busiest processes, by memory or cpu. Reports process names only, "
        "never command-line arguments."
    ),
)
def processes(ctx: Context, limit: int = 15, sort: str = "memory") -> list[dict[str, Any]]:
    if sort not in ("memory", "cpu"):
        raise CapabilityError("sort should be 'memory' or 'cpu'.")
    limit = max(1, min(limit, 100))
    # `comm` is the executable name. `args` would include secrets passed on
    # command lines, so it is deliberately not requested.
    result = ctx.system.run(["ps", "-eo", "pid=,user=,rss=,pcpu=,comm="])
    if not result.ok:
        raise CapabilityError("Could not list processes: ps is not available here.")
    rows: list[tuple[float, dict[str, Any]]] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            memory, cpu = round(int(parts[2]) / 1024, 1), float(parts[3])
            row = {
                "pid": int(parts[0]),
                "user": parts[1],
                "memory_mib": memory,
                "cpu_percent": cpu,
                "name": os.path.basename(parts[4].strip()),
            }
        except ValueError:
            continue
        rows.append((memory if sort == "memory" else cpu, row))
    rows.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _rank, row in rows[:limit]]


@capability(
    "machine.services",
    risk="safe",
    description="Running systemd services. scope is 'system' or 'user'.",
)
def services(ctx: Context, scope: str = "system") -> list[dict[str, str]]:
    if scope not in ("system", "user"):
        raise CapabilityError("scope should be 'system' or 'user'.")
    argv = ["systemctl", *(["--user"] if scope == "user" else [])]
    argv += ["list-units", "--type=service", "--state=running"]
    argv += ["--no-legend", "--plain", "--no-pager"]
    result = ctx.system.run(argv)
    if not result.ok:
        raise CapabilityError("Could not list services: systemd is not available here.")
    units = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4:
            units.append(
                {
                    "name": parts[0],
                    "state": parts[3],
                    "description": parts[4] if len(parts) > 4 else "",
                }
            )
    return units
