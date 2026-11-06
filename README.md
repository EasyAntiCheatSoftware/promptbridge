# Promptbridge

A small, local coding-agent gateway with private configuration, per-session
authentication, bounded connections and editable system prompts.

The project name is provider-neutral for a future OpenAI API port. **That port
is not implemented yet:** the current adapter runs a separate Claude Code CLI
against OpenCode's Anthropic-compatible gateway, pinned to `crouton-alpha-free`.
The existing `claude-openapi` command is retained for compatibility. No existing
Claude installation or configuration is overwritten.

## Requirements and setup

- macOS, ARM64 or Intel, with Python 3.12 or later.
- An OpenCode API key and provider-granted model access. A proxy does not remove
  provider client restrictions, expired-key errors or quota limits.
- Git for source checkout; the proprietary native executable is **not** stored
  in this repository.

From the project directory:

```sh
python3 runtime_setup.py
./install.sh
claude-openapi --crouton-set-key
claude-openapi --crouton-check
```

The runtime setup downloads a pinned release from the official vendor source,
checks its published SHA-256, and requires the expected Apple-trusted publisher
signature and application identity before activation. Every native launch checks
publisher signature again. A failed check preserves the existing executable.
No ad-hoc re-signing, global vendor installer, or automatic update is used.

The default command directory is `/opt/homebrew/bin`; set
`PROMPTBRIDGE_BIN_DIR` when installing elsewhere. Add that directory to your
terminal's PATH yourself if needed. Existing unrelated commands are preserved.

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
- Outbound HTTPS host/base-path allowlist, redirects disabled, model pinned on
  both supported POST routes, and a 32 MiB request-body cap.
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
read-only lookup of an existing OpenCode API-key store. OAuth records are ignored.
State defaults to `./state` and can be relocated with `CROUTON_STATE_DIR`.
`CROUTON_TIMEOUT_SECONDS` adjusts the upstream socket timeout from its default
120 seconds, within 1–3600 seconds. This is separate from inbound deadlines.

Use `--crouton-status` to inspect nonsecret paths and the selected key source.
Do not publish state, environment files, transcripts, runtime binaries or keys.
The supplied ignore rules exclude these paths.

## Remove or rename-revert

```sh
./rollback.sh --check
./rollback.sh
```

Removal unlinks only the matching installed command and retains all state.
`./rollback-rename.sh` restores the former `claude-crouton` command name while
keeping the **current hardened implementation**, not a vulnerable historical
copy. It is self-contained, works after moving the package and performs explicit
safety checks under optimized Python. Staging failure preserves the working
new-name command. Run `./install.sh` to install the current name again.

## Tests

Portable, credential-free regression suite:

```sh
python3 -B -m unittest discover -s tests -v
```

On macOS after installing the independently verified runtime, exercise actual
native prompt and Read-tool behavior against a local dummy model:

```sh
python3 -B tests/native_smoke.py --native-case replacement
python3 -B tests/native_smoke.py --native-case append
python3 -B tests/native_smoke.py --native-case file
python3 -B tests/native_smoke.py --native-case tool-loop
```

These tests establish local compatibility, not successful live provider access.
Live access requires a valid key, quota and provider permission. There is no
automatic paid fallback.

## Future API port

Transport, credentials, local resource limits, command lifecycle and the native
adapter are small separate functions/modules. An OpenAI API adapter will need
explicit Responses/tool/stream conversion and its own credential setup and
contract tests; changing a URL alone is not such a port.
