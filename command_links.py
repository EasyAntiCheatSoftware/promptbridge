"""Name-only command lifecycle. Never restore historical vulnerable source."""
import os
from pathlib import Path
import tempfile


def matches(link: Path, target: Path) -> bool:
    return link.is_symlink() and os.readlink(link) == str(target)


def install(root: Path, bindir: Path, name: str = "claude-openapi"):
    target = root / "bin" / name
    destination = bindir / name
    if not target.is_file() or not os.access(target, os.X_OK):
        raise ValueError("Launcher missing or not executable; existing commands preserved.")
    if os.path.lexists(destination):
        if matches(destination, target):
            return
        raise ValueError("Destination is occupied; existing command preserved.")
    destination.symlink_to(target)


def remove(root: Path, bindir: Path, name: str = "claude-openapi"):
    link = bindir / name
    if matches(link, root / "bin" / name):
        link.unlink()


def undo_rename(root: Path, bindir: Path):
    """Restore only the old command name; retain the current hardened core."""
    old, new = bindir / "claude-crouton", bindir / "claude-openapi"
    old_target, new_target = root / "bin/claude-crouton", root / "bin/claude-openapi"
    for link, target in [(old, old_target), (new, new_target)]:
        if os.path.lexists(link) and not matches(link, target):
            raise ValueError("Unrelated installed command preserved.")
    if not new_target.is_file() or not os.access(new_target, os.X_OK):
        raise ValueError("New launcher is not executable; installed commands preserved.")
    source = new_target.read_bytes()
    if os.path.lexists(old_target) and (old_target.is_symlink() or old_target.read_bytes() != source):
        raise ValueError("Unrelated old launcher preserved.")
    if old_target.exists() and not os.access(old_target, os.X_OK):
        raise ValueError("Old launcher is not executable; current command preserved.")
    # Prepare a usable launcher before switching links. All checks are explicit
    # and remain active under PYTHONOPTIMIZE=1/2.
    if not old_target.exists():
        fd, name = tempfile.mkstemp(prefix=".launcher-", dir=old_target.parent)
        stage = Path(name)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(source)
                file.flush()
                os.fsync(file.fileno())
            stage.chmod(0o755)
            os.replace(stage, old_target)
        finally:
            stage.unlink(missing_ok=True)
    if not os.path.lexists(old):
        old.symlink_to(old_target)
    # A failed stage/link creation leaves the working new command in place.
    if matches(new, new_target):
        new.unlink()


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parent
    bindir = Path(os.environ.get("PROMPTBRIDGE_BIN_DIR", "/opt/homebrew/bin"))
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    if action == "install":
        install(root, bindir)
    elif action == "remove":
        remove(root, bindir)
    elif action == "undo-rename":
        undo_rename(root, bindir)
    elif action == "check":
        print("Matching command installed:", matches(bindir / "claude-openapi", root / "bin/claude-openapi"))
    else:
        raise SystemExit("Choose install, remove, undo-rename or check.")
