# Cascading startup configuration

The chezmoi feature owns includes in host startup files, and whole managed
fragments under the configured XDG config directory's `envy/` directory.
It installs the fragments before adding the includes. Host configuration runs
first, followed by the core overlay, bundle overrides, and machine-local
`.bashrc.local`, `.zshrc.local`, or `.tmux.conf.local`. Bash-to-Zsh handoff and
tmux attachment happen after shell configuration.

Interactive login Bash defers its overlay until the end of the first existing
`.bash_profile`, `.bash_login`, or `.profile`. With no profile, envy creates a
`.bash_profile` containing its hook. The hook loads `.bashrc` if the profile did
not already do so. POSIX shells ignore the Bash hook. Interactive login Zsh
defers its overlay until the end of `.zlogin`; nonlogin Zsh uses `.zshrc`.
An explicit `ZDOTDIR` inside HOME is supported. Set it consistently for install
and verification. Runtime-only relocation from another startup script cannot be
discovered without executing user code. Relocation requires removing the old
recorded integration first. Noninteractive shells do not run these overlays.

The integration owns only the exact `# >>> envy >>>` / `# <<< envy <<<` block
and any separator newline it inserted. Upgrade preserves outside bytes and
moves the block to the end. Removal preserves outside edits and removes a
newly created startup file only if nothing remains. Symlink startup files,
symlink parent directories, edited/unowned blocks, and duplicate/malformed
markers require manual reconciliation; they are never overwritten.
The handler does not parse arbitrary shell control flow: a host file that exits
before its include needs a reviewed placement change. Test new shells before
closing an existing session.

## Existing installations

Before replacing the composed source, the provider prepares a migration using
the old rendered targets and the original-file backup ledger. A still-intact
host original is preserved. A byte-identical old generated file is replaced
with its recorded original plus an include; a file originally created by envy
becomes an include-only file. Customized/merged generated files are refused
with their path, without printing their contents. Reconcile those files with
the recorded originals and move personal settings into the corresponding
`.local` file before retrying. Missing ownership evidence is a refusal.

The hash-only `startup-integration.json` journal under the envy state directory
lets interrupted migrations resume after the old source has been replaced.
All inputs are checked before writes; concurrent external edits are unsupported
and detected changes stop application. The operation is not a multi-file atomic
transaction. Old originals remain archived, but the legacy whole-file removal
entries are retired so uninstall cannot restore them over later host edits.

Bundles that depend on this lifecycle declare `requires: ["startup-includes-v1"]`.
Optional `settings.startup.defaults` maps `.bashrc` and `.zshrc` to absolute host
skeleton paths. Available skeletons seed only absent originals; existing host
files are retained. Seeded host configuration is retained on removal. Paths
specific to an organization belong in its bundle.

Verification: `envy verify --only chezmoi` and `envy doctor chezmoi`. Repair:
`envy upgrade --only chezmoi`. Dry-run does not write hooks or journal state.
