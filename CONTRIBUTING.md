# Contributing

Changes should preserve the lifecycle and ownership contracts documented in the
README:

- handlers are idempotent and have non-mutating verification;
- manifests use structured commands and explicit bundles;
- removal touches only recorded envy-owned resources;
- dry-run is mutation-free;
- credentials and private environment facts never enter source, fixtures, logs,
  state, or command arguments;
- new dependencies are exact, documented, and license-reviewed.

Run every command listed in the README's Development section before submitting a
change.
