import { readFileSync } from "fs";
import { join } from "path";

describe("route evidence map interactions", () => {
  it("does not refit map bounds when feedback points change", () => {
    const source = readFileSync(join(__dirname, "../../components/route-evidence-map.tsx"), "utf-8");
    const fitEffect = source.match(/map\.fitBounds[\s\S]+?\}, \[loaded, routeData\.bounds, routeFitKey\]\);/)?.[0];

    expect(fitEffect).toBeTruthy();
    expect(fitEffect).not.toContain("feedback");
    expect(source).toContain('setSourceData(map, "feedback-route", feedbackData.route)');
    expect(source).toContain('setSourceData(map, "feedback-points", feedbackData.points)');
  });
});
