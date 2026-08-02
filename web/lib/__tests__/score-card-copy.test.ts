import { readFileSync } from "fs";
import { join } from "path";

describe("score card copy", () => {
  it("distinguishes far reachable transit from no routed transit", () => {
    const source = readFileSync(join(__dirname, "../../app/page.tsx"), "utf-8");

    expect(source).toContain("Transit beyond scoring range");
    expect(source).toContain("Transit route not connected yet");
    expect(source).toContain("No transit candidate nearby");
    expect(source).toContain("Closest routed ${label} is ${formatDistance(nearestM)}");
    expect(source).toContain("Current scoring range is 1.2 km");
    expect(source).toContain("Walking route not connected yet");
    expect(source).toContain("Outside current candidate thresholds");
  });

  it("keeps shortest route context visible when it matches Shiokest", () => {
    const source = readFileSync(join(__dirname, "../../app/page.tsx"), "utf-8");

    expect(source).toContain("Shortest and Shiokest use the same walk here.");
    expect(source).toContain('sameRoute ? "Shortest (same)" : "Shortest"');
  });
});
