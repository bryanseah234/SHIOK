import { postalGeomToRouteGeoJson } from "../route-geojson";
import type { PostalGeom } from "../types";

describe("postalGeomToRouteGeoJson", () => {
  it("converts encoded route geometry into MapLibre line collections", () => {
    const geom: PostalGeom = {
      postal: "560123",
      shortest: "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
      sheltered: "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
      exposure_gaps: [
        {
          geom: "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
          len_m: 42,
          label: "open segment",
        },
      ],
      route_segments: {
        shortest: [
          {
            geom: "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
            len_m: 20,
            is_covered: false,
          },
          {
            geom: "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
            len_m: 30,
            is_covered: true,
          },
        ],
        sheltered: [
          {
            geom: "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
            len_m: 50,
            is_covered: true,
          },
        ],
      },
    };

    const route = postalGeomToRouteGeoJson(geom);

    expect(route.shortest.features[0].geometry.coordinates).toEqual([
      [-120.2, 38.5],
      [-120.95, 40.7],
      [-126.453, 43.252],
    ]);
    expect(route.shortest.features).toHaveLength(2);
    expect(route.shortest.features[0].properties).toMatchObject({
      kind: "shortest",
      is_covered: 0,
      len_m: 20,
      segment_index: 0,
    });
    expect(route.sheltered.features[0].properties.kind).toBe("sheltered");
    expect(route.sheltered.features[0].properties.is_covered).toBe(1);
    expect(route.exposureGaps.features[0].properties).toMatchObject({
      kind: "exposure_gap",
      label: "open segment",
      len_m: 42,
    });
    expect(route.bounds).toEqual([
      [-126.453, 38.5],
      [-120.2, 43.252],
    ]);
    expect(route.center?.[0]).toBeCloseTo(-123.3265);
    expect(route.center?.[1]).toBeCloseTo(40.876);
  });

  it("uses route part arrays as separate fallback line features", () => {
    const geom: PostalGeom = {
      postal: "560231",
      shortest: "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
      sheltered: "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
      shortest_parts: ["_p~iF~ps|U_ulLnnqC", "_ulLnnqC_mqNvxq`@"],
      sheltered_parts: ["_p~iF~ps|U_ulLnnqC", "_ulLnnqC_mqNvxq`@"],
      exposure_gaps: [],
    };

    const route = postalGeomToRouteGeoJson(geom);

    expect(route.shortest.features).toHaveLength(2);
    expect(route.shortest.features[0].geometry.coordinates).toEqual([
      [-120.2, 38.5],
      [-120.95, 40.7],
    ]);
    expect(route.shortest.features[1].properties).toMatchObject({
      kind: "shortest",
      part_index: 1,
    });
  });
});
