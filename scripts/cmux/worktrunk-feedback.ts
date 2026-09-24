import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import type {
  ExtensionFactory,
  ExtensionReviewSnapshot,
  ExtensionReviewSnapshotNote,
} from "hunkdiff/extension";

type CommandResult = { code: number; stdout: string; stderr: string };
type DeliveryResult = { status: string; delivered_ids: string[] };

function runCommand(command: string, args: string[], input?: string): Promise<CommandResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      reject(new Error(`${command} timed out`));
    }, 10_000);
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ code: code ?? 1, stdout, stderr });
    });
    child.stdin.on("error", () => undefined);
    child.stdin.end(input);
  });
}

function sameRevision(left: ExtensionReviewSnapshot | null, right: ExtensionReviewSnapshot): boolean {
  return left?.generation === right.generation && left.stateRevision === right.stateRevision;
}

function fingerprint(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

function noteFingerprint(note: ExtensionReviewSnapshotNote): string {
  return fingerprint({
    id: note.id,
    fileKey: note.fileKey,
    anchor: note.anchor,
    summary: note.summary,
    rationale: note.rationale,
    resolution: note.resolution,
  });
}

function notePayload(note: ExtensionReviewSnapshotNote, snapshot: ExtensionReviewSnapshot) {
  const file = snapshot.files.find((candidate) => candidate.fileKey === note.fileKey);
  const preferred = note.anchor.preferred;
  if (!file || !preferred) return null;
  const payload = {
    id: note.id,
    source: note.source,
    path: file.path,
    side: preferred.side,
    line: preferred.line,
    summary: note.summary,
    rationale: note.rationale,
    resolution: note.resolution,
  };
  return { ...payload, fingerprint: fingerprint(payload) };
}

const extension: ExtensionFactory = (hunk) => {
  hunk.on("note_changed", async ({ kind, note }, ctx) => {
    if (note.source !== "user") return;
    const operation = kind === "removed" ? "remove" : "upsert";
    const trackingToken = randomUUID().replaceAll("-", "");
    const result = await runCommand(
      "wt-pi",
      ["_review", operation, "--repo", ctx.cwd, "--pid", String(process.pid), "--tracking-token", trackingToken],
      JSON.stringify({ id: note.id, source: note.source, fingerprint: noteFingerprint(note) }),
    );
    if (result.code !== 0) ctx.notify("Could not persist Worktrunk review ownership.", "warning");
  });

  hunk.registerCommand(
    { id: "worktrunk-send-feedback", title: "Send human feedback to task Pi", key: "ctrl+shift+f" },
    async (ctx) => {
      const snapshot = ctx.review.snapshot();
      if (!snapshot) {
        ctx.notify("Review state is unavailable.", "warning");
        return;
      }
      const comments = snapshot.notes
        .filter((note) => note.source === "user")
        .map((note) => notePayload(note, snapshot))
        .filter((note) => note !== null);
      if (comments.length === 0) {
        ctx.notify("There are no saved human comments to send.", "info");
        return;
      }
      for (const comment of comments) {
        const trackingToken = randomUUID().replaceAll("-", "");
        const tracked = await runCommand(
          "wt-pi",
          ["_review", "upsert", "--repo", ctx.cwd, "--pid", String(process.pid), "--tracking-token", trackingToken],
          JSON.stringify({ id: comment.id, source: "user", fingerprint: comment.fingerprint }),
        );
        if (tracked.code !== 0) {
          ctx.notify("Could not establish review ownership; feedback was not sent.", "error");
          return;
        }
      }
      if (!sameRevision(ctx.review.snapshot(), snapshot)) {
        ctx.notify("Review state changed; feedback was not sent.", "warning");
        return;
      }
      const sent = await runCommand(
        "wt-pi",
        ["feedback", "--repo", ctx.cwd],
        JSON.stringify({ comments }),
      );
      if (sent.code !== 0) {
        ctx.notify(sent.stderr.trim() || "Feedback was not delivered.", "error");
        return;
      }
      let receipt: DeliveryResult;
      try {
        receipt = JSON.parse(sent.stdout) as DeliveryResult;
      } catch {
        ctx.notify("Feedback delivery returned an invalid receipt; comments were retained.", "error");
        return;
      }
      const expectedIds = new Set(comments.map((comment) => comment.id));
      if (
        !["delivered", "already-delivered"].includes(receipt.status) ||
        !Array.isArray(receipt.delivered_ids) ||
        receipt.delivered_ids.length !== expectedIds.size ||
        new Set(receipt.delivered_ids).size !== expectedIds.size ||
        receipt.delivered_ids.some((id) => typeof id !== "string" || !expectedIds.has(id))
      ) {
        ctx.notify("Feedback delivery returned an invalid receipt; comments were retained.", "error");
        return;
      }
      if (!sameRevision(ctx.review.snapshot(), snapshot)) {
        ctx.notify("Feedback was delivered, but review state changed; comments were retained.", "warning");
        return;
      }
      let sessionId: string;
      try {
        const listed = await runCommand("hunk", ["session", "list", "--json"]);
        if (listed.code !== 0) throw new Error("Session lookup failed");
        const sessions = JSON.parse(listed.stdout).sessions.filter(
          (session: { pid: number; cwd: string }) => session.pid === process.pid && session.cwd === ctx.cwd,
        );
        if (sessions.length !== 1 || typeof sessions[0].sessionId !== "string") {
          throw new Error("Ambiguous session identity");
        }
        sessionId = sessions[0].sessionId;
      } catch {
        ctx.notify("Feedback was delivered, but the Hunk session could not be identified; comments were retained.", "warning");
        return;
      }
      if (!sameRevision(ctx.review.snapshot(), snapshot)) {
        ctx.notify("Feedback was delivered, but review state changed; comments were retained.", "warning");
        return;
      }
      for (const commentId of receipt.delivered_ids) {
        const removed = await runCommand(
          "hunk",
          ["session", "comment", "rm", sessionId, commentId, "--json"],
        );
        if (removed.code !== 0) {
          ctx.notify("Feedback was delivered, but one or more comments could not be removed.", "warning");
          return;
        }
      }
      ctx.notify(`Delivered ${receipt.delivered_ids.length} human comment${receipt.delivered_ids.length === 1 ? "" : "s"}.`, "info");
    },
  );
};

export default extension;
