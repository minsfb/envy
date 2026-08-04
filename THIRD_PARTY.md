# Third-party dependencies

envy does not vendor or redistribute the tools and plugins below. It invokes
externally installed Chezmoi, Mise, zsh, tmux, Alacritty, Git, Python, Bash, and
Android platform tools under their respective licenses.

Chezmoi and Mise are used as found when already installed. When they are absent,
envy may install the exact pinned release into `~/.local/bin` by downloading
the upstream artifact recorded in `dependencies.json` and refusing to proceed
unless its SHA-256 digest matches. Nothing is redistributed: the download comes
from the upstream project, and an artifact with no recorded digest fails closed.

The optional remote-development feature similarly downloads a verified
standalone `code-server` release tree into Envy-owned data. It is not placed on
`PATH`, and no installer script or mutable package source is used.

| Project | Version | License |
| --- | --- | --- |
| Chezmoi | `2.71.1` | MIT |
| Mise | `2026.7.11` | MIT |
| code-server | `4.131.0` | MIT |

The locked Mise toolchain installs these secrets tools without vendoring them:

| Project | Version | License |
| --- | --- | --- |
| age | `1.3.0` | BSD-3-Clause |
| SOPS | `3.10.2` | MPL-2.0 |

On macOS, the managed Alacritty configuration fetches the JetBrains Mono
typeface (`v2.304`, OFL-1.1) from the upstream GitHub release as a chezmoi
external archive pinned by SHA-256; it is not vendored or redistributed.

Managed shell and tmux plugin checkouts are selected by reviewed release tags
and verified at the immutable commits recorded in `dependencies.json`:

| Project | Revision | License |
| --- | --- | --- |
| zsh-autosuggestions | `e52ee8ca55bcc56a17c828767a3f98f22a68d4eb` | MIT |
| zsh-completions | `28c5bdcaf81bb89e56d0df8267d822c3b8aed9e0` | ZSH |
| zsh-syntax-highlighting | `db085e4661f6aafd24e5acb5b2e17e4dd5dddf3e` | BSD-3-Clause |
| zsh-history-substring-search | `400e58a87f72ecec14f783fbd29bc6be4ff1641c` | BSD-3-Clause |
| tmux-resurrect | `e87d7d592cac97fa38c12395ebec042c154a1844` | MIT |
| tmux-continuum | `46e0e0023476018ddb4cc0d44783eede27e5a8ec` | MIT |

Before release, independently verify each upstream license at the exact revision
and preserve any required notices. `dependencies.json` is the machine-readable
inventory of these revisions and licenses.
