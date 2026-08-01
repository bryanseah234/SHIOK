import dataBundle from "../../data-bundle.json";
import { readFileSync } from "fs";
import { join } from "path";

describe("deployment packaging", () => {
  it("uploads the active generated data bundle to Vercel", () => {
    const webIgnore = readFileSync(join(__dirname, "../../.vercelignore"), "utf-8");
    const rootIgnore = readFileSync(join(__dirname, "../../../.vercelignore"), "utf-8");
    const activeBundle = String(dataBundle.bundle);

    expect(webIgnore).toContain("public/data/generated_*/");
    expect(webIgnore).toContain(`!public/data/${activeBundle}/`);
    expect(webIgnore).toContain(`!public/data/${activeBundle}/**`);
    expect(rootIgnore).toContain("web/public/data/generated_*/");
    expect(rootIgnore).toContain(`!web/public/data/${activeBundle}/`);
    expect(rootIgnore).toContain(`!web/public/data/${activeBundle}/**`);
  });

  it("skips Vercel builds for commits outside the web project", () => {
    const config = JSON.parse(readFileSync(join(__dirname, "../../vercel.json"), "utf-8"));

    expect(config.ignoreCommand).toBe("node scripts/ignore-build.mjs");
  });

  it("materializes derived lookup shards during web builds", () => {
    const script = readFileSync(join(__dirname, "../../scripts/ensure-data-bundle.mjs"), "utf-8");

    expect(script).toContain("ensureDerivedLookupShards");
    expect(script).toContain("writePostalPrefixShards");
    expect(script).toContain("writeTransitH3Shards");
  });

  it("keeps routed browser smoke QA available for launch checks", () => {
    const packageJson = JSON.parse(readFileSync(join(__dirname, "../../package.json"), "utf-8"));
    const script = readFileSync(join(__dirname, "../../scripts/browser-smoke.mjs"), "utf-8");

    expect(packageJson.scripts["qa:browser"]).toBe("node scripts/browser-smoke.mjs");
    expect(script).toContain("Input.dispatchKeyEvent");
    expect(script).toContain("keyboard_search_used");
    expect(script).toContain("score_has_max_denominator");
    expect(script).toContain("map_has_text_equivalent");
    expect(script).toContain("short_mobile_card_bottom_visible");
  });
});
