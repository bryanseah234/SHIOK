import dataBundle from "../../data-bundle.json";
import { readFileSync } from "fs";
import { join } from "path";

describe("deployment packaging", () => {
  it("uploads the active generated data bundle to Vercel", () => {
    const ignore = readFileSync(join(__dirname, "../../.vercelignore"), "utf-8");
    const activeBundle = String(dataBundle.bundle);

    expect(ignore).toContain("public/data/generated_*/");
    expect(ignore).toContain(`!public/data/${activeBundle}/`);
    expect(ignore).toContain(`!public/data/${activeBundle}/**`);
  });
});
