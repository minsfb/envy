# Chezmoi source

This directory is the portable base source managed by the `chezmoi` feature.
Additional explicit bundles may contribute non-overlapping source trees.
`compose_chezmoi.py` rejects duplicate relative source paths before Chezmoi
examines or changes the home directory.

The base owns:

- `.zshrc` and `.bashrc`, including local and bundle fragment extension points;
- tmux configuration, persistence helpers, and agent-session/notification
  helpers;
- Alacritty configuration on macOS;
- a MetaCode notification plugin and Pi lifecycle adapter;
- the locked Mise tool configuration;
- canonical agent guidance with Claude and Codex symlink adapters;
- the optional Pi lifecycle extension.

Bundle shell behavior belongs under
`dot_config/mins-env/shell.d/{NN-name}.pre.zsh`, `.zsh`, `.pre.bash`, or `.bash`.
Pre-fragments load before portable shell defaults; regular fragments load after
them. Machine-only changes belong in `~/.zshrc.local` or `~/.bashrc.local`.

The default Chezmoi source is not pointed at a repository checkout. mins-env
builds an owned source tree under the XDG data directory and always passes it
with `--source`. This keeps multiple bundle roots explicit and makes source-file
ownership auditable.
