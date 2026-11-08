# Arch Linux installation

Supports glibc Arch Linux on x86_64 and ARM64, with Python 3.12+. CI executes the
signed Linux x86_64 runtime on a current Arch container as an ordinary user.
ARM64 platform selection is unit-tested; it is not an ARM64 hardware CI run.
Alpine/musl, 32-bit machines and Windows are not supported by this installer.

## 1. Install prerequisites

```sh
sudo pacman -Syu --needed python git gnupg ca-certificates
```

Use the full system upgrade shown above; avoid Arch partial upgrades. This is
the only step that needs administrator access. Do not run the project installer
with `sudo`. Python packages, Node, npm and OpenRouter are not required.

## 2. Get the project

If you already have a checkout, open that folder and skip cloning. Otherwise:

```sh
git clone https://github.com/EasyAntiCheatSoftware/promptbridge.git
cd promptbridge
```

For a private repository, authenticate Git with an account that has access
before cloning (for example, `gh auth login` and `gh auth setup-git` if you use
GitHub CLI). Do not put tokens into clone URLs.

## 3. Install

```sh
./install-arch.sh
```

This single command downloads and verifies the independent native runtime and
installs `promptbridge` and `claude-openapi` into `~/.local/bin`. If that folder is not on your PATH,
the installer prints the exact line to add. The usual setting is:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Add that line to `~/.bashrc` or `~/.zshrc` for future terminals. The installer does
not edit shell profiles. Keep the checkout in place: the command links to it.
For another command directory, use `./install-arch.sh --bin-dir /your/bin`.

## 4. Configure and run

```sh
promptbridge
```

Use the colored menu to save your API key (hidden input), choose a model,
edit persistent instructions and start/continue/resume a chat. Arrow keys,
Enter and number shortcuts work; there are no setup commands to copy. The
legacy command and all existing direct CLI options remain available.

Choose a model by its displayed number or ID; the choice is saved. An OpenCode key is needed
for inference, not installation/catalog listing. OpenRouter is not involved.
Provider client restrictions, quota and model availability still apply; a free
catalog entry does not guarantee access from a third-party client.

## Verification, updates and removal

Linux setup verifies the vendor's detached GPG signature on the pinned release
manifest against the primary fingerprint
`31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE`, then verifies the binary's SHA-256.
GPG uses a temporary isolated keyring, not your own keys/settings. Each launch
rechecks the signed manifest and executable hash. A new release bundle is
activated only after verification succeeds; failures retain the previous runtime.

Open **Updates** in the dashboard to check, confirm an update and restart, or
roll back. Startup checks offer update/skip/skip-until-next-version choices.
Private GitHub access uses your Git credentials; `gh` browser sign-in is optional.
Manual setup remains available after a `git pull --ff-only`. If Python was moved/removed, rerun setup
with your current Python (or set `PROMPTBRIDGE_PYTHON=/path/to/python3`).

To remove only this project's two commands, keeping private settings and runtime:

```sh
./rollback.sh --check
./rollback.sh
```

For a custom command directory, prefix removal with
`PROMPTBRIDGE_BIN_DIR=/your/bin`. Unrelated commands and the original Claude
installation/configuration are preserved. To reclaim retained runtime disk
space after removal, delete this checkout only after backing up any `state/`
you want to keep; nothing is stored in your original Claude configuration.
