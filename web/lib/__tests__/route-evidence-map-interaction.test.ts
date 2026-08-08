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
    expect(source).toContain('minzoom: 11.5');
    expect(source).toContain('TRANSIT_POI_HOT_PINK');
  });

  it("pre-fetches manifest on mount and wires interactive click-to-route in page.tsx", () => {
    const pageSource = readFileSync(join(__dirname, "../../app/page.tsx"), "utf-8");

    // Cold-load manifest prefetch
    expect(pageSource).toContain("void fetchManifest().then");

    // Dynamic stop routing
    expect(pageSource).toContain("selectionForChosenStop");
    expect(pageSource).toContain("isCustomStopSelected");
    expect(pageSource).toContain("onResetChosenStop");
    expect(pageSource).toContain("resetCustomStopBtn");
  });

  it("keeps arbitrary clicked OneMap routes preview-only and resettable", () => {
    const pageSource = readFileSync(join(__dirname, "../../app/page.tsx"), "utf-8");
    const liveScoringSource = readFileSync(
      join(__dirname, "../../lib/live-route-scoring.ts"),
      "utf-8"
    );

    expect(liveScoringSource).toContain('state: "NOT_YET_SCORED"');
    expect(liveScoringSource).toContain("total: null");
    expect(liveScoringSource).toContain("subscores: null");
    expect(liveScoringSource).toContain('routing_type: "live_onemap_preview"');
    expect(liveScoringSource).toContain("authoritative_score: false");

    expect(pageSource).toContain("Preview route evidence only");
    expect(pageSource).toContain("Preview only: this clicked stop has route evidence");
    expect(pageSource).toContain('<Metric label="Preview walk" value={formatDistance(selectedDistance)} />');
    expect(pageSource).toContain('<Metric label="Sheltered evidence" value={formatPercent(selectedCoverage)} />');
    expect(pageSource).toContain('<Metric label="Score status" value="Not scored" />');
    expect(pageSource).toContain('previewRoute ? "Preview route" : "Shiokest"');
    expect(pageSource).toContain("{score.paths && !previewRoute && (");
    expect(pageSource).toContain("{score.paths && previewRoute && (");
    expect(pageSource).toContain("{score.paths && !directBusFallback && !previewRoute && (");

    expect(pageSource).toContain("params.delete(\"stop\")");
    expect(pageSource).toContain("onResetChosenStop={() => handleStopSelect(null)}");
    expect(pageSource).toContain("const resolved = nextStopId && nextStopId !== bestCandidateId ? nextStopId : null");
  });

  it("keeps precomputed candidate geometry authoritative instead of demoting it to preview", () => {
    const pageSource = readFileSync(join(__dirname, "../../app/page.tsx"), "utf-8");

    expect(pageSource).toContain("const candGeomOption = baseSelection.geom?.candidates?.[chosenStopId]");
    expect(pageSource).toContain("const candScore = baseSelection.score?.candidates?.find");
    expect(pageSource).toContain("if (candGeomOption && baseSelection.geom)");
    expect(pageSource).toContain('routing_type: candScore?.routing_type ?? "precomputed_candidate"');
    expect(pageSource.indexOf("if (candGeomOption && baseSelection.geom)")).toBeLessThan(
      pageSource.indexOf("Fallback: show route evidence only while OneMap loads in background")
    );
  });
});
