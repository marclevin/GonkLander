"""What development tools are installed on this machine."""

from __future__ import annotations

from typing import Any

from gonk.capabilities.registry import Context, capability
from gonk.tools.catalog import load_catalog
from gonk.tools.detect import detect


@capability("tools.list", risk="safe", description="Known tools and whether each is installed.")
def list_tools(ctx: Context) -> list[dict[str, Any]]:
    report = []
    for spec in load_catalog(ctx.config.directory).values():
        found = detect(spec, ctx.system)
        report.append(
            {
                "name": spec.name,
                "description": spec.description,
                "installed": found.present,
                "version": found.version,
            }
        )
    return report
