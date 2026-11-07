# Changes

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
