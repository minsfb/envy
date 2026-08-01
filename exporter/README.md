# Exporter

`export.py` is the only supported path from the development checkout to a
staging or release tree. It uses `allowlist.txt` as an exact manifest: every
source file must be listed, and every listed file must exist. Adding a source
file therefore fails closed until the manifest is reviewed.

The exporter rejects symlinks, Git metadata, submodule metadata, Git LFS
pointers, binaries, probable credentials, absolute user paths, and unapproved
URL hosts. It never copies source history, refs, tags, remotes, alternates,
replace refs, hooks, or Git objects.

## Preview a directory

Create a local, non-Git snapshot without modifying a remote:

```bash
python3 exporter/export.py \
  --source . \
  --destination /new/path/mins-env-export
```

The destination must not already exist. The completed tree is verified before
an atomic rename makes it visible.

## Stage through Git

Pass the staging repository through an argument or
`MINS_ENV_EXPORT_REMOTE`. Do not put credentials in the URL; use the normal Git
credential or SSH configuration.

```bash
MINS_ENV_EXPORT_REMOTE="$STAGING_REPOSITORY" \
  python3 exporter/export.py --source .
```

Without `--push`, the exporter fetches the selected branch, prepares and
verifies the exact commit in a temporary checkout, reports its commit ID, and
leaves the remote unchanged. Publish that same deterministic export with:

```bash
MINS_ENV_EXPORT_REMOTE="$STAGING_REPOSITORY" \
  python3 exporter/export.py --source . --push
```

The default branch is `main`; use `--branch` to select another branch. An empty
repository is supported. The selected branch is a generated snapshot branch:
each export is a single root commit with no inherited source or staging
history. Publishing replaces the branch using `--force-with-lease` against the
exact fetched tip. The exporter never merges and never performs an unleased
force-push, so a concurrent remote update fails for review and retry. Do not use
the generated branch for hand-authored changes.

Each run uses a fresh temporary repository with templates, hooks, automatic
line-ending conversion, and commit signing disabled. The source commit time,
fixed message, exact tree, and explicit exporter identity determine the root
commit. Re-exporting the same reviewed source therefore produces the same
commit even if the staging branch drifted. Override the generic identity with
`--author-name` and `--author-email` or the corresponding
`MINS_ENV_EXPORT_AUTHOR_NAME` and
`MINS_ENV_EXPORT_AUTHOR_EMAIL` variables.

## Add release metadata

Staging exports contain only allowlisted source content. After rights and
license approval, supply all three release arguments together:

```bash
MINS_ENV_EXPORT_REMOTE="$STAGING_REPOSITORY" \
  python3 exporter/export.py \
    --source . \
    --license-file /approved/LICENSE \
    --approval-id APPROVAL-RECORD-ID \
    --copyright-holder 'APPROVED HOLDER' \
    --push
```

This additionally generates `LICENSE`, per-file `PROVENANCE.json`, a CycloneDX
`SBOM.cdx.json`, and `SHA256SUMS`. Obtain a second review of the exact staged
tree and generated artifacts before external publication. The private parent
repository runs an additional internal-identifier scanner; that denylist stays
outside this export so it does not publish a glossary of private infrastructure.
