# Changes

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
