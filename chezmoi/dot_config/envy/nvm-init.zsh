if [[ "${ENVY_NVM_ENABLED:-}" == 1 && "${ENVY_NODE_BACKEND:-}" != system && -r "$ENVY_ROOT/lib/nvm.sh" ]]; then
  source "$ENVY_ROOT/lib/nvm.sh"
  if nv_nvm_load; then
    autoload -U add-zsh-hook
    add-zsh-hook -d chpwd nv_nvm_select 2>/dev/null
    add-zsh-hook chpwd nv_nvm_select
    nv_nvm_select
  fi
fi
