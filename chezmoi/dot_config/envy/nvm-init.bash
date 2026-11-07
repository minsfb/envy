if [[ "${ENVY_NVM_ENABLED:-}" == 1 && "${ENVY_NODE_BACKEND:-}" != system && -r "$ENVY_ROOT/lib/nvm.sh" ]]; then
  source "$ENVY_ROOT/lib/nvm.sh"
  if nv_nvm_load; then
    _nv_nvm_prompt() {
      if [[ "${_nv_nvm_previous_pwd:-}" != "$PWD" ]]; then
        nv_nvm_select
        _nv_nvm_previous_pwd="$PWD"
      fi
    }
    if [[ "${PROMPT_COMMAND[*]:-}" != *'_nv_nvm_prompt'* ]]; then
      if declare -p PROMPT_COMMAND 2>/dev/null | grep -q 'declare -a'; then
        PROMPT_COMMAND+=(_nv_nvm_prompt)
      else
        PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND; }_nv_nvm_prompt"
      fi
    fi
    nv_nvm_select
  fi
fi
