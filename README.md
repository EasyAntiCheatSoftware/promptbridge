# Promptbridge

A local gateway that runs an independent Claude Code CLI against OpenCode's
Anthropic-compatible inference API. It adds a colored terminal dashboard, model
selection, saved instructions, private credentials, saved chats and confirmed
GitHub updates. Your normal Claude installation/configuration is not changed.

**Current transport: direct OpenCode API.** Promptbridge does not run the
OpenCode TUI/server as its backend. The provider-neutral name leaves room for a
future OpenAI API adapter; **that adapter is not implemented**.

## Important: model pricing is not model access

**A model working in OpenCode does not guarantee it works through Promptbridge.**
OpenCode documents that Console free-tier chat access is limited to OpenCode.
Other clients need provider-granted API access, such as an eligible Go account
or paid-model access. See the [official Console access rules](https://opencode.ai/docs/console/models/#endpoints).

`[FREE]` in the picker means zero published catalog prices—not unlimited usage,
client eligibility, regional availability or a billing guarantee. Promptbridge
does not impersonate OpenCode, reset quotas, or silently fall back to paid models.
Local dummy-backend tests verify compatibility, not live account/model access.

## Install

Supported: Python 3.12+, Git, macOS Apple Silicon/Intel, and glibc Linux
x86_64/ARM64. No pip packages, Node or npm are required. Native runtimes are
pinned and downloaded separately; they are never committed to this repository.

Get the project once, or open your existing checkout:

```sh
git clone https://github.com/EasyAntiCheatSoftware/promptbridge.git
cd promptbridge
```

### Arch Linux

```sh
sudo pacman -Syu --needed python git gnupg ca-certificates
./install-arch.sh
```

Use a full system upgrade, not an Arch partial upgrade. Only dependency setup
needs administrator access. [Complete Arch instructions](docs/install-arch.md).

### macOS

```sh
brew install python git  # skip if Python 3.12+ and Git are already available
./install-macos.sh
```

[Complete macOS instructions](docs/install-macos.md).

Run project setup **without sudo**. New installs put `promptbridge` and its
compatible `claude-openapi` alias in `~/.local/bin`; add that directory to PATH
if prompted. `--bin-dir /your/bin` or `PROMPTBRIDGE_BIN_DIR` chooses another
location. A matching existing `/opt/homebrew/bin` installation is retained;
unrelated commands and shell profiles are preserved. Keep the checkout/package
in place: installed commands link to it. `./install.sh` is the shared installer.

macOS verifies the expected Apple-trusted publisher/app signature. Linux verifies
a signed vendor manifest with a pinned GPG fingerprint and the executable's
SHA-256. Verification runs at activation and every native launch. No global
vendor installer, ad-hoc signing or disabled signature checks are used.

## Everyday use

Type **`promptbridge`** from your project folder. Arrows/Enter, j/k and number
shortcuts operate the dashboard; terminal text is not a mouse-button interface.
`TERM=dumb` or a small terminal uses numbered input: Enter chooses the default,
invalid input retries, and q goes back. `NO_COLOR=1` disables colors.

The home menu supports:

- Start a new chat, continue the last chat in this folder, or select one to resume.
- Search/page through models and save a default.
- Edit persistent system instructions: append, replace or disable.
- Save/replace an OpenCode key using hidden input.
- Check updates, roll back, and inspect settings or the last failure.

A missing key is requested privately before launching. Credentials are local to
your machine; GitHub updates do not transfer them. No OpenRouter account is used.
Native first-run theme, security-notice and workspace-trust prompts still apply.
Only approve a folder you intend the coding CLI to access. Its default **No,
exit** closes the native session without starting a chat; Promptbridge never
preapproves trust or disables tool-permission checks.

Chat files and preferences survive closing the program. No background service
has to stay open overnight. Continue/resume operates on the native client's
isolated saved chats for the same working folder.

`$VISUAL` or `$EDITOR` edits staged private instruction files; nano/vi or multiline
input are fallbacks. Replacement instructions remove the native default coding
instructions; append retains them. Saved instruction mode reapplies on launch
and resume. Explicit command-line instructions remain per-invocation options.

## Models and saved defaults

The dashboard defaults to **free-only**; enabling paid/unknown pricing requires
confirmation. Free-only refreshes public metadata and rejects nonzero or unknown
input/output/cache prices, including in-session model switches. Decimal prices
are compared exactly. This is a catalog-price guard, not a billing/access promise.

For direct CLI use, model precedence is `--model`/`--opencode-model`, then
`PROMPTBRIDGE_MODEL`, then `OPENCODE_MODEL`, then the private saved default.
The dashboard passes its saved selection explicitly. Without a selection, the
legacy `crouton-alpha-free` default remains; an interactive native launch can
ask for another free-listed model if that legacy ID is missing. Example strings
such as `MODEL_ID` are rejected rather than sent as model names.

The native `/model` picker lists real OpenCode names/IDs and FREE or PAID/UNKNOWN
labels. Enter saves a default; the native session-only option does not. Saved
native default changes sync to Promptbridge when the CLI exits normally. A
one-run `--model` override does not replace the saved preference. An explicit
`--settings` file with its own `modelPicker` overrides the generated lineup.

Each native launch uses `--model`; Promptbridge does **not** set the hard
`ANTHROPIC_MODEL` environment override that would outrank a saved `/model` default
on later launches. Parent-shell Anthropic settings are removed only from the
isolated child environment, not changed in your shell. Lower-priority startup
and family/background model defaults keep native aliases from unexpectedly
selecting a different paid provider model.

Actual request model IDs are preserved, including a Crouton selection after
starting on Big Pickle. Native family aliases use the launch model. Changing
this local selection does not change a provider's access rules.

Catalog names/prices are public metadata, not an account entitlement list.
Interactive launches refresh missing/day-old metadata; an offline refresh keeps
the cache. Free-only always needs a fresh successful fetch. New IDs need no
release and an `opencode/` prefix is accepted. Embedding, audio-only,
image-generation and decision endpoints are not chat models and are excluded.

The native context budget comes from catalog input/context limits at startup.
Unknown IDs use a conservative 200K-token default; the legacy Crouton fallback
is 1M. Use `PROMPTBRIDGE_CONTEXT_TOKENS` for an uncataloged smaller model. Relaunch
when switching to a smaller context: changing `/model` does not rebuild the
already-started client's context budget.

## Troubleshooting

### Crouton works in OpenCode, but Promptbridge returns 429

For the message `FreeUsageLimitError` / “Free models work in OpenCode”, the
provider rejected this direct API request. It is not a local Promptbridge usage
counter. Clearing chats, reinstalling, changing a saved default or removing the
old Big Pickle environment warning does not grant free-tier client access.

Console and Go are separate routes. An API key alone does not establish a Go
subscription; choose a route/model your account is entitled to use. See
[Go client requirements](https://opencode.ai/docs/console/go/#where-can-i-use-it)
and the [official Claude Code gateway setup](https://opencode.ai/docs/console/harnesses/claude-code/).
There is no automatic subscription, route change or paid-model fallback.

### Startup exits or no useful error is visible

The dashboard keeps failures visible and distinguishes missing credentials,
native exits and provider HTTP errors. **Settings → Last failure details** shows
the last owner-only `state/last-launch.json` report. It contains fixed sanitized
status summaries, not raw provider bodies, request headers, keys or prompt text,
and is never uploaded. It describes the last failure, not a fresh access test.

A 401 usually needs key/account attention; 403 means denied access; 429 may mean
quota, rate limiting or the specific free-client restriction above. A successful
local test is not proof that a live provider accepted your account/model.

## Updates and rollback

Interactive startup checks GitHub at most once every six hours. An update offers
**Update and restart**, **Skip for now**, or **Skip until the next version**.
The last option suppresses builds of that semantic version; **Updates → Check
now** can still offer it. Automatic checks can be turned off. Noninteractive,
print, help, version and provider-management commands never fetch/prompt for
updates. An offline check does not block opening the dashboard.

Updates require the original GitHub origin, a fast-forward `main` history, clean
source files and no other active native chat. Linked/submodule and private
state/runtime target paths are rejected. Source syntax and native publisher/
version checks run before restart. Verification failure triggers rollback;
concurrent edits that prevent rollback are preserved and reported for attention.
**Updates → Roll back** restores the last matching installation; source edits
and later mismatched updates block that action rather than being overwritten.

Keys/chats/instructions stay in ignored private state. Restart preserves the
working folder, CLI arguments and environment, but never replays an interrupted
model request. Finish active chats before updating. GitHub credentials are used
only if repository access requires them; **Updates → Sign in** can run `gh`
browser sign-in when installed. No API key or administrator password is needed
for updates. GitHub repository/account access is the source-code trust boundary,
not a separately signed release channel.

Normal Git installs update their checkout. Legacy/ZIP installs get a managed
checkout under `<package>/runtime/source-checkout`; their state remains at
`<package>/state`. Keep the package and its managed checkout in place.
`promptbridge --update` is the interactive manual check. `promptbridge --version`
shows the app version; `claude-openapi --version` retains the native CLI version.

## Optional command-line reference

```sh
# Discovery is public; no key or inference is needed.
promptbridge --opencode-models --free
promptbridge --opencode-models --free --json
promptbridge --opencode-select-model --free

# Select a run or save a default (provider access is still required).
promptbridge --free-only --model crouton-alpha-free
promptbridge --opencode-set-model big-pickle
promptbridge --model opencode/glm-5.3-flash-free

# A tiny LIVE access test; unlike catalog listing, this sends a model request.
promptbridge --opencode-check --free-only --model crouton-alpha-free

# Per-run instructions; the dashboard also saves these without CLI commands.
promptbridge --system-prompt "Replacement instructions"
promptbridge --system-prompt-file /absolute/path/to/prompt.md
promptbridge --append-system-prompt "Additional instructions"
promptbridge --append-system-prompt-file /absolute/path/to/rules.md
```

Other native arguments pass through. `--system-prompt-snapshot off` is added so
instruction edits can apply on resumed conversations. Explicit later native
options retain their normal behavior. Old `--crouton-help/status/check/set-key`
and `--opencode-*` commands remain compatible.

## Private state, credentials and resource limits

State defaults to **`<source checkout>/state`**, not the caller's current folder.
For a legacy managed installation it stays at `<package>/state`. Override with
`PROMPTBRIDGE_STATE_DIR` (prefer an absolute path for consistent launches).
Native home/configuration and chats are beneath that directory. This is
configuration separation, **not an OS sandbox**: tools retain the invoking
user's filesystem authority and working directory.

Credential priority: `OPENCODE_API_KEY`, then the private saved key, then a
read-only existing OpenCode API-key store. Console routes prefer `opencode`;
Go routes prefer `opencode-go`. OAuth records are not used. Keys/JSON updates
are owner-only staged writes, fsynced and atomically replaced; failed writes
preserve the previous file. State folders use 0700 and key files use 0600.

`PROMPTBRIDGE_UPSTREAM` accepts these HTTPS bases only:

| Route | Base URL | Access notes |
| --- | --- | --- |
| Console (default) | `https://opencode.ai/inference/anthropic` | API account/model access; Console free-client limit applies |
| Go | `https://opencode.ai/inference/go/anthropic` | Matching Go account/subscription and model/region eligibility |
| Legacy compatibility | `https://opencode.ai/zen` | Legacy route, not the preferred setup |

Legacy `CROUTON_STATE_DIR`, `CROUTON_UPSTREAM` and `CROUTON_TIMEOUT_SECONDS` remain
supported, with the neutral variables taking precedence. `--opencode-status`
shows model, nonsecret paths and key source.

The proxy binds an ephemeral loopback port with a fresh constant-time-checked
token, separate from the provider key. It caps connections at 24, bounds initial
headers to 10 seconds and bodies to 32 MiB, and applies socket timeouts. Upstream
inference defaults to 120 seconds; `PROMPTBRIDGE_TIMEOUT_SECONDS` accepts 1–3600.
Redirects are disabled. Native version/beta headers and query strings are
preserved; only selected retry/rate-limit/request-id response headers are passed.
Cookies and hop-by-hop headers are not copied blindly. Routine request payloads
and credentials are not logged. SIGTERM preserves exit status 143; descendant
tool lifecycle stays the native client's responsibility.

## Remove commands or revert the legacy alias

```sh
./rollback.sh --check
./rollback.sh
```

Removal unlinks only this installation's matching `promptbridge` and
`claude-openapi` command links, retaining state/runtime. For a custom command
directory, set the same `PROMPTBRIDGE_BIN_DIR` on removal. Unrelated commands and
your original Claude executable are preserved.

`./rollback-rename.sh` restores the legacy `claude-crouton` alias while retaining
the current implementation and canonical `promptbridge` command. This is an
alias operation, not a source downgrade. Keep paths stable or repair links before
moving/removing a checkout. Rerun `./install.sh` to restore current aliases.

## Verification

CI runs on Ubuntu, macOS and Arch. Arch/macOS also install the authenticated
native runtime, exercise real native tools/model changes, and complete actual
first-run screens against a local dummy model backend.

```sh
python3 -B -m unittest discover -s tests -v

# After installing the verified native runtime:
python3 -B tests/native_smoke.py --native-case replacement
python3 -B tests/native_smoke.py --native-case append
python3 -B tests/native_smoke.py --native-case file
python3 -B tests/native_smoke.py --native-case tool-loop --model big-pickle
python3 -B tests/native_picker_smoke.py
python3 -B tests/dashboard_smoke.py
python3 -B tests/first_launch_smoke.py --expect-explanation
python3 -B tests/update_runtime_smoke.py
```

These tests send no live provider inference. Use the explicitly labeled access
check above when you intend to test a real account/model. Keep keys, transcripts,
state, environment files and runtime binaries out of Git; supplied ignore rules
exclude them.

## Future API adapter

An OpenAI API adapter still needs deliberate request/tool/stream conversion,
credential setup and contract tests. Changing the base URL alone is not a port.

References: [OpenCode Console](https://opencode.ai/docs/console/models/),
[Go](https://opencode.ai/docs/console/go/),
[Claude Code model precedence](https://code.claude.com/docs/en/model-config#setting-your-model).
