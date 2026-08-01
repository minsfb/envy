# Third-party dependencies

mins-env does not vendor or redistribute the tools and plugins below. It invokes
externally installed Chezmoi, Mise, zsh, tmux, Alacritty, Git, Python, Bash, and
Android platform tools under their respective licenses.

The locked Mise toolchain installs these secrets tools without vendoring them:

| Project | Version | License |
| --- | --- | --- |
| age | `1.3.0` | BSD-3-Clause |
| SOPS | `3.10.2` | MPL-2.0 |

Managed shell and tmux plugin checkouts are selected by reviewed release tags
and verified at the immutable commits recorded in `dependencies.json`:

| Project | Revision | License |
| --- | --- | --- |
| zsh-autosuggestions | `e52ee8ca55bcc56a17c828767a3f98f22a68d4eb` | MIT |
| zsh-syntax-highlighting | `db085e4661f6aafd24e5acb5b2e17e4dd5dddf3e` | BSD-3-Clause |
| zsh-history-substring-search | `400e58a87f72ecec14f783fbd29bc6be4ff1641c` | BSD-3-Clause |
| tmux-resurrect | `e87d7d592cac97fa38c12395ebec042c154a1844` | MIT |
| tmux-continuum | `46e0e0023476018ddb4cc0d44783eede27e5a8ec` | MIT |

Before release, independently verify each upstream license at the exact revision
and preserve any required notices. `dependencies.json` is the machine-readable
inventory used to generate the release SBOM.
