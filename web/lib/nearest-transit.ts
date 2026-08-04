/**
 * Nearest-transit derivation for the point-to-point stop picker.
 *
 * Pure client-side helpers: given a postal's origin coordinate and the loaded
 * transit POI collection, rank the nearest bus_stop / mrt_exit POIs by direct
 * (haversine) distance and pick the top N candidates.
 *
 * The current score bundle does NOT ship a ranked candidate list or per-stop
 * route geometry alongside each postal, so:
 *   - Candidate distances here are STRAIGHT-LINE, not routed metres.
 *   - Coverage / sheltered ratios are NOT known for non-best candidates.
 *   - The map route line stays on the auto-picked best_transit stop.
 * The picker surface documents these limits (see `TransitStopPicker`).
 */
import type {
  ScoreRecord,
  TransitAccessMode,
  TransitPoiCollection,
} from "./types";

export type TransitCandidateKind = "bus_stop" | "mrt_exit";

export interface TransitCandidate {
  /** Stable POI id (e.g. "bus:66361", "mrt:21491"). Matches feature.properties.id. */
  id: string;
  name: string;
  kind: TransitCandidateKind;
  /** Straight-line distance in metres from the postal origin. */
  straight_line_m: number;
  /** [lng, lat] as stored in the POI feature. */
  coordinates: [number, number];
  /** Bus stop code / MRT exit label, when available. */
  code?: string;
  station?: string;
  exit?: string;
}

const EARTH_RADIUS_M = 6371008.8;

/** Great-circle distance in metres between two lat/lng pairs (WGS84 spherical). */
export function haversineMeters(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number
): number {
  const rad = (x: number) => (x * Math.PI) / 180;
  const dLat = rad(lat2 - lat1);
  const dLng = rad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

function allowedKindsForMode(mode: TransitAccessMode): Set<string> {
  if (mode === "bus") return new Set(["bus_stop"]);
  if (mode === "mrt_lrt") return new Set(["mrt_exit"]);
  return new Set(["bus_stop", "mrt_exit"]);
}

function asString(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed.length ? trimmed : undefined;
}

/**
 * Rank the nearest picker-eligible transit POIs to the postal origin.
 * `mode` gates which kinds are eligible (matches the score-card transit toggle).
 * `mrt_station` features are excluded because they are parents of exits.
 */
export function deriveNearestTransitCandidates(options: {
  originLat: number;
  originLng: number;
  transitPois: TransitPoiCollection | null;
  mode: TransitAccessMode;
  limit?: number;
}): TransitCandidate[] {
  const { originLat, originLng, transitPois, mode, limit = 5 } = options;
  if (!transitPois?.features?.length) return [];
  if (!Number.isFinite(originLat) || !Number.isFinite(originLng)) return [];

  const allowed = allowedKindsForMode(mode);
  const seen = new Set<string>();
  const candidates: TransitCandidate[] = [];

  for (const feature of transitPois.features) {
    const kind = feature.properties?.kind;
    if (!allowed.has(kind as string)) continue;

    const id = asString(feature.properties?.id);
    if (!id || seen.has(id)) continue;

    const coords = feature.geometry?.coordinates;
    if (!Array.isArray(coords) || coords.length < 2) continue;
    const [lng, lat] = coords;
    if (typeof lng !== "number" || typeof lat !== "number") continue;

    seen.add(id);
    candidates.push({
      id,
      name: asString(feature.properties?.name) ?? id,
      kind: kind === "mrt_exit" ? "mrt_exit" : "bus_stop",
      straight_line_m: haversineMeters(originLat, originLng, lat, lng),
      coordinates: [lng, lat],
      code: asString(feature.properties?.code),
      station: asString(feature.properties?.station),
      exit: asString(feature.properties?.exit),
    });
  }

  candidates.sort((a, b) => a.straight_line_m - b.straight_line_m);
  return candidates.slice(0, Math.max(0, limit));
}

/**
 * Attempt to map the auto-picked `best_node` back to a POI id.
 *  - Bus stops: score records `exit=<stop_code>`, POIs use `bus:<code>`.
 *  - MRT exits: match by station + exit; if unavailable, by full name.
 * Returns null when no confident match is found.
 */
export function resolveBestCandidateId(
  candidates: TransitCandidate[],
  score: ScoreRecord | null
): string | null {
  const node = score?.best_node;
  if (!node) return null;

  if (node.type === "bus_stop" && node.exit) {
    const bus = `bus:${node.exit}`;
    if (candidates.some((candidate) => candidate.id === bus)) return bus;
    // Fall through to name matching if the shard did not include the coded POI.
  }

  if (node.type === "mrt_lrt_exit") {
    const station = node.station?.toLowerCase();
    const exit = node.exit?.toLowerCase();
    if (station && exit) {
      const match = candidates.find(
        (candidate) =>
          candidate.station?.toLowerCase() === station &&
          candidate.exit?.toLowerCase() === exit
      );
      if (match) return match.id;
    }
  }

  const name = node.name?.toLowerCase();
  if (!name) return null;
  const nameMatch = candidates.find(
    (candidate) => candidate.name.toLowerCase() === name
  );
  return nameMatch?.id ?? null;
}

export interface CandidateComparison {
  fartherPct: number;
  bestStraightM: number;
  activeStraightM: number;
}

/**
 * Percentage of extra straight-line distance for the active pick vs the best.
 * Returns null when either side is missing or the best distance is not positive.
 * NOTE: this is direct distance only — routed comparisons need per-candidate
 * scoring data that today's bundle does not ship.
 */
export function candidateComparison(
  active: TransitCandidate | null | undefined,
  best: TransitCandidate | null | undefined
): CandidateComparison | null {
  if (!active || !best) return null;
  if (!(best.straight_line_m > 0)) return null;
  const pct =
    ((active.straight_line_m - best.straight_line_m) / best.straight_line_m) *
    100;
  return {
    fartherPct: pct,
    bestStraightM: best.straight_line_m,
    activeStraightM: active.straight_line_m,
  };
}

/** Result of a keyboard navigation event on the chip row. */
export type ChipKeyAction =
  | { kind: "focus"; index: number }
  | { kind: "activate"; chipId: string }
  | { kind: "ignore" };

/**
 * Pure keyboard-navigation helper: given the chip ids in order, the current
 * index, and the key pressed, return the resulting action.
 */
export function nextChipAction(
  chipIds: string[],
  currentIndex: number,
  key: string
): ChipKeyAction {
  if (chipIds.length === 0) return { kind: "ignore" };
  if (key === "ArrowRight") {
    const next = (currentIndex + 1 + chipIds.length) % chipIds.length;
    return { kind: "focus", index: next };
  }
  if (key === "ArrowLeft") {
    const next = (currentIndex - 1 + chipIds.length) % chipIds.length;
    return { kind: "focus", index: next };
  }
  if (key === "Enter" || key === " " || key === "Spacebar") {
    if (currentIndex < 0 || currentIndex >= chipIds.length) return { kind: "ignore" };
    return { kind: "activate", chipId: chipIds[currentIndex] };
  }
  return { kind: "ignore" };
}
