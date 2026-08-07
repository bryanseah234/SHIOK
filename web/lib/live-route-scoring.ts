/**
 * Live client-side shelter segmentation and dynamic SHIOK scoring.
 *
 * Takes a pedestrian walking route (e.g. from OneMap API or direct path),
 * tests sub-segments against local shelter evidence (LTA linkways, HDB void decks,
 * overhead bridges), and produces full multi-color RouteSegments and calibrated
 * ScoreRecord metrics.
 */

import { haversineMeters, type TransitCandidate } from "./nearest-transit";
import { decodePolyline, encodePolyline, type LatLng } from "./polyline";
import type {
  ExposureGap,
  GeomGap,
  PostalGeom,
  RouteSegment,
  ScoreRecord,
  Subscores,
} from "./types";

export interface ShelterFeature {
  points: LatLng[];
  isCovered: boolean;
  sourceClass: string;
}

export type LiveTargetStop =
  | TransitCandidate
  | {
      id: string;
      name: string;
      kind: string;
      coordinates: [number, number];
      code?: string;
      station?: string;
      exit?: string;
      straight_line_m?: number;
    };

export interface LiveRouteOptions {
  postal: string;
  originCoords: { lat: number; lng: number };
  targetStop: LiveTargetStop;
  routeCoordinates: LatLng[];
  baseScore?: ScoreRecord | null;
  baseGeom?: PostalGeom | null;
}

/**
 * Extract known shelter segments from a loaded PostalGeom to use as local ground truth.
 */
export function extractShelterEvidence(baseGeom?: PostalGeom | null): ShelterFeature[] {
  if (!baseGeom) return [];
  const features: ShelterFeature[] = [];

  const segments = [
    ...(baseGeom.route_segments?.sheltered ?? []),
    ...(baseGeom.route_segments?.shortest ?? []),
  ];

  for (const seg of segments) {
    if (!seg.geom) continue;
    try {
      const pts = decodePolyline(seg.geom);
      if (pts.length >= 2) {
        features.push({
          points: pts,
          isCovered: seg.is_covered,
          sourceClass: seg.source_class ?? (seg.is_covered ? "osm_covered" : "exposed"),
        });
      }
    } catch {
      // Ignore malformed segment polylines
    }
  }

  // Also extract from any pre-computed candidate geometries
  if (baseGeom.candidates) {
    for (const opt of Object.values(baseGeom.candidates)) {
      const optSegs = [
        ...(opt.route_segments?.sheltered ?? []),
        ...(opt.route_segments?.shortest ?? []),
      ];
      for (const seg of optSegs) {
        if (!seg.geom) continue;
        try {
          const pts = decodePolyline(seg.geom);
          if (pts.length >= 2) {
            features.push({
              points: pts,
              isCovered: seg.is_covered,
              sourceClass: seg.source_class ?? (seg.is_covered ? "osm_covered" : "exposed"),
            });
          }
        } catch {
          // Ignore
        }
      }
    }
  }

  return features;
}

/** Distance in metres from a point to a line segment. */
function distanceToSegmentMeters(
  p: LatLng,
  a: LatLng,
  b: LatLng
): number {
  const [pLat, pLng] = p;
  const [aLat, aLng] = a;
  const [bLat, bLng] = b;

  const dx = bLng - aLng;
  const dy = bLat - aLat;
  const lenSq = dx * dx + dy * dy;

  if (lenSq === 0) {
    return haversineMeters(pLat, pLng, aLat, aLng);
  }

  let t = ((pLng - aLng) * dx + (pLat - aLat) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));

  const projLat = aLat + t * dy;
  const projLng = aLng + t * dx;
  return haversineMeters(pLat, pLng, projLat, projLng);
}

/** Check if a midpoint is within `thresholdMeters` of any known shelter segment. */
function findMatchingShelter(
  mid: LatLng,
  shelterEvidence: ShelterFeature[],
  thresholdMeters = 18
): { isCovered: boolean; sourceClass: string } | null {
  for (const feature of shelterEvidence) {
    if (!feature.isCovered) continue;
    for (let i = 0; i < feature.points.length - 1; i++) {
      const d = distanceToSegmentMeters(mid, feature.points[i], feature.points[i + 1]);
      if (d <= thresholdMeters) {
        return { isCovered: true, sourceClass: feature.sourceClass };
      }
    }
  }
  return null;
}

/**
 * Segment a walking route polyline into colored sheltered / exposed RouteSegments
 * and compute real-time scores.
 */
export function scoreLiveRoute(options: LiveRouteOptions): {
  geom: PostalGeom;
  score: ScoreRecord;
} {
  const {
    postal,
    originCoords,
    targetStop,
    routeCoordinates,
    baseScore,
    baseGeom,
  } = options;

  const shelterEvidence = extractShelterEvidence(baseGeom);

  // Ensure we have at least start and end
  const coords: LatLng[] =
    routeCoordinates.length >= 2
      ? routeCoordinates
      : [
          [originCoords.lat, originCoords.lng],
          [targetStop.coordinates[1], targetStop.coordinates[0]],
        ];

  interface SubChunk {
    pts: LatLng[];
    lenM: number;
    isCovered: boolean;
    sourceClass: string;
  }

  const chunks: SubChunk[] = [];
  let totalDistanceM = 0;
  let totalShelteredM = 0;

  // Process each polyline segment
  for (let i = 0; i < coords.length - 1; i++) {
    const p1 = coords[i];
    const p2 = coords[i + 1];
    const segLen = haversineMeters(p1[0], p1[1], p2[0], p2[1]);
    if (segLen <= 0.1) continue;

    totalDistanceM += segLen;
    const mid: LatLng = [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2];

    const match = findMatchingShelter(mid, shelterEvidence);
    const isCovered = match?.isCovered ?? false;
    const sourceClass = match?.sourceClass ?? (isCovered ? "osm_covered" : "exposed");

    if (isCovered) {
      totalShelteredM += segLen;
    }

    // Merge into previous chunk if continuous with same coverage
    const prev = chunks[chunks.length - 1];
    if (prev && prev.isCovered === isCovered && prev.sourceClass === sourceClass) {
      prev.pts.push(p2);
      prev.lenM += segLen;
    } else {
      chunks.push({
        pts: [p1, p2],
        lenM: segLen,
        isCovered,
        sourceClass,
      });
    }
  }

  // Fallback if empty chunks
  if (chunks.length === 0) {
    const directLen = haversineMeters(
      originCoords.lat,
      originCoords.lng,
      targetStop.coordinates[1],
      targetStop.coordinates[0]
    );
    totalDistanceM = directLen;
    chunks.push({
      pts: [
        [originCoords.lat, originCoords.lng],
        [targetStop.coordinates[1], targetStop.coordinates[0]],
      ],
      lenM: directLen,
      isCovered: false,
      sourceClass: "exposed",
    });
  }

  // Convert chunks to RouteSegments and ExposureGaps
  const routeSegments: RouteSegment[] = [];
  const geomGaps: GeomGap[] = [];
  const exposureGaps: ExposureGap[] = [];

  for (const chunk of chunks) {
    const encoded = encodePolyline(chunk.pts);
    const roundedLen = Math.round(chunk.lenM);
    routeSegments.push({
      geom: encoded,
      len_m: roundedLen,
      is_covered: chunk.isCovered,
      source_class: chunk.sourceClass,
      source_layer: chunk.sourceClass,
      confidence: chunk.isCovered ? "high" : "low",
    });

    if (!chunk.isCovered && roundedLen >= 4) {
      geomGaps.push({
        geom: encoded,
        len_m: roundedLen,
        label: "Exposed walk",
      });
      const midPoint = chunk.pts[Math.floor(chunk.pts.length / 2)];
      exposureGaps.push({
        len_m: roundedLen,
        label: "Exposed walk",
        location: {
          lat: midPoint[0],
          lon: midPoint[1],
        },
      });
    }
  }

  const wholePolyline = encodePolyline(coords);
  const roundedTotalM = Math.round(totalDistanceM);
  const roundedShelteredM = Math.round(totalShelteredM);
  const coveredRatio = roundedTotalM > 0 ? roundedShelteredM / roundedTotalM : 0;

  // Derive calibrated subscores matching SHIOK Index formulas
  // 1. Access Score: full at <= 400m, 40 at 800m, 0 at 1200m
  let accessScore = 0;
  if (roundedTotalM <= 400) {
    accessScore = 100;
  } else if (roundedTotalM <= 800) {
    const ratio = (roundedTotalM - 400) / (800 - 400);
    accessScore = 100 - ratio * (100 - 40);
  } else if (roundedTotalM <= 1200) {
    const ratio = (roundedTotalM - 800) / (1200 - 800);
    accessScore = 40 - ratio * 40;
  } else {
    accessScore = 0;
  }

  // 2. Rain Shelter Score
  const rainScore = Math.min(100, Math.max(0, coveredRatio * 100));

  // 3. Heat Comfort Score (shelter + tree shade proxy)
  const baseShadeRatio = baseScore?.paths?.shade_ratio ?? 0.12;
  const heatScore = Math.min(
    100,
    Math.max(0, (coveredRatio + baseShadeRatio * (1 - coveredRatio)) * 100)
  );

  // 4. Bus connectivity / MRT access
  const busScore = baseScore?.subscores?.bus ?? (targetStop.kind === "bus_stop" ? 85 : 90);

  // 5. Crossing friction
  const crossingScore = Math.max(0, 100 - exposureGaps.length * 15);

  // Weighted composite total (Access: 35%, Rain: 35%, Heat: 15%, Crossing: 15%)
  const compositeTotal =
    roundedTotalM > 1200
      ? 0
      : Math.round(
          accessScore * 0.35 +
            rainScore * 0.35 +
            heatScore * 0.15 +
            crossingScore * 0.15
        );

  const subscores: Subscores = {
    access: Math.round(accessScore),
    bus: busScore ? Math.round(busScore) : null,
    rain: Math.round(rainScore),
    heat: Math.round(heatScore),
    crossing: Math.round(crossingScore),
  };

  const isMrt = targetStop.kind === "mrt_exit";
  const bestNode = {
    type: isMrt ? "mrt_lrt_exit" : "bus_stop",
    name: targetStop.name,
    station: "station" in targetStop ? targetStop.station : undefined,
    exit: "exit" in targetStop ? targetStop.exit : undefined,
    routed_m: roundedTotalM,
    straight_line_m: roundedTotalM,
    snap_distance_m: 0,
  };

  const scoreRecord: ScoreRecord = {
    postal,
    state: roundedTotalM > 1200 ? "NO_TRANSIT_IN_RANGE" : "SCORED",
    total: roundedTotalM > 1200 ? null : compositeTotal,
    subscores,
    best_node: bestNode,
    paths: {
      shortest_m: roundedTotalM,
      sheltered_m: roundedShelteredM,
      detour_pct: 0,
      covered_m: roundedShelteredM,
      covered_ratio: Number(coveredRatio.toFixed(3)),
      shade_m: Math.round(roundedTotalM * baseShadeRatio),
      shade_ratio: baseShadeRatio,
      heat_comfort_m: Math.round(roundedShelteredM + roundedTotalM * baseShadeRatio),
      heat_comfort_ratio: Number(
        (coveredRatio + baseShadeRatio * (1 - coveredRatio)).toFixed(3)
      ),
      routing_type: "live_onemap_snapped",
    },
    exposure_gaps: exposureGaps,
    candidates: baseScore?.candidates ?? [],
    data_as_of: baseScore?.data_as_of ?? null,
    provenance: "live_onemap_shelter_snapped",
  };

  const postalGeom: PostalGeom = {
    postal,
    shortest: wholePolyline,
    sheltered: wholePolyline,
    exposure_gaps: geomGaps,
    route_segments: {
      shortest: routeSegments,
      sheltered: routeSegments,
    },
  };

  return {
    geom: postalGeom,
    score: scoreRecord,
  };
}
