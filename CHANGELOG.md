# Changes

## 0.3.3 — Model-selection warning and accurate access documentation

- Select each native launch with --model without hard-pinning ANTHROPIC_MODEL;
  /model no longer warns that an old environment selection overrides its save.
- Real native regression switches Big Pickle to Crouton, sends a message,
  verifies the exact upstream request model, and checks the next saved default.
- README now states the direct-API/free-tier client limitation before install,
  explains the 429 separately from model selection, fixes state location and
  command lifecycle wording, and lists the actual native test commands.

## 0.3.2 — Startup diagnostics and real first-run coverage

- Start prompts privately for a missing machine-local OpenCode key instead of
  returning an opaque status 2; command-line missing-key failures remain status 2.
- Preserve sanitized credential/native/provider failure stages in an owner-only
  local report, and display provider status/reason outside the native screen.
- Settings can show the last report without exposing keys, headers or prompts.
- Test production native arguments without test-only permission/MCP overrides.
- Exercise actual theme/security/trust screens from an absent native profile;
  accepting a folder remains an explicit native UI choice, never a config patch.

## 0.3.1 — Reliable dashboard actions

- Recognize application-keypad and CSI-u Enter; ignore unrelated terminal
  reports/key-release events rather than treating them as Cancel.
- Show immediate progress before starting/continuing/resuming a native chat.
- Keep nonzero native exit statuses visible until acknowledged instead of
  immediately redrawing home over a failed launch.
- Compact/dumb-terminal menus accept Enter for the default and retry invalid
  choices instead of silently cancelling or closing the dashboard.
- Flush menu output before raw input, including buffered/embedded terminals.
- End-to-end dashboard/native and menu routing regressions use local fixtures.

## 0.3.0 — Dashboard and consent-based updates

- Colored keyboard-driven `promptbridge` dashboard; compatible legacy alias.
- Saved model, instructions, hidden API key, new/continue/resume chat actions.
- GitHub main update checks with update/skip/skip-until-next-version choices,
  opt-out, manual checks, verification, restart and last-update rollback.
- Stop for local edits, unexpected origin/history and active native sessions.
  A stable bootstrap gates imports during source replacement.
- Preserve private chats/keys/settings and original Claude configuration;
  legacy packages get a managed Git checkout without relocating private state.
- Arch/macOS installers provision both names and record the Python interpreter.
- Local Git failure/restart/rollback, terminal navigation and persistence tests;
  no provider requests are needed for updater verification.

## Reliability fixes

- Reconcile stale native model defaults before launch, so selecting an older
  model actually saves it; one-run model overrides remain temporary.
- Preserve native anthropic-beta capability headers and request query strings
  through both message and token-count routes without changing model IDs.
- Close listeners when worker startup or signal registration fails, without
  blocking on shutdown of a worker that never started.
- Prefer the matching Go/Console record during read-only credential lookup;
  explicit environment and private saved keys retain their existing priority.
- Compare pricing exactly, preserve decimal metadata as text, and reject huge,
  nonfinite or nonzero underflow prices without falsely marking them free.
- Reproduced failures against the prior commit and added targeted regressions;
  the real native picker now covers a stale-default reselection as well.

## Clear native OpenCode model picker

- Replace confusing family-alias rows with an explicit native modelPicker lineup
  including non-Claude OpenCode model names, IDs and pricing labels.
- Reject literal MODEL_ID/example placeholders before launch or preference writes.
- Numbered --opencode-select-model menu saves real IDs without inference or keys.
- Interactive launch refreshes missing/day-old public metadata, retaining cache
  on failure; free-only still fails closed without fresh catalog pricing.
- Preserve other private settings and load only the isolated user settings source.
- Sync native picker default changes on exit; per-session choices stay temporary.
- Real terminal UI regression checks model labels, selection, saving and next launch.

## Arch Linux and macOS installers

- Separate one-command setup entry points and guides for Arch Linux and macOS.
- Arch glibc x86_64/ARM64 target support, with a pinned GPG primary fingerprint,
  signed release manifests and per-launch executable hash verification.
- Whole verified runtime bundles activate atomically; failed updates preserve
  the previous executable and its authentication metadata.
- User-local command installation by default, with collision checks before
  downloads and existing matching Homebrew links preserved.
- Remember the setup interpreter for launching and command removal; no system
  Python modifications, pip dependencies or shell-profile edits.
- Installer regression coverage and actual native tool tests on macOS and Arch.

## Selectable OpenCode models

- Public text/chat model discovery, current-price free filtering and private cache.
- Per-run model selection, environment/saved defaults and native model-picker data.
- Removed the unconditional Crouton request rewrite; explicit model IDs survive.
- Optional free-only guard blocks paid/unpriced models before an inference call.
- Model-specific startup context limits replace the blanket 1M-token setting.
- Neutral management options/environment names with existing aliases retained.
- Defensive model parsing, atomic preference/cache updates, and expanded tests.
- Provider free-tier/client restrictions are documented rather than bypassed.

## Initial hardened source release

- Atomic, owner-only key replacement and explicit invalid-key handling.
- Pre-thread connection limits, absolute header deadlines and body timeouts.
- Rate-limit/retry header passthrough and closed error-response resources.
- Name-only, staged, optimization-independent and self-contained rename recovery.
- Runtime setup with published checksum and Apple-trusted publisher requirement;
  no proprietary executable is redistributed in Git.
- Bounded connectivity-check output, configurable upstream timeout and correct
  SIGTERM status.
- Portable regression tests, native fixture smoke tests and least-privilege CI.
- Neutral project identity for a future OpenAI API adapter. Current protocol and
  provider remain explicitly documented rather than presented as an OpenAI port.
