"""Read a text file from a directory the owner has opened up."""

from __future__ import annotations

import os
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from gonk.capabilities.registry import CapabilityError, Context, capability

# Refused even inside an allowed root. A directory full of source code is
# still likely to contain a few of these.
SECRET_NAMES = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx", "*.keystore",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    ".netrc", ".pgpass", ".npmrc", ".pypirc", ".git-credentials", ".htpasswd",
    "credentials", "credentials.*", "secrets", "secrets.*", "*.token",
)  # fmt: skip
# .git is here because remote URLs in .git/config can embed access tokens.
SECRET_DIRECTORIES = {
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store", ".git",
}  # fmt: skip


def looks_secret(path: Path) -> bool:
    name = path.name.lower()
    if any(fnmatchcase(name, pattern) for pattern in SECRET_NAMES):
        return True
    return any(part in SECRET_DIRECTORIES for part in path.parts)


def is_gonks_own(path: Path, ctx: Context) -> bool:
    """Gonk's configuration, tokens and audit log are never readable through Gonk,
    even if the owner opens up a directory that contains them."""
    for directory in (ctx.config.directory, ctx.config.state_directory):
        if directory == Path():
            continue
        try:
            real = directory.resolve()
        except (OSError, RuntimeError):
            continue
        if path == real or real in path.parents:
            return True
    return False


def resolve_allowed(requested: str, roots: list[str], home: Path) -> Path:
    """The real path of `requested`, if it lies inside one of `roots`.

    Both sides are resolved first, so a symlink inside a root that points
    outside it does not get through.
    """
    if not roots:
        raise CapabilityError(
            "No directories are open for reading.",
            hints=["Add directories to files.roots in config.yaml."],
        )
    candidate = Path(requested).expanduser()
    if not candidate.is_absolute():
        candidate = home / candidate
    try:
        real = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise CapabilityError(f"{requested} does not exist.") from None

    for root in roots:
        try:
            real_root = Path(root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if real == real_root or real_root in real.parents:
            return real
    raise CapabilityError(
        f"{requested} is outside the directories open for reading.",
        hints=[f"files.roots is: {', '.join(roots)}"],
    )


@capability(
    "files.read",
    risk="sensitive",
    description=(
        "Read a text file under files.roots. Files that look like credentials are refused."
    ),
)
def read(ctx: Context, path: str) -> dict[str, Any]:
    real = resolve_allowed(path, ctx.config.files.roots, ctx.system.home())
    if looks_secret(real) or is_gonks_own(real, ctx):
        raise CapabilityError(f"{path} looks like it holds credentials, so it is not readable.")
    if not real.is_file():
        raise CapabilityError(f"{path} is not a file.")

    limit = ctx.config.files.max_bytes
    try:
        # `real` has no symlinks left in it. O_NOFOLLOW keeps it that way if
        # the file is swapped for a link between the check above and this open.
        descriptor = os.open(real, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError as error:
        raise CapabilityError(f"Could not read {path}: {error.strerror}.") from error
    truncated = len(data) > limit
    data = data[:limit]
    if b"\x00" in data:
        raise CapabilityError(f"{path} is a binary file.")
    return {
        "path": str(real),
        "size_bytes": real.stat().st_size,
        "truncated": truncated,
        "content": data.decode("utf-8", errors="replace"),
    }
