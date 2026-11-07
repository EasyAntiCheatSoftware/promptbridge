#!/usr/bin/env python3
"""Independent, authenticated native runtimes for macOS and glibc Linux."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.request
import uuid

VERSION = "2.1.318"
BASE = "https://downloads.claude.ai/claude-code-releases"
KEY_URL = "https://downloads.claude.ai/keys/claude-code.asc"
SIGNING_FINGERPRINT = "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"
REQUIREMENT = 'anchor apple generic and identifier "com.anthropic.claude-code" and certificate leaf[subject.OU] = "Q6L2SF6YDW"'


def platform_target():
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(platform.machine().lower())
    if not system or not arch:
        raise OSError("Supported platforms: macOS and glibc Linux, x86_64 or ARM64.")
    if system == "linux" and platform.libc_ver()[0] == "musl":
        raise OSError("This Linux adapter requires glibc; Arch Linux is supported.")
    return system + "-" + arch


def file_hash(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def verify_manifest(directory):
    """Authenticate a signed manifest with a pinned primary key, in a fresh keyring."""
    executable = shutil.which("gpg")
    if not executable:
        raise OSError("GnuPG is required on Linux. Arch: sudo pacman -Syu --needed gnupg")
    for name in ("manifest.json", "manifest.json.sig", "vendor-key.asc"):
        if not (directory / name).is_file():
            raise ValueError("Runtime authentication metadata missing; rerun the platform installer.")
    with tempfile.TemporaryDirectory(prefix="promptbridge-gpg-") as home:
        command = [executable, "--no-options", "--batch", "--homedir", home,
                   "--no-auto-key-retrieve", "--no-auto-key-import"]
        imported = subprocess.run([*command, "--import", str(directory / "vendor-key.asc")],
                                  capture_output=True, text=True, timeout=30)
        if imported.returncode:
            raise ValueError("Vendor release key import failed.")
        checked = subprocess.run([*command, "--status-fd", "1", "--verify",
                                  str(directory / "manifest.json.sig"), str(directory / "manifest.json")],
                                 capture_output=True, text=True, timeout=30)
        signatures = [line.split() for line in checked.stdout.splitlines()
                      if line.startswith("[GNUPG:] VALIDSIG ")]
        if checked.returncode or not any(len(fields) >= 3 and (fields[2] == SIGNING_FINGERPRINT or fields[-1] == SIGNING_FINGERPRINT) for fields in signatures):
            raise ValueError("Vendor manifest signature/fingerprint verification failed.")
    manifest = json.loads((directory / "manifest.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("version") != VERSION:
        raise ValueError("Signed runtime manifest does not match the pinned version.")
    return manifest


def expected_hash(manifest, target):
    try:
        checksum = manifest["platforms"][target]["checksum"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Runtime platform is missing from the vendor manifest.") from exc
    if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise ValueError("Invalid runtime checksum in vendor manifest.")
    return checksum


def verify_runtime(path: Path):
    target = platform_target()
    if target.startswith("darwin-"):
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "-R=" + REQUIREMENT, str(path)],
                       check=True, capture_output=True, timeout=30)
    else:
        # Resolve the activated release link to the matching authentication bundle.
        executable = path.resolve(strict=True)
        manifest = verify_manifest(executable.parent)
        if file_hash(executable) != expected_hash(manifest, target):
            raise ValueError("Linux runtime checksum does not match the signed manifest.")


def download(url, destination):
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as file:
        shutil.copyfileobj(response, file)
        file.flush()
        os.fsync(file.fileno())


def install(root: Path):
    target = platform_target()
    directory = root / "runtime"
    if directory.is_symlink() or (directory / "provenance.json").is_symlink():
        raise ValueError("Runtime metadata/directory is a symlink; existing files preserved.")
    releases = directory / "releases"
    if releases.is_symlink():
        raise ValueError("Runtime release directory is a symlink; existing files preserved.")
    if target.startswith("linux-") and not shutil.which("gpg"):
        raise OSError("GnuPG is required on Linux. Arch: sudo pacman -Syu --needed gnupg")
    releases.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/{VERSION}"
    print("Downloading and authenticating native runtime", VERSION, "for", target, flush=True)
    pointer = directory / (".activate-" + uuid.uuid4().hex)
    # Activate a whole generation only after binary and authentication writes pass.
    with tempfile.TemporaryDirectory(prefix=".download-", dir=directory) as temporary:
        stage = Path(temporary)
        download(url + "/manifest.json", stage / "manifest.json")
        if target.startswith("linux-"):
            download(url + "/manifest.json.sig", stage / "manifest.json.sig")
            download(KEY_URL, stage / "vendor-key.asc")
            manifest = verify_manifest(stage)
        else:
            manifest = json.loads((stage / "manifest.json").read_text())
        checksum = expected_hash(manifest, target)
        download(url + f"/{target}/claude", stage / "claude")
        if file_hash(stage / "claude") != checksum:
            raise ValueError("Runtime checksum mismatch; existing executable preserved.")
        (stage / "claude").chmod(0o555)
        verify_runtime(stage / "claude")
        (stage / "provenance.json").write_text(json.dumps({
            "version": VERSION, "platform": target, "source": url, "sha256": checksum,
            "publisher_requirement": REQUIREMENT if target.startswith("darwin-") else SIGNING_FINGERPRINT,
        }, indent=2) + "\n")
        release = releases / (VERSION + "-" + target + "-" + uuid.uuid4().hex[:12])
        os.rename(stage, release)
        try:
            pointer.symlink_to(release.relative_to(directory) / "claude")
            os.replace(pointer, directory / "claude")
        finally:
            pointer.unlink(missing_ok=True)
    print("Installed independently verified runtime", VERSION, "for", target)


if __name__ == "__main__":
    install(Path(__file__).resolve().parent)
