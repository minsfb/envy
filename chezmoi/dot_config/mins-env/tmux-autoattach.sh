# tmux-autoattach.sh — opt-in attachment to a persistent tmux session.
#
# A transport or login wrapper may set TMUX_AUTOATTACH=1 and optionally
# TMUX_SESSION. TMUX_AUTO remains accepted during migration.

_mins_tmux_autoattach() {
    if [ -n "${TMUX_AUTOATTACH:-${TMUX_AUTO:-}}" ] \
        && [ -z "${TMUX:-}" ] \
        && command -v tmux >/dev/null 2>&1; then
        local session="${TMUX_SESSION:-main}"
        exec tmux new-session -A -s "$session"
    fi
}

_mins_tmux_autoattach
unset -f _mins_tmux_autoattach 2>/dev/null || true
