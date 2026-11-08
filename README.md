# Promptbridge

A small, local coding-agent gateway with private configuration, per-session
authentication, bounded connections and editable system prompts.

The project name is provider-neutral for a future OpenAI API port. **That port
is not implemented yet:** the current adapter runs a separate Claude Code CLI
against OpenCode's Anthropic-compatible gateway, with selectable OpenCode
text/chat models. OpenCode translates other native text-model APIs at its gateway.
The existing `claude-openapi` command is retained for compatibility. No existing
Claude installation or configuration is overwritten.

## Easy installation

Python 3.12+, Git, and macOS (Apple Silicon/Intel) or glibc Linux
(x86_64/ARM64) are supported. No pip packages, Node or npm are needed.
The native executable is downloaded independently, never committed to Git.

From a checkout of this project, choose your platform:

### Arch Linux

```sh
sudo pacman -Syu --needed python git gnupg ca-certificates
./install-arch.sh
```

[Arch: complete setup, updates and removal](docs/install-arch.md).

### macOS

```sh
brew install python git  # skip if Python 3.12+ and Git are already installed
./install-macos.sh
```

[macOS: complete setup, updates and removal](docs/install-macos.md).

Both installers verify the native runtime, then install the separate
`promptbridge` dashboard and compatible `claude-openapi` command in `~/.local/bin` for new checkouts. Add that folder to
PATH if prompted. Keep the checkout in place. For a custom directory use
`--bin-dir /your/bin` (or `PROMPTBRIDGE_BIN_DIR`). A matching existing project
command in `/opt/homebrew/bin` is retained; unrelated commands, original Claude
settings and shell profiles are preserved. Run project setup without `sudo`.
`./install.sh` is the shared entry point if dependencies are already present.

After installing, type **`promptbridge`**. Use arrows/Enter or number shortcuts:
set a hidden API key, choose a real model name, edit saved instructions, start a
chat, or continue/resume saved work. The default dashboard is free-only; enabling
paid/unknown models requires explicit confirmation. `claude-openapi` with no
arguments opens the same dashboard. Existing command-line options still work.

OpenCode credentials and provider-granted access are required for inference,
not installation. **No OpenRouter account is needed.** A proxy does not remove
provider client restrictions, expired-key errors or quota limits.

macOS uses the expected Apple-trusted publisher signature and app identity.
Linux uses a signed vendor manifest with a pinned GPG primary fingerprint and
SHA-256. Authentication is checked at activation and each native launch.
Failed runtime updates preserve the previous executable. No global vendor
installer or ad-hoc signing is used. A confirmed Promptbridge update refreshes
the native runtime only when its signed, pinned version changes.

## Dashboard and updates

Launch `promptbridge` from your project folder. The home screen includes new,
continue and resume chat actions, a searchable/paged model list, saved system
instructions (append/replace/off), hidden key entry, updates and settings.
Choices persist across restarts; chats stay under the same isolated private
state. No background service needs to stay open overnight. The original Claude
setup remains separate. `NO_COLOR=1` disables colors; `TERM=dumb` uses numbered
input instead of cursor navigation. Enter selects the default in either mode;
application-keypad/CSI-u Enter is also recognized. Unrelated terminal reports
are ignored rather than cancelling a menu. Invalid numbered input is retried instead of closing the menu. Chat launch
progress is shown immediately, and a failed native exit stays visible until
you acknowledge it. Use the keyboard rather than clicking terminal text. `$VISUAL` / `$EDITOR` edits staged private
instructions, with nano/vi or multiline entry as fallbacks.

On interactive startup, check GitHub at most once every six hours. An available
update offers **Update and restart**, **Skip for now**, or **Skip until the next
version**. The last option suppresses all builds with that semantic version;
manual **Updates → Check now** can still offer it. Automatic checks can be
switched off. Noninteractive / print / help / version commands never prompt or
fetch updates. An offline check leaves chat startup available (model inference
still needs its provider connection).

Updates use only this repository's original GitHub origin and fast-forward
`main`. Repository visibility is unchanged. If GitHub access requires credentials,
**Updates → Sign in** can open GitHub CLI browser sign-in when `gh` is installed. Updates never request
an API key or administrator password. Source is trusted code from the repository,
not a separate independently signed release channel: keep GitHub access secure.

Local source edits/untracked files, development branches, divergent history or
another active chat block installation. Source is statically checked before
checkout; native publisher/version checks run before restart. Failed checks
restore the prior source/native pointer. **Updates → Roll back** restores the
last matching installation; it also preserves local edits. Git/source updates
never overwrite tracked private-state/runtime paths. Keys, saved chats, prompts
and settings remain in the same ignored private state directory. Restart retains
the working folder, CLI arguments and environment; an interrupted in-flight
model request is not replayed. An active chat must finish before updating.

Legacy/ZIP packages get a managed Git checkout under `runtime/source-checkout`;
their keys/chats stay at the old package's `state/` path. Normal cloned installs
update their own checkout. Keep both in place. `promptbridge --update` checks
manually in an interactive terminal; `promptbridge --version` shows the app
version, while `claude-openapi --version` retains the native CLI version.

## Choose any OpenCode text/chat model

```sh
# Easiest: choose a named model from a numbered menu and save it.
claude-openapi --opencode-select-model --free
claude-openapi

# Fetch the current public catalog; no API key or inference call is needed.
claude-openapi --opencode-models
claude-openapi --opencode-models --free
claude-openapi --opencode-models --free --json

# One invocation; use an ID from the catalog, not a native family alias.
claude-openapi --model big-pickle
claude-openapi --model iceman-free --system-prompt "Your instructions"
claude-openapi --model opencode/glm-5.3-flash-free

# Persist the default privately, without touching the existing Claude setup.
claude-openapi --opencode-set-model big-pickle
claude-openapi

# Require current catalog pricing to be zero; reject paid/unknown switches.
claude-openapi --free-only --model big-pickle
```

Model precedence is per-run `--model` (or `--opencode-model`), then
`PROMPTBRIDGE_MODEL`, then `OPENCODE_MODEL`, then the private saved default.
Without any selection, the former `crouton-alpha-free` default is preserved for
backward compatibility. If that ID is absent from the catalog, an interactive
first launch asks you to choose a free-listed model rather than silently routing
to something else. Literal example placeholders such as `MODEL_ID` are rejected
with a link to the selection command. New IDs work without a code release; they need not be in
the cached catalog. An `opencode/` prefix is accepted and removed before routing.
Explicit request IDs are preserved, including native `/model` changes; native
family/background aliases use the configured model rather than silently picking
a different paid model. No automatic fallback is added.

The public catalog lists supported text/chat models and prices, **not your
account's access**. `--free-only` refreshes that catalog and permits only models
whose input/output and any published cache prices are zero; unknown prices and
paid models are rejected before sending the prompt. This is a catalog-based
guard, not a provider billing guarantee. Decimal catalog numbers are retained
as exact text in discovery JSON/cache so tiny nonzero prices never round to free. To check actual access, use
`--opencode-check --model MODEL` (optionally `--free-only`).

OpenCode currently restricts free-tier chat models to its own client, even when
they appear in the public catalog. Provider key, region, quota and client rules
remain authoritative; a free listing or local fixture success does not remove
those restrictions. There is no client impersonation or access-rule bypass.
Paid models are supported when explicitly selected and granted by your account.
System One decision, embedding, audio-only and image-generation endpoints are
not chat/coding-agent models and are excluded from this adapter's model list.

Interactive startup loads the public catalog automatically when missing or more
than 24 hours old. A refresh failure retains the cached list; `--free-only`
always requires a fresh successful catalog fetch. Run `--opencode-models` to
refresh manually. The native `/model` picker uses a private `modelPicker` lineup
with real OpenCode names, IDs, and FREE or PAID/UNKNOWN labels, including
non-Claude models which native gateway discovery filters out. Misleading
built-in family aliases are hidden. Choose a default with Enter; changes are
synced to the wrapper's saved preference when the CLI exits normally. Before
launch, a stale native default is reconciled to the wrapper's saved preference;
a per-run override is not saved by that reconciliation. The native
session-only choice does not change that default. An explicit `--settings` file
containing its own `modelPicker` takes precedence over this generated lineup.

The catalog also supplies input/context limits for subsequent launches.
Unknown IDs conservatively use 200K tokens; the legacy
Crouton default retains its known 1M limit. Set
`PROMPTBRIDGE_CONTEXT_TOKENS` explicitly for an uncataloged model with a smaller
limit. Restart with `--model MODEL` when changing to a smaller-context model,
since the native client's context budget is fixed when that process starts.

## System prompts

```sh
claude-openapi --system-prompt "Your replacement instructions"
claude-openapi --system-prompt-file /absolute/path/to/prompt.md
claude-openapi --append-system-prompt "Additional instructions"
claude-openapi --append-system-prompt-file /absolute/path/to/rules.md
```

Replacement drops the native default coding instructions; appending retains
them. `--system-prompt-snapshot off` lets edits apply when launching a resumed
conversation. Other native options pass through unchanged.

## Security and reliability

- Loopback-only ephemeral listener, fresh random token and constant-time token
  comparison. The real provider key is not the native client's proxy token.
- At most 24 connections; overflow is closed before worker creation. Absolute
  10-second request-line/header deadlines also stop byte-drip clients. Body/client
  socket inactivity is bounded separately from upstream inference.
- Outbound HTTPS host/base-path allowlist, redirects disabled, explicit model
  IDs preserved on both supported POST routes, and a 32 MiB request-body cap.
- Native `anthropic-beta` and `anthropic-version` headers and query strings
  reach the selected gateway unchanged, including token-count requests.
- Selected retry/rate-limit/request-id headers are preserved; cookies and
  hop-by-hop upstream headers are not copied blindly.
- Key updates use an owner-only staged file, fsync and atomic replacement. A
  failed update preserves the previous key. Empty saved keys get a clear error.
- Private state/home/configuration folders use mode 0700; saved keys use 0600.
  This is configuration separation, **not OS isolation**. The native client and
  its tools keep the invoking user's filesystem authority and working directory.
- Routine HTTP payloads and credentials are not logged. Direct connectivity
  checks use bounded output and redact the literal key.
- SIGTERM preserves exit status 143. Cleanup directly manages the launched CLI;
  descendant tool lifecycle remains the native client's responsibility.

Credential precedence: `OPENCODE_API_KEY`, then the private saved key, then a
read-only lookup of an existing OpenCode API-key store. Go routes prefer the
`opencode-go` record; Console routes prefer `opencode`. OAuth records are ignored.
State defaults to `./state` and can be relocated with `PROMPTBRIDGE_STATE_DIR`.
`PROMPTBRIDGE_TIMEOUT_SECONDS` adjusts the upstream socket timeout from its default
120 seconds, within 1–3600 seconds. This is separate from inbound deadlines.

`PROMPTBRIDGE_UPSTREAM` can select a supported base URL:
`https://opencode.ai/inference/anthropic` (default),
`https://opencode.ai/inference/go/anthropic` (Go subscription), or
`https://opencode.ai/zen` (legacy). Use the matching account/key; choosing a
different route does not create model access. The legacy `CROUTON_STATE_DIR`,
`CROUTON_UPSTREAM`, `CROUTON_TIMEOUT_SECONDS` variables remain supported, with
their neutral replacements taking precedence.

Use `--opencode-status` to inspect the model, nonsecret paths and key source.
Old `--crouton-help/status/check/set-key` commands remain compatible.
Do not publish state, environment files, transcripts, runtime binaries or keys.
The supplied ignore rules exclude these paths.

## Remove or rename-revert

```sh
./rollback.sh --check
./rollback.sh
```

Removal unlinks only the matching installed command and retains all state.
For a custom `--bin-dir`, set the same `PROMPTBRIDGE_BIN_DIR` on removal.
`./rollback-rename.sh` restores the former `claude-crouton` command name while
keeping the **current hardened implementation**, not a vulnerable historical
copy. It is self-contained, works after moving the package and performs explicit
safety checks under optimized Python. Staging failure preserves the working
new-name command. Run `./install.sh` to install the current name again.

## Tests

CI runs on Ubuntu, macOS and Arch Linux; macOS and Arch also install the
authenticated native runtime and exercise a real local tool round-trip.

Portable, credential-free regression suite:

```sh
python3 -B -m unittest discover -s tests -v
```

On macOS or supported Linux after installing the verified runtime, exercise actual
native prompt and Read-tool behavior against a local dummy model:

```sh
python3 -B tests/native_smoke.py --native-case replacement
python3 -B tests/native_smoke.py --native-case append
python3 -B tests/native_smoke.py --native-case file
python3 -B tests/native_smoke.py --native-case tool-loop
python3 -B tests/native_smoke.py --native-case tool-loop --model big-pickle
python3 -B tests/native_picker_smoke.py
```

These tests establish local compatibility, not successful live provider access.
Live access requires a valid key, quota and provider permission. There is no
automatic paid fallback.

## Future API port

Transport, credentials, local resource limits, command lifecycle and the native
adapter are small separate functions/modules. An OpenAI API adapter will need
explicit Responses/tool/stream conversion and its own credential setup and
contract tests; changing a URL alone is not such a port.

### Provider references

- [Current OpenCode models, pricing and client restrictions](https://opencode.ai/docs/console/models/)
- [Official Claude-compatible gateway and Go routing](https://opencode.ai/docs/console/harnesses/claude-code/)
