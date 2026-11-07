# Security policy

Treat provider keys and user state as private. Never commit `state/`, `runtime/`,
environment files, transcripts, credentials or machine-specific backups.

Security invariants:

1. Only authenticated loopback clients may consume provider authority.
2. Connection allocation and initial parsing must be bounded before authentication.
3. Credentials go only to explicitly supported HTTPS provider endpoints; redirects
   must not forward them to a different host.
4. Failed key rotation must preserve the previous key.
5. Installation/removal/rename operations must preserve unrelated command paths,
   including under Python optimization and partial failure.
6. Native executables must pass checksum/publisher verification before activation;
   do not bypass this by ad-hoc signing or disabling platform checks.
7. Respect explicit OpenCode model IDs. Never silently redirect a free selection
   to a paid model. Free-only sessions must reject paid or unpriced request IDs
   using the current public catalog, including in-session switches.
8. Model discovery is public metadata only: never send credentials or prompts
   with catalog requests. Persist preferences/cache atomically with private modes.

This repository is not a filesystem sandbox or a replacement for the native
client's tool-permission controls. Provider access/retention/quota policies are
external boundaries. Report suspected issues privately to the repository owner.

Runtime platform assurance: macOS requires the Apple-trusted expected signing
identity. Linux requires the detached vendor manifest signature, pinned primary
fingerprint and matching binary SHA-256 at installation and every launch. Linux
verification uses a disposable keyring and never imports into the user's keys.
Do not replace either check with checksum-only trust. Complete bundles activate
atomically so a failed update retains matching old binary/metadata. Installers
never require administrator access; only system dependency installation does.
