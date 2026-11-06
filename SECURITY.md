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

This repository is not a filesystem sandbox or a replacement for the native
client's tool-permission controls. Provider access/retention/quota policies are
external boundaries. Report suspected issues privately to the repository owner.
