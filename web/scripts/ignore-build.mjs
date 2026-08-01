import { execFileSync } from "node:child_process";
import { isAbsolute, relative, resolve } from "node:path";

function git(args) {
  return execFileSync("git", args, { encoding: "utf8" }).trim();
}

function changedFiles() {
  try {
    return git(["diff", "--name-only", "HEAD^", "HEAD"])
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
  } catch {
    return ["__force_build__"];
  }
}

const repoRoot = resolve(git(["rev-parse", "--show-toplevel"]));
const projectRoot = resolve(process.cwd());
const files = changedFiles();

const touchesProject = files.some((file) => {
  if (file === "__force_build__") return true;
  const absolute = resolve(repoRoot, file);
  const rel = relative(projectRoot, absolute);
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
});

if (touchesProject) {
  console.log("Vercel build required: commit touches web project files.");
  process.exit(1);
}

console.log("Vercel build skipped: commit does not touch web project files.");
process.exit(0);
