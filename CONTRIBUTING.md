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
- core features, defaults, documentation, and tests remain vendor- and
  environment-neutral, except for explicitly task- and bundle-overridable
  review-skill model defaults;
- organization-specific hosts, tools, adapters, and
  policies belong in explicit bundles behind generic extension points;
- new dependencies are exact, documented, and license-reviewed;
- bumping a provisioned tool version re-records its per-platform SHA-256 digests
  with `python3 lib/provision.py record <tool>`; an artifact whose digest is
  unrecorded must fail closed rather than install.

The portable review skill's documented GPT/Claude family pair is an explicit,
task- and bundle-overridable default, not a vendor binding of the offline
controller. This exception covers the skill's default-family prose, supporting
references, and synthetic fixtures and test assertions exercising those defaults;
the controller, schema, manifests, and unrelated features remain vendor-neutral.
Exact models remain runtime or bundle decisions; organization-specific access
and launch-evidence integration stay in private adapters behind generic extension
points. Bundle adapters supply explicit preparation inputs as described
in [review model selection](assets/agent-assets/skills/review/references/model-selection.md),
not a duplicate managed skill path.

Run every command listed in the README's Development section before submitting a
change.
