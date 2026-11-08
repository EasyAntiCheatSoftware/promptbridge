"""Credential-free installer contracts; real platform verification also runs in CI."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import managed_install
import command_links
import install as setup
import runtime_setup as runtime


class PlatformTests(unittest.TestCase):
    def test_platform_matrix(self):
        for system, arch, expected in [("Darwin", "arm64", "darwin-arm64"),
                                       ("Darwin", "x86_64", "darwin-x64"),
                                       ("Linux", "aarch64", "linux-arm64"),
                                       ("Linux", "x86_64", "linux-x64")]:
            with self.subTest(system=system, arch=arch), patch.object(runtime.platform, "system", return_value=system), patch.object(runtime.platform, "machine", return_value=arch), patch.object(runtime.platform, "libc_ver", return_value=("glibc", "2.43")):
                self.assertEqual(runtime.platform_target(), expected)

    def test_unsupported_and_musl(self):
        for system, arch, libc in [("Windows", "x86_64", ""), ("Linux", "riscv64", "glibc"), ("Linux", "aarch64", "musl")]:
            with self.subTest(system=system, arch=arch, libc=libc), patch.object(runtime.platform, "system", return_value=system), patch.object(runtime.platform, "machine", return_value=arch), patch.object(runtime.platform, "libc_ver", return_value=(libc, "")):
                with self.assertRaises(OSError):
                    runtime.platform_target()

    def test_manifest_checksums_fail_closed(self):
        for manifest in [{}, None, {"platforms": []}, {"platforms": {"linux-x64": {"checksum": "not-a-hash"}}}]:
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                runtime.expected_hash(manifest, "linux-x64")


class SignatureTests(unittest.TestCase):
    def fixture(self, root):
        manifest = {"version": runtime.VERSION, "platforms": {}}
        (root / "manifest.json").write_text(json.dumps(manifest))
        (root / "manifest.json.sig").write_text("fixture-signature")
        (root / "vendor-key.asc").write_text("fixture-key")
        return manifest

    def test_pinned_primary_or_signing_subkey(self):
        for signer, primary in [(runtime.SIGNING_FINGERPRINT, ""), ("A" * 40, runtime.SIGNING_FINGERPRINT)]:
            with self.subTest(signer=signer), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                manifest = self.fixture(root)
                status = f"[GNUPG:] VALIDSIG {signer} 2026-11-07 1 0 4 0 1 10 00 {primary}\n"
                with patch.object(runtime.shutil, "which", return_value="/fixture/gpg"), patch.object(runtime.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0, status)]) as run:
                    self.assertEqual(runtime.verify_manifest(root), manifest)
                    command = run.call_args.args[0]
                    self.assertIn("--no-options", command)
                    self.assertIn("--no-auto-key-retrieve", command)
                    self.assertNotEqual(command[command.index("--homedir") + 1], str(Path.home() / ".gnupg"))

    def test_missing_key_wrong_signer_bad_signature_and_version(self):
        cases = [(0, "", runtime.VERSION), (0, "[GNUPG:] VALIDSIG WRONG", runtime.VERSION),
                 (1, f"[GNUPG:] VALIDSIG {runtime.SIGNING_FINGERPRINT}", runtime.VERSION),
                 (0, f"[GNUPG:] VALIDSIG {runtime.SIGNING_FINGERPRINT}", "0.0.0")]
        for code, status, version in cases:
            with self.subTest(code=code, status=status, version=version), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.fixture(root)
                (root / "manifest.json").write_text(json.dumps({"version": version}))
                with patch.object(runtime.shutil, "which", return_value="gpg"), patch.object(runtime.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], code, status)]):
                    with self.assertRaises(ValueError):
                        runtime.verify_manifest(root)
        with tempfile.TemporaryDirectory() as tmp, patch.object(runtime.shutil, "which", return_value=None):
            with self.assertRaisesRegex(OSError, "GnuPG"):
                runtime.verify_manifest(Path(tmp))
        with tempfile.TemporaryDirectory() as tmp, patch.object(runtime.shutil, "which", return_value="gpg"):
            with self.assertRaisesRegex(ValueError, "metadata missing"):
                runtime.verify_manifest(Path(tmp))

    def test_mac_keeps_publisher_requirement(self):
        with patch.object(runtime, "platform_target", return_value="darwin-arm64"), patch.object(runtime.subprocess, "run") as run:
            runtime.verify_runtime(Path("/fixture/claude"))
            self.assertIn("-R=" + runtime.REQUIREMENT, run.call_args.args[0])
            self.assertTrue(run.call_args.kwargs["check"])

    def test_linux_authenticates_bundle_and_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "claude"
            binary.write_bytes(b"fixture")
            manifest = {"platforms": {"linux-x64": {"checksum": runtime.file_hash(binary)}}}
            link = root / "active"
            link.symlink_to(binary)
            with patch.object(runtime, "platform_target", return_value="linux-x64"), patch.object(runtime, "verify_manifest", return_value=manifest) as verify:
                runtime.verify_runtime(link)
                verify.assert_called_with(root.resolve())
                binary.write_bytes(b"tampered")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    runtime.verify_runtime(link)


class RuntimeInstallTests(unittest.TestCase):
    def exercise(self, fail=None):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            (root / "runtime").mkdir()
            active = root / "runtime/claude"
            active.write_bytes(b"old-runtime")
            data = b"fixture-new-runtime"
            manifest = {"version": runtime.VERSION, "platforms": {"linux-x64": {"checksum": hashlib.sha256(data).hexdigest()}}}
            urls = []
            def download(url, destination):
                urls.append(url)
                if destination.name == "manifest.json":
                    destination.write_text(json.dumps(manifest))
                elif destination.name == "claude":
                    destination.write_bytes(b"bad-download" if fail == "checksum" else data)
                else:
                    destination.write_text("fixture-authentication")
            with patch.object(runtime, "platform_target", return_value="linux-x64"), patch.object(runtime.shutil, "which", return_value="gpg"), patch.object(runtime, "download", side_effect=download), patch.object(runtime, "verify_manifest", side_effect=ValueError("signature") if fail == "signature" else lambda _: manifest), patch.object(runtime, "verify_runtime") as verify, patch.object(runtime.os, "replace", side_effect=OSError("activation") if fail == "activate" else os.replace):
                if fail:
                    with self.assertRaises((ValueError, OSError)):
                        runtime.install(root)
                    self.assertEqual(active.read_bytes(), b"old-runtime")
                    self.assertFalse(active.is_symlink())
                    if fail == "signature":
                        self.assertFalse(any(url.endswith("/linux-x64/claude") for url in urls))
                else:
                    runtime.install(root)
                    verify.assert_called_once()
                    self.assertTrue(active.is_symlink())
                    self.assertEqual(active.read_bytes(), data)
                    self.assertEqual(active.stat().st_mode & 0o777, 0o555)
                    self.assertTrue((active.resolve().parent / "manifest.json.sig").is_file())
                    self.assertEqual(json.loads((active.resolve().parent / "provenance.json").read_text())["platform"], "linux-x64")
                    self.assertIn(runtime.KEY_URL, urls)
            self.assertEqual(list((root / "runtime").glob(".download-*")), [])
            self.assertEqual(list((root / "runtime").glob(".activate-*")), [])

    def test_signed_bundle_atomic_activation(self):
        self.exercise()

    def test_failures_preserve_old_runtime(self):
        for failure in ["signature", "checksum", "activate"]:
            with self.subTest(failure=failure):
                self.exercise(failure)


class SetupTests(unittest.TestCase):
    def fixture(self, root):
        (root / "bin").mkdir()
        launcher = root / "bin/claude-openapi"
        launcher.write_text("#!/bin/sh\nexit 0\n")
        launcher.chmod(0o755)
        (root / "runtime").mkdir()
        (root / "bin/promptbridge").write_bytes(launcher.read_bytes())
        (root / "bin/promptbridge").chmod(0o755)
        (root / "promptbridge.json").write_bytes((ROOT / "promptbridge.json").read_bytes())
        (root / "bootstrap.py").write_bytes((ROOT / "bootstrap.py").read_bytes())
        native = root / 'runtime/claude'
        native.write_text('#!/bin/sh\necho "' + runtime.VERSION + ' (Claude Code)"\n')
        native.chmod(0o755)

    def test_idempotent_setup_and_interpreter_receipt(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp) / "project"; root.mkdir()
            self.fixture(root)
            bindir = Path(tmp) / "new-command-dir"
            with patch.object(managed_install, "prepare_source", return_value=root), patch.object(managed_install, "runtime_module", return_value=runtime), patch.object(runtime, "verify_runtime"), patch.object(runtime, "install") as download:
                setup.install(root, bindir)
                setup.install(root, bindir)
                download.assert_not_called()
            self.assertEqual((root / "runtime/python-path").read_text(), sys.executable + "\n")
            self.assertTrue(command_links.matches(bindir / "claude-openapi", root.resolve() / "bin/claude-openapi"))
            self.assertFalse((bindir / "claude").exists())
            command_links.remove(root.resolve(), bindir)
            self.assertFalse((bindir / "claude-openapi").exists())

    def test_collision_and_missing_launcher_fail_before_runtime(self):
        for failure in ["collision", "canonical-collision", "launcher", "runtime-symlink"]:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "project"; root.mkdir()
                self.fixture(root)
                bindir = Path(tmp) / "commands"; bindir.mkdir()
                unrelated = bindir / "claude-openapi"
                if failure == "collision": unrelated.write_text("preserve")
                if failure == "canonical-collision": (bindir / "promptbridge").write_text("preserve")
                if failure == "launcher": (root / "bin/claude-openapi").chmod(0o644)
                if failure == "runtime-symlink":
                    (root / "runtime/claude").unlink()
                    (root / "runtime").rmdir()
                    (root / "runtime").symlink_to(bindir)
                with patch.object(runtime, "verify_runtime") as verify, patch.object(runtime, "install") as download:
                    with self.assertRaises(ValueError): setup.install(root, bindir)
                    verify.assert_not_called(); download.assert_not_called()
                if failure == "collision": self.assertEqual(unrelated.read_text(), "preserve")

    def test_default_directory_and_legacy_preservation(self):
        root = Path("/fixture/project")
        with patch.dict(os.environ, {"PROMPTBRIDGE_BIN_DIR": ""}), patch.object(command_links, "matches", return_value=False):
            self.assertEqual(command_links.default_bin_dir(root), Path.home() / ".local/bin")
        with patch.dict(os.environ, {"PROMPTBRIDGE_BIN_DIR": ""}), patch.object(command_links, "matches", return_value=True):
            self.assertEqual(command_links.default_bin_dir(root), Path("/opt/homebrew/bin"))
        with patch.dict(os.environ, {"PROMPTBRIDGE_BIN_DIR": "~/custom/bin"}):
            self.assertEqual(command_links.default_bin_dir(root), (Path.home() / "custom/bin").resolve())

    def test_launch_and_removal_use_saved_python_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="bridge space ") as tmp:
            root = Path(tmp).resolve()
            self.fixture(root)
            (root / "bin/claude-openapi").write_bytes((ROOT / "bin/claude-openapi").read_bytes())
            (root / "bridge.py").write_text('print("INTERPRETER_OK")\n')
            (root / "command_links.py").write_bytes((ROOT / "command_links.py").read_bytes())
            (root / "rollback.sh").write_bytes((ROOT / "rollback.sh").read_bytes())
            python = root / "python with spaces"; python.symlink_to(sys.executable)
            (root / "runtime/python-path").write_text(str(python) + "\n")
            bindir = root / "command dir"
            command_links.install(root, bindir)
            environment = dict(os.environ, PROMPTBRIDGE_BIN_DIR=str(bindir)); environment.pop("PROMPTBRIDGE_PYTHON", None)
            result = subprocess.run([str(bindir / "claude-openapi")], env=environment, capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout.strip()), (0, "INTERPRETER_OK"), result.stderr)
            result = subprocess.run(["sh", str(root / "rollback.sh")], env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((bindir / "claude-openapi").exists())

    def test_platform_entrypoints_reject_wrong_os(self):
        script = "install-arch.sh" if sys.platform == "darwin" else "install-macos.sh"
        result = subprocess.run(["sh", str(ROOT / script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("installer on", result.stderr)


if __name__ == "__main__":
    unittest.main()
