import { spawnSync } from "node:child_process";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

function notify(action: "start" | "end", ctx: any): void {
	const home = process.env.HOME;
	if (!home || !process.env.TMUX_PANE) return;

	const sessionId = ctx.sessionManager.getSessionId();
	if (!sessionId) return;
	const transcriptPath = ctx.sessionManager.getSessionFile();
	const payload = JSON.stringify({
		session_id: sessionId,
		transcript_path: transcriptPath ?? null,
		cwd: ctx.cwd ?? process.cwd(),
	});

	spawnSync(join(home, ".local", "bin", "mins-agent-register"), ["pi", action], {
		input: payload,
		stdio: ["pipe", "ignore", "ignore"],
		timeout: 2000,
	});
}

function bell(): void {
	const home = process.env.HOME;
	if (!home) return;
	spawnSync(join(home, ".local", "bin", "mins-agent-notify"), ["pi"], {
		stdio: "ignore",
		timeout: 2000,
	});
}

export default function minsTmuxSession(pi: ExtensionAPI): void {
	pi.on("session_start", (_event, ctx) => notify("start", ctx));
	pi.on("session_shutdown", (_event, ctx) => notify("end", ctx));
	pi.on("agent_settled", () => bell());
}
