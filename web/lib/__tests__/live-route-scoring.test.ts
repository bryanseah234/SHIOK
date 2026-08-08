import { describe, expect, it } from "vitest";
import {
  extractShelterEvidence,
  scoreLiveRoute,
} from "../live-route-scoring";
import { encodePolyline, type LatLng } from "../polyline";
import type { PostalGeom, ScoreRecord } from "../types";

describe("live route preview segmentation", () => {
  const sampleShelterPoints: LatLng[] = [
    [1.3501, 103.8501],
    [1.3505, 103.8505],
    [1.3510, 103.8510],
  ];

  const sampleGeom: PostalGeom = {
    postal: "560109",
    shortest: encodePolyline(sampleShelterPoints),
    sheltered: encodePolyline(sampleShelterPoints),
    exposure_gaps: [],
    route_segments: {
      sheltered: [
        {
          geom: encodePolyline(sampleShelterPoints),
          len_m: 140,
          is_covered: true,
          source_class: "lta_covered_linkway",
        },
      ],
      shortest: [
        {
          geom: encodePolyline(sampleShelterPoints),
          len_m: 140,
          is_covered: true,
          source_class: "lta_covered_linkway",
        },
      ],
    },
  };

  const sampleScore: ScoreRecord = {
    postal: "560109",
    state: "SCORED",
    total: 88,
    subscores: {
      access: 95,
      bus: 80,
      rain: 90,
      heat: 85,
      crossing: 90,
    },
    best_node: {
      type: "bus_stop",
      name: "Bef Ang Mo Kio Ave 10",
      routed_m: 140,
    },
    paths: {
      shortest_m: 140,
      sheltered_m: 130,
      detour_pct: 0,
      shade_ratio: 0.15,
    },
    exposure_gaps: [],
    provenance: "test",
    data_as_of: "2026-08-05",
  };

  it("extracts shelter evidence from PostalGeom", () => {
    const evidence = extractShelterEvidence(sampleGeom);
    expect(evidence.length).toBeGreaterThan(0);
    expect(evidence[0].isCovered).toBe(true);
    expect(evidence[0].sourceClass).toBe("lta_covered_linkway");
  });

  it("segments live route along known shelter evidence without fabricating a score", () => {
    const routeCoords: LatLng[] = [
      [1.3501, 103.8501],
      [1.3503, 103.8503],
      [1.3505, 103.8505],
      [1.3510, 103.8510],
    ];

    const result = scoreLiveRoute({
      postal: "560109",
      originCoords: { lat: 1.3501, lng: 103.8501 },
      targetStop: {
        id: "bus:54009",
        name: "Test Bus Stop",
        kind: "bus_stop",
        coordinates: [103.8510, 1.3510],
      },
      routeCoordinates: routeCoords,
      baseScore: sampleScore,
      baseGeom: sampleGeom,
    });

    expect(result.score.state).toBe("NOT_YET_SCORED");
    expect(result.score.total).toBeNull();
    expect(result.score.subscores).toBeNull();
    expect(result.score.total).not.toBe(sampleScore.total);
    expect(result.score.subscores).not.toEqual(sampleScore.subscores);
    expect(result.score.paths?.shortest_m).toBeGreaterThan(0);
    expect(result.score.paths?.covered_ratio).toBeGreaterThan(0.5);
    expect(result.score.paths?.routing_type).toBe("live_onemap_preview");
    expect(result.score.provenance).toMatchObject({
      source: "live_onemap_preview",
      authoritative_score: false,
    });
    expect(result.geom.route_segments?.sheltered?.length).toBeGreaterThan(0);
    expect(result.geom.shortest).toBeTruthy();
  });

  it("segments exposed route far from shelter as exposed with exposure gaps", () => {
    const exposedCoords: LatLng[] = [
      [1.3700, 103.8700],
      [1.3720, 103.8720],
      [1.3740, 103.8740],
    ];

    const result = scoreLiveRoute({
      postal: "560109",
      originCoords: { lat: 1.3700, lng: 103.8700 },
      targetStop: {
        id: "bus:99999",
        name: "Exposed Stop",
        kind: "bus_stop",
        coordinates: [103.8740, 1.3740],
      },
      routeCoordinates: exposedCoords,
      baseScore: sampleScore,
      baseGeom: sampleGeom,
    });

    expect(result.score.state).toBe("NOT_YET_SCORED");
    expect(result.score.paths?.covered_ratio).toBe(0);
    expect(result.geom.exposure_gaps.length).toBeGreaterThan(0);
    expect(result.score.subscores).toBeNull();
  });
});
