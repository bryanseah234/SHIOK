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

  it("keeps route evidence and transit POIs visible on the subdued basemap", () => {
    const source = readFileSync(join(__dirname, "../../components/route-evidence-map.tsx"), "utf-8");

    expect(source).toContain('"line-width": 6.8');
    expect(source).toContain('"line-width": 4.8');
    expect(source).toContain('minzoom: 9.8');
    expect(source).toContain('minzoom: 12.0');
  });
});
