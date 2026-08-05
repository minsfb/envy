# Contributing

Changes should preserve the lifecycle and ownership contracts documented in the
README:

- converge and non-purge remove handlers are idempotent and verification is
  non-mutating, so journaled bundle operations can safely resume;
- manifests use structured commands and explicit bundles;
- removal touches only recorded envy-owned resources;
- dry-run is mutation-free;
- credentials and private environment facts never enter source, fixtures, logs,
  state, or command arguments;
- new dependencies are exact, documented, and license-reviewed;
- bumping a provisioned tool version re-records its per-platform SHA-256 digests
  with `python3 lib/provision.py record <tool>`; an artifact whose digest is
  unrecorded must fail closed rather than install.

Run every command listed in the README's Development section before submitting a
change.
