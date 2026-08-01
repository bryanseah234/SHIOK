import { describe, expect, it } from "vitest";
import { posix, win32 } from "node:path";
import { anyFileTouchesProject, fileTouchesProject } from "../../scripts/ignore-build.mjs";

describe("Vercel ignored-build file matching", () => {
  it("requires a build when a POSIX commit touches the web project", () => {
    expect(
      fileTouchesProject(
        "web/app/page.tsx",
        "/repo",
        "/repo/web",
        posix
      )
    ).toBe(true);
  });

  it("skips a POSIX docs-only commit outside the web project", () => {
    expect(
      anyFileTouchesProject(
        ["docs/DEPLOYMENT.md", "qa/lighthouse.json"],
        "/repo",
        "/repo/web",
        posix
      )
    ).toBe(false);
  });

  it("requires a build when a Windows commit touches the web project", () => {
    expect(
      fileTouchesProject(
        "web\\scripts\\ensure-data-bundle.mjs",
        "C:\\repo",
        "C:\\repo\\web",
        win32
      )
    ).toBe(true);
  });

  it("skips a Windows docs-only commit outside the web project", () => {
    expect(
      anyFileTouchesProject(
        ["docs\\DEPLOYMENT.md", "qa\\lighthouse.json"],
        "C:\\repo",
        "C:\\repo\\web",
        win32
      )
    ).toBe(false);
  });

  it("forces a build when git diff cannot be evaluated", () => {
    expect(
      fileTouchesProject(
        "__force_build__",
        "/repo",
        "/repo/web",
        posix
      )
    ).toBe(true);
  });
});
