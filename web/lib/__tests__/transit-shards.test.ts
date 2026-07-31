import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("h3-js", () => ({
  gridDisk: (cell: string) => [cell, "neighbor-cell"],
  latLngToCell: () => "route-cell",
}));

function jsonResponse(ok: boolean, payload?: unknown): Response {
  return {
    ok,
    status: ok ? 200 : 404,
    json: async () => payload,
  } as Response;
}

function bareUrl(input: RequestInfo | URL): string {
  return String(input).split("?")[0];
}

describe("fetchTransitPoisForGeom", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("loads nearby transit POIs from route H3 shards instead of the island-wide file", async () => {
    vi.stubEnv("NEXT_PUBLIC_DATA_BASE", "/data/generated/");
    const busStop = {
      type: "Feature",
      geometry: { type: "Point", coordinates: [103.8, 1.3] },
      properties: { id: "12345", kind: "bus_stop", name: "Test Stop" },
    };
    const mrtExit = {
      type: "Feature",
      geometry: { type: "Point", coordinates: [103.81, 1.31] },
      properties: { id: "NS1-A", kind: "mrt_exit", name: "Test MRT Exit" },
    };

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = bareUrl(input);
      if (url.endsWith("/transit/h3/route-cell.json")) {
        return jsonResponse(true, { type: "FeatureCollection", features: [busStop] });
      }
      if (url.endsWith("/transit/h3/neighbor-cell.json")) {
        return jsonResponse(true, { type: "FeatureCollection", features: [busStop, mrtExit] });
      }
      return jsonResponse(false);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { fetchTransitPoisForGeom } = await import("../data");
    const pois = await fetchTransitPoisForGeom({
      postal: "560234",
      shortest: "??",
      sheltered: "??",
      exposure_gaps: [],
    });

    expect(pois.features).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/^\/data\/generated\/transit\/h3\/route-cell\.json\?v=/),
      { cache: "no-store" }
    );
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringMatching(/^\/data\/generated\/transit\/pois\.json\?v=/),
      { cache: "no-store" }
    );
  });
});
