#!/usr/bin/env python3
"""Install a checksum- and publisher-verified runtime without global changes."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import urllib.request

VERSION = "2.1.318"
BASE = "https://downloads.claude.ai/claude-code-releases"
REQUIREMENT = 'anchor apple generic and identifier "com.anthropic.claude-code" and certificate leaf[subject.OU] = "Q6L2SF6YDW"'


def verify_runtime(path: Path):
    if platform.system() != "Darwin":
        raise OSError("The current native adapter supports macOS only.")
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "-R=" + REQUIREMENT, str(path)],
                   check=True, capture_output=True, timeout=30)


def install(root: Path):
    if platform.system() != "Darwin":
        raise OSError("The current native adapter supports macOS only.")
    arch = {"arm64": "arm64", "x86_64": "x64"}.get(platform.machine())
    if not arch:
        raise OSError("Unsupported native architecture.")
    directory = root / "runtime"
    metadata = directory / "provenance.json"
    if metadata.is_symlink():
        raise ValueError("Runtime metadata is a symlink; existing files preserved.")
    url = f"{BASE}/{VERSION}"
    with urllib.request.urlopen(url + "/manifest.json", timeout=30) as response:
        manifest = json.load(response)
    expected = manifest["platforms"]["darwin-" + arch]["checksum"]
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".runtime-", dir=directory)
    stage = Path(name)
    try:
        with os.fdopen(fd, "wb") as file, urllib.request.urlopen(url + f"/darwin-{arch}/claude", timeout=120) as response:
            shutil.copyfileobj(response, file)
            file.flush()
            os.fsync(file.fileno())
        digest = hashlib.sha256(stage.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError("Runtime checksum mismatch; existing executable preserved.")
        stage.chmod(0o555)
        verify_runtime(stage)
        os.replace(stage, directory / "claude")
        data = json.dumps({
            "version": VERSION, "source": url, "sha256": digest,
            "publisher_requirement": REQUIREMENT}, indent=2) + "\n"
        meta_fd, meta_name = tempfile.mkstemp(prefix=".provenance-", dir=directory)
        meta_stage = Path(meta_name)
        try:
            with os.fdopen(meta_fd, "w") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(meta_stage, metadata)
        finally:
            meta_stage.unlink(missing_ok=True)
        print("Installed independently verified runtime", VERSION)
    finally:
        stage.unlink(missing_ok=True)


if __name__ == "__main__":
    install(Path(__file__).resolve().parent)
