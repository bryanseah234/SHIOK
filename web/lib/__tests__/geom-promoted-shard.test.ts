import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("h3-js", () => ({
  latLngToCell: () => "parent-cell",
}));

function jsonResponse(ok: boolean, payload?: unknown): Response {
  return {
    ok,
    status: ok ? 200 : 404,
    json: async () => payload,
  } as Response;
}

describe("fetchGeomForPostal", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("falls back from a missing H3-8 parent shard to promoted child shards", async () => {
    vi.stubEnv("NEXT_PUBLIC_DATA_BASE", "/data/generated/");
    const childRecord = {
      postal: "123456",
      shortest: "encoded-shortest",
      sheltered: "encoded-sheltered",
      exposure_gaps: [],
    };

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/geom/h3/parent-cell.json")) return jsonResponse(false);
      if (url.endsWith("/geom/postal-index.json")) return jsonResponse(false);
      if (url.endsWith("/geom/index.json")) {
        return jsonResponse(true, { "parent-cell": ["child-cell"] });
      }
      if (url.endsWith("/geom/h3/child-cell.json")) return jsonResponse(true, [childRecord]);
      return jsonResponse(false);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { fetchGeomForPostal } = await import("../data");

    await expect(fetchGeomForPostal("123456", 1.3, 103.8)).resolves.toEqual(childRecord);
    expect(fetchMock).toHaveBeenCalledWith("/data/generated/geom/h3/parent-cell.json");
    expect(fetchMock).toHaveBeenCalledWith("/data/generated/geom/index.json");
    expect(fetchMock).toHaveBeenCalledWith("/data/generated/geom/h3/child-cell.json");
  });

  it("loads route geometry for postal-only lookup through the postal shard index", async () => {
    vi.stubEnv("NEXT_PUBLIC_DATA_BASE", "/data/generated/");
    const childRecord = {
      postal: "560234",
      shortest: "encoded-shortest",
      sheltered: "encoded-sheltered",
      exposure_gaps: [],
    };

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/geom/postal-index.json")) {
        return jsonResponse(true, { "560234": "postal-child" });
      }
      if (url.endsWith("/geom/h3/postal-child.json")) return jsonResponse(true, [childRecord]);
      return jsonResponse(false);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { fetchGeomForPostal } = await import("../data");

    await expect(fetchGeomForPostal("560234")).resolves.toEqual(childRecord);
    expect(fetchMock).toHaveBeenCalledWith("/data/generated/geom/postal-index.json");
    expect(fetchMock).toHaveBeenCalledWith("/data/generated/geom/h3/postal-child.json");
  });
});
