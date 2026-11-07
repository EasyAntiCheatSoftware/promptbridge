# Changes

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
