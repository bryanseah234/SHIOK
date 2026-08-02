import type { PostalGeom, RouteSegment, ScoreRecord } from "./types";

interface RouteDisplaySelection {
  geom: PostalGeom | null;
  score: Pick<ScoreRecord, "paths"> | null;
}

function segmentSignature(segments: RouteSegment[] | undefined): string {
  return JSON.stringify(
    (segments ?? []).map((segment) => ({
      geom: segment.geom,
      is_covered: segment.is_covered,
      source_class: segment.source_class ?? "",
      source_layer: segment.source_layer ?? "",
      synth_class: segment.synth_class ?? "",
    }))
  );
}

export function routesAreSame(selection: RouteDisplaySelection | null): boolean {
  if (!selection?.geom || !selection.score?.paths) return false;
  if (selection.score.paths.routing_type === "direct_bus_fallback_unrouted") return true;
  if (selection.geom.shortest !== selection.geom.sheltered) return false;

  const shortestSegments = selection.geom.route_segments?.shortest;
  const shelteredSegments = selection.geom.route_segments?.sheltered;
  if (shortestSegments?.length || shelteredSegments?.length) {
    return segmentSignature(shortestSegments) === segmentSignature(shelteredSegments);
  }

  return true;
}
