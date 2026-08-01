import { execFileSync } from "node:child_process";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

function git(args) {
  return execFileSync("git", args, { encoding: "utf8" }).trim();
}

export function changedFiles() {
  try {
    return git(["diff", "--name-only", "HEAD^", "HEAD"])
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
  } catch {
    return ["__force_build__"];
  }
}

export function fileTouchesProject(file, repoRoot, projectRoot, pathApi = path) {
  if (file === "__force_build__") return true;
  const absolute = pathApi.resolve(repoRoot, file);
  const rel = pathApi.relative(projectRoot, absolute);
  return rel === "" || (!rel.startsWith("..") && !pathApi.isAbsolute(rel));
}

export function anyFileTouchesProject(files, repoRoot, projectRoot, pathApi = path) {
  return files.some((file) => fileTouchesProject(file, repoRoot, projectRoot, pathApi));
}

export function main() {
  const repoRoot = path.resolve(git(["rev-parse", "--show-toplevel"]));
  const projectRoot = path.resolve(process.cwd());
  const touchesProject = anyFileTouchesProject(changedFiles(), repoRoot, projectRoot);

  if (touchesProject) {
    console.log("Vercel build required: commit touches web project files.");
    return 1;
  }

  console.log("Vercel build skipped: commit does not touch web project files.");
  return 0;
}

if (path.resolve(process.argv[1] || "") === path.resolve(fileURLToPath(import.meta.url))) {
  process.exit(main());
}
