import { describe, expect, it } from "vitest";

import { routesAreSame } from "../route-display";
import type { PostalGeom, ScoreRecord } from "../types";

function selection({
  shortest = "short-route",
  sheltered = "sheltered-route",
  shortestM = 100,
  shelteredM = 100,
  routingType = "sheltered",
  routeSegments,
}: {
  shortest?: string;
  sheltered?: string;
  shortestM?: number;
  shelteredM?: number;
  routingType?: string;
  routeSegments?: PostalGeom["route_segments"];
}) {
  return {
    geom: {
      postal: "560231",
      shortest,
      sheltered,
      exposure_gaps: [],
      route_segments: routeSegments,
    } satisfies PostalGeom,
    score: {
      paths: {
        shortest_m: shortestM,
        sheltered_m: shelteredM,
        detour_pct: 0,
        routing_type: routingType,
      },
    } as ScoreRecord,
  };
}

describe("routesAreSame", () => {
  it("does not collapse same-distance routes when geometry differs", () => {
    expect(
      routesAreSame(
        selection({
          shortest: "direct-shortest-polyline",
          sheltered: "lambda-sheltered-polyline",
          shortestM: 41.4,
          shelteredM: 41.4,
        })
      )
    ).toBe(false);
  });

  it("treats identical geometry and segment provenance as the same route", () => {
    const segments = [
      {
        geom: "same-segment",
        len_m: 25,
        is_covered: true,
        source_class: "lta_covered_linkway",
      },
    ];

    expect(
      routesAreSame(
        selection({
          shortest: "same-polyline",
          sheltered: "same-polyline",
          routeSegments: {
            shortest: segments,
            sheltered: segments,
          },
        })
      )
    ).toBe(true);
  });

  it("does not collapse identical geometry when segment shelter evidence differs", () => {
    expect(
      routesAreSame(
        selection({
          shortest: "same-polyline",
          sheltered: "same-polyline",
          routeSegments: {
            shortest: [
              {
                geom: "same-segment",
                len_m: 25,
                is_covered: false,
                source_class: "exposed",
              },
            ],
            sheltered: [
              {
                geom: "same-segment",
                len_m: 25,
                is_covered: true,
                source_class: "lta_covered_linkway",
              },
            ],
          },
        })
      )
    ).toBe(false);
  });

  it("keeps direct bus fallback as one route because it is not routed geometry", () => {
    expect(
      routesAreSame(
        selection({
          routingType: "direct_bus_fallback_unrouted",
          shortest: "direct-estimate",
          sheltered: "direct-estimate",
        })
      )
    ).toBe(true);
  });
});
