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
    expect(script).toContain("--postals");
    expect(script).toContain("--expected-state");
    expect(script).toContain("--transit-mode");
    expect(script).toContain("--route-mode");
    expect(script).toContain("--must-include");
    expect(script).toContain("result_count");
    expect(script).toContain("Input.dispatchKeyEvent");
    expect(script).toContain("keyboard_search_used");
    expect(script).toContain("transit_mode_selected");
    expect(script).toContain("route_mode_selected");
    expect(script).toContain("required_text_present");
    expect(script).toContain("pending_badge_absent");
    expect(script).toContain("not_yet_copy_distinct_from_no_transit");
    expect(script).toContain("Transit beyond scoring range");
    expect(script).toContain("Closest routed transit");
    expect(script).toContain("bodyHtml");
    expect(script).toContain("Runtime.exceptionThrown");
    expect(script).toContain("score_has_max_denominator");
    expect(script).toContain("map_has_text_equivalent");
    expect(script).toContain("short_mobile_card_bottom_visible");
  });

  it("keeps data-bundle release dry-run safe by default", () => {
    const script = readFileSync(join(__dirname, "../../../scripts/release-data-bundle.ps1"), "utf-8");

    expect(script).toContain("confirm_production_not_set");
    expect(script).toContain("release=not_started");
    expect(script).toContain("-ConfirmProduction");
  });

  it("keeps bundle activation packaging-aware", () => {
    const script = readFileSync(join(__dirname, "../../../scripts/activate-data-bundle.ps1"), "utf-8");

    expect(script).toContain('Join-Path $WebDir "data-bundle.json"');
    expect(script).toContain(".vercelignore");
    expect(script).toContain("!web/public/data/$DataBundle/");
    expect(script).toContain("!public/data/$DataBundle/");
  });

  it("keeps launch check local-only and broad enough for release rehearsal", () => {
    const script = readFileSync(join(__dirname, "../../../scripts/launch-check.ps1"), "utf-8");

    expect(script).toContain("deploy=false");
    expect(script).toContain("uv run python run.py test");
    expect(script).toContain("npm --prefix web test");
    expect(script).toContain("npm --prefix web run build");
    expect(script).toContain("uv run python run.py readiness");
    expect(script).toContain("Find-AvailablePort");
    expect(script).toContain("port_adjusted");
    expect(script).toContain("Stop-ProcessTree");
    expect(script).toContain("Stop-NewListenerOnPort");
    expect(script).toContain("--expected-state no_transit");
    expect(script).toContain("--expected-state not_yet_scored");
    expect(script).toContain("--transit-mode mrt_lrt");
    expect(script).toContain("--route-mode both");
    expect(script).toContain("Release plan only");
    expect(script).not.toContain("deploy-production");
    expect(script).not.toContain("vercel deploy");
  });

  it("keeps postal universe prep bounded and non-deploying", () => {
    const script = readFileSync(
      join(__dirname, "../../../scripts/prepare-postal-universe.ps1"),
      "utf-8",
    );

    expect(script).toContain("confirm_bounded_geocode_not_set");
    expect(script).toContain("postal-universe");
    expect(script).toContain("geocode-universe");
    expect(script).toContain("--confirm-bounded-geocode");
    expect(script).toContain("batch-plan");
    expect(script).toContain("score=false");
    expect(script).toContain("deploy=false");
    expect(script).not.toContain("score-batch");
    expect(script).not.toContain("deploy-production");
    expect(script).not.toContain("vercel deploy");
  });
});
