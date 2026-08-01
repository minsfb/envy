const events = new Set([
  "permission.asked",
  "question.asked",
  "session.error",
  "session.idle",
])

export const NvAgentNotifications = async () => ({
  event: async ({ event }) => {
    if (!events.has(event.type) || !process.env.HOME) return
    const helper = `${process.env.HOME}/.local/bin/nv-agent-notify`
    if (!(await Bun.file(helper).exists())) return
    await Bun.spawn([helper, "metacode"], {
      stdin: "ignore",
      stdout: "ignore",
      stderr: "ignore",
    }).exited
  },
})
