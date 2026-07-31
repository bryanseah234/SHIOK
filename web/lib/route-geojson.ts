import { decodePolyline } from "./polyline";
import type { PostalGeom, RouteSegment } from "./types";

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

function toLngLatParts(encodedParts: string[] | undefined, fallback: string): LngLat[][] {
  const source = encodedParts && encodedParts.length > 0 ? encodedParts : [fallback];
  return source.map(toLngLat).filter((coordinates) => coordinates.length > 0);
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

function routeSegmentFeatures(
  segments: RouteSegment[] | undefined,
  kind: "shortest" | "sheltered",
  fallbackParts: LngLat[][]
): LineStringFeature[] {
  const features = (segments ?? [])
    .map((segment, index) =>
      lineFeature(toLngLat(segment.geom), {
        kind,
        is_covered: segment.is_covered ? 1 : 0,
        len_m: segment.len_m,
        segment_index: index,
      })
    )
    .filter((feature) => feature.geometry.coordinates.length > 0);

  if (features.length > 0) return features;
  return fallbackParts.map((coordinates, index) =>
    lineFeature(coordinates, {
      kind,
      part_index: index,
    })
  );
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
  const shortestParts = toLngLatParts(geom.shortest_parts, geom.shortest);
  const shelteredParts = toLngLatParts(geom.sheltered_parts, geom.sheltered);
  const shortestFeatures = routeSegmentFeatures(
    geom.route_segments?.shortest,
    "shortest",
    shortestParts
  );
  const shelteredFeatures = routeSegmentFeatures(
    geom.route_segments?.sheltered,
    "sheltered",
    shelteredParts
  );
  const gapFeatures = geom.exposure_gaps.map((gap, index) =>
    lineFeature(toLngLat(gap.geom), {
      kind: "exposure_gap",
      label: gap.label,
      len_m: gap.len_m,
      index,
    })
  );

  const allCoords = [
    ...shortestFeatures.flatMap((feature) => feature.geometry.coordinates),
    ...shelteredFeatures.flatMap((feature) => feature.geometry.coordinates),
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
      features: shortestFeatures,
    },
    sheltered: {
      ...emptyCollection(),
      features: shelteredFeatures,
    },
    exposureGaps: {
      ...emptyCollection(),
      features: gapFeatures,
    },
    bounds,
    center,
  };
}
