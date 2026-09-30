"""Find capabilities: the built-in modules, then the user's plugins."""

from __future__ import annotations

import importlib
import importlib.util
import os
import pkgutil
import stat
import sys
from pathlib import Path

from gonk.capabilities import builtin
from gonk.capabilities.registry import Registry
from gonk.core.errors import GonkError

PLUGIN_DIRECTORY = "plugins"


def load_builtin(registry: Registry) -> None:
    for module_info in pkgutil.iter_modules(builtin.__path__):
        module = importlib.import_module(f"{builtin.__name__}.{module_info.name}")
        registry.add_from(vars(module), "built-in")


def unsafe_reason(path: Path) -> str:
    """Why a plugin path must not be loaded, or "" if it is fine.

    A plugin is code that runs as you. If anyone else could have written the
    file, loading it would hand them your account.
    """
    if not hasattr(os, "getuid"):
        return ""  # Windows: permissions work differently; rely on the profile directory
    try:
        info = path.stat()
    except OSError as error:
        return f"cannot be inspected ({error.strerror})"
    if info.st_uid != os.getuid():
        return "is owned by another user"
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return f"is writable by other users (fix: chmod go-w {path})"
    return ""


def load_plugins(registry: Registry, directory: Path) -> None:
    """Load every *.py in the plugin directory. A broken plugin is reported and
    skipped; it never takes the rest of Gonk down with it."""
    if not directory.is_dir():
        return
    if reason := unsafe_reason(directory):
        registry.problems.append(f"{directory} {reason}; no plugins were loaded")
        return

    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        if reason := unsafe_reason(path):
            registry.problems.append(f"{path.name} {reason}; skipped")
            continue
        module_name = f"gonk_plugin_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise GonkError("could not be read as a Python module")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            registry.add_from(vars(module), f"plugin {path.name}")
        except Exception as error:  # a plugin may fail in any way it likes
            sys.modules.pop(module_name, None)
            registry.problems.append(f"{path.name}: {error}")


def build_registry(config_directory: Path | None) -> Registry:
    registry = Registry()
    load_builtin(registry)
    if config_directory is not None:
        load_plugins(registry, config_directory / PLUGIN_DIRECTORY)
    return registry
