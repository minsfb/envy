# Shared interactive and noninteractive Node selection. No downloads here.
nv_nvm_load() {
  export NVM_DIR="${ENVY_NVM_DIR:-$HOME/.nvm}"
  if [ ! -r "$NVM_DIR/nvm.sh" ]; then
    printf '%s\n' 'Managed nvm is missing; run envy upgrade --only nvm' >&2
    return 1
  fi
  . "$NVM_DIR/nvm.sh" --no-use
}

nv_nvm_select() {
  local requested
  requested=""
  if [ "${1:-}" != --default ]; then
    requested="$(nvm_find_nvmrc)"
  fi
  if [ -n "$requested" ]; then
    if ! nvm use --silent; then
      nvm deactivate --silent >/dev/null 2>&1 || true
      printf '%s\n' 'Project Node is unavailable; run nvm install from this project.' >&2
      return 1
    fi
  else
    nvm use --silent default
  fi
}

# System selection cannot interpret nvm's project requirements. Detect them
# without loading nvm, including when nvm is intentionally not installed.
nv_node_find_project() {
  local directory="$PWD"
  while :; do
    if [ -f "$directory/.nvmrc" ]; then
      printf '%s\n' "$directory/.nvmrc"
      return
    fi
    [ "$directory" != / ] || break
    directory="${directory%/*}"
    [ -n "$directory" ] || directory=/
  done
}
