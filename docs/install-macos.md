# macOS installation

Supports Apple Silicon and Intel macOS, with Python 3.12+. The independently
pinned native CLI currently requires macOS 13 or later.

## 1. Install prerequisites

If you already have Python 3.12+ and Git, skip this step. With Homebrew installed:

```sh
brew install python git
```

Homebrew itself is available from [brew.sh](https://brew.sh/). No Python packages,
Node, npm, GnuPG or OpenRouter account are needed on macOS.

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
./install-macos.sh
```

One command downloads/verifies the independent native runtime and creates the
separate `promptbridge` dashboard and compatible `claude-openapi` command. New checkouts use `~/.local/bin`. An existing
matching project command in `/opt/homebrew/bin` is retained when rerunning setup
in its original folder. Unrelated commands are never replaced.

If prompted, add this to your shell profile (`~/.zshrc` on most Macs):

```sh
export PATH="$HOME/.local/bin:$PATH"
```

No shell profiles, existing Claude executable or original Claude settings are
edited. Do not use `sudo`. Keep the checkout in place; the command links to it.
To choose another command directory, use `./install-macos.sh --bin-dir /your/bin`.

## 4. Configure and run

```sh
promptbridge
```

Use the colored menu to save your API key (hidden input), choose a model,
edit persistent instructions and start/continue/resume a chat. Arrow keys,
Enter and number shortcuts work; there are no setup commands to copy. The
legacy command and all existing direct CLI options remain available.

Choose a model by its displayed number or ID; the choice is saved. Inference requires an
OpenCode key; installation/catalog listing does not. Free-tier client rules,
quota and model permissions still apply. No OpenRouter account is involved.

## Verification, updates and removal

The runtime is downloaded from the official vendor source with its published
SHA-256 checked, then Apple-trusted signing and the expected publisher/app
identity are required before activation. Each launch checks publisher signing
again. No ad-hoc signing or global vendor installer is used.

Open **Updates** in the dashboard to check, confirm an update and restart, or
roll back. Startup checks offer update/skip/skip-until-next-version choices.
Private GitHub access uses your Git credentials; `gh` browser sign-in is optional.
Manual setup remains available after a `git pull --ff-only`. The installer records the selected Python locally, avoiding
accidental use of an older system Python after PATH changes. If Python is moved
or removed, rerun setup or set `PROMPTBRIDGE_PYTHON=/path/to/python3`.

Remove only the two matching installed commands, keeping private state and runtime:

```sh
./rollback.sh --check
./rollback.sh
```

With a custom command directory, prefix removal with
`PROMPTBRIDGE_BIN_DIR=/your/bin`. Remove the checkout yourself only after saving
any `state/` you want to retain. Your original Claude installation is independent.
