import { decodePolyline } from "./polyline";
import type { PostalGeom } from "./types";

export type LngLat = [number, number];

export interface LineStringFeature {
  type: "Feature";
  geometry: {
    type: "LineString";
    coordinates: LngLat[];
  };
  properties: Record<string, string | number>;
}

export interface LineStringFeatureCollection {
  type: "FeatureCollection";
  features: LineStringFeature[];
}

export interface RouteGeoJson {
  shortest: LineStringFeatureCollection;
  sheltered: LineStringFeatureCollection;
  exposureGaps: LineStringFeatureCollection;
  bounds: [LngLat, LngLat] | null;
  center: LngLat | null;
}

function toLngLat(encoded: string): LngLat[] {
  return decodePolyline(encoded).map(([lat, lng]) => [lng, lat]);
}

function lineFeature(
  coordinates: LngLat[],
  properties: Record<string, string | number>
): LineStringFeature {
  return {
    type: "Feature",
    geometry: {
      type: "LineString",
      coordinates,
    },
    properties,
  };
}

function emptyCollection(): LineStringFeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

function boundsFor(points: LngLat[]): [LngLat, LngLat] | null {
  if (points.length === 0) return null;
  const lngs = points.map(([lng]) => lng);
  const lats = points.map(([, lat]) => lat);
  return [
    [Math.min(...lngs), Math.min(...lats)],
    [Math.max(...lngs), Math.max(...lats)],
  ];
}

export function postalGeomToRouteGeoJson(geom: PostalGeom): RouteGeoJson {
  const shortestCoords = toLngLat(geom.shortest);
  const shelteredCoords = toLngLat(geom.sheltered);
  const gapFeatures = geom.exposure_gaps.map((gap, index) =>
    lineFeature(toLngLat(gap.geom), {
      kind: "exposure_gap",
      label: gap.label,
      len_m: gap.len_m,
      index,
    })
  );

  const allCoords = [
    ...shortestCoords,
    ...shelteredCoords,
    ...gapFeatures.flatMap((feature) => feature.geometry.coordinates),
  ];
  const bounds = boundsFor(allCoords);
  const center: LngLat | null =
    bounds === null
      ? null
      : [(bounds[0][0] + bounds[1][0]) / 2, (bounds[0][1] + bounds[1][1]) / 2];

  return {
    shortest: {
      ...emptyCollection(),
      features: [
        lineFeature(shortestCoords, {
          kind: "shortest",
        }),
      ],
    },
    sheltered: {
      ...emptyCollection(),
      features: [
        lineFeature(shelteredCoords, {
          kind: "sheltered",
        }),
      ],
    },
    exposureGaps: {
      ...emptyCollection(),
      features: gapFeatures,
    },
    bounds,
    center,
  };
}
