"""Find a referenced asset when its package was copied under another folder.

Assets copied into a project by hand keep the package names they had where they came
from (``/AthenaSeason5T/EvePort/...``) while living under another folder here
(``Content/EvePort_RudeLemon/EvePort/...``). What they reference sits at the same place
relative to them, so a reference is looked up from the referencing file as well.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

Resolver = Callable[[str], Optional[Path]]


def relative_to_package(file: Path, package_name: str, object_path: str) -> Optional[Path]:
    """``object_path`` placed next to ``file`` as it is next to ``package_name``; None if not there."""
    own = package_name.strip("/").split("/")[:-1]  # the referencing package's folder
    target = object_path.split(".", 1)[0].strip("/").split("/")
    shared = 0
    while shared < min(len(own), len(target) - 1) and own[shared] == target[shared]:
        shared += 1
    if shared < 2:  # same mount and at least one folder in common, or it is a guess
        return None
    folder = file.parent
    for _ in range(len(own) - shared):
        folder = folder.parent
    candidate = folder.joinpath(*target[shared:]).with_suffix(".uasset")
    return candidate if candidate.is_file() else None


def with_relative(resolve: Resolver, file: Path, package_name: str) -> Resolver:
    """``resolve``, then the lookup from ``file`` for references it cannot place."""

    def lookup(object_path: str) -> Optional[Path]:
        return resolve(object_path) or relative_to_package(file, package_name, object_path)

    return lookup
