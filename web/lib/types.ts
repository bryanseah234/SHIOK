export type ScoreState = "SCORED" | "SCORED_PARTIAL" | "NOT_YET_SCORED" | "NO_TRANSIT_IN_RANGE";

export interface Subscores {
  access: number | null;
  bus: number | null;
  rain: number | null;
  heat: number | null;
  crossing: number | null;
}

export interface BestNode {
  type: string;
  name: string;
  routed_m: number | null;
  station?: string;
  exit?: string;
  straight_line_m?: number;
  snap_distance_m?: number;
}

export interface Paths {
  shortest_m: number;
  sheltered_m: number;
  detour_pct: number;
  routing_type?: string;
  covered_m?: number;
  covered_ratio?: number;
  shade_m?: number;
  shade_ratio?: number;
  heat_comfort_m?: number;
  heat_comfort_ratio?: number;
  shortest_covered_ratio?: number;
  shortest_shade_ratio?: number;
  origin_snap_connector_m?: number;
  destination_snap_connector_m?: number;
  endpoint_snap_connector_m?: number;
}

export interface ExposureGap {
  len_m: number;
  label: string;
  location?: {
    lat: number;
    lon: number;
  };
}

/**
 * Compact per-route summary carried on each ranked transit candidate.
 * All fields are optional because a candidate emitted via the direct-bus
 * fallback path has no routed geometry — only a straight-line direct
 * distance and a bus wait — so `shortest_m`/`sheltered_m`/`covered_ratio`
 * can legitimately be null.
 */
export interface CandidatePaths {
  shortest_m: number | null;
  sheltered_m: number | null;
  covered_ratio: number | null;
  detour_pct: number | null;
  shade_ratio: number | null;
}

/**
 * One entry in `ScoreRecord.candidates` — a routed transit stop that the
 * point-to-point picker can promote to the "active" pick and recompute the
 * score card + map line against.
 *
 * `geometry_ref` is a `"<postal>_<node_id>"` lookup key into the geom
 * shard's per-candidate map (see `PostalGeom.candidates`). It is null when
 * the pipeline could not retain per-candidate geometry (e.g. the candidate
 * came from the direct-bus straight-line fallback).
 */
export interface TransitCandidate {
  /** Stable POI id (e.g. "bus:66361", "mrt:21491"). */
  node_id: string;
  node_name: string;
  node_type: string;
  direct_distance_m: number | null;
  paths: CandidatePaths;
  geometry_ref: string | null;
  route_trust: string;
  routing_type: string | null;
  state: ScoreState;
}

export interface ScoreRecord {
  postal: string;
  state: ScoreState;
  total: number | null;
  subscores: Subscores | null;
  best_node: BestNode | null;
  paths: Paths | null;
  exposure_gaps: ExposureGap[] | null;
  route_options?: Partial<Record<TransitAccessMode, RouteOption>>;
  /**
   * Optional list of the top-N nearest ranked transit candidates (sorted by
   * `direct_distance_m` ascending; capped at 5). Emitted only for scored
   * records that have at least one candidate with a stable POI id. Score
   * records generated before this field was added omit it entirely; readers
   * must default to an empty list.
   */
  candidates?: TransitCandidate[];
  data_as_of: string | null;
  provenance: string | Record<string, unknown>;
}

export type TransitAccessMode = "best_transit" | "mrt_lrt" | "bus";

export interface RouteOption {
  state: ScoreState;
  total: number | null;
  subscores: Subscores | null;
  best_node: BestNode | null;
  paths: Paths | null;
  exposure_gaps: ExposureGap[] | null;
}

export interface GeomGap {
  geom: string;
  len_m: number;
  label: string;
}

export interface RouteSegment {
  geom: string;
  len_m: number;
  is_covered: boolean;
  source_class?: string;
  source_layer?: string;
  synth_class?: string;
  confidence?: string;
  source_summary?: string;
}

export interface PostalGeom {
  postal: string;
  shortest: string;
  sheltered: string;
  shortest_parts?: string[];
  sheltered_parts?: string[];
  exposure_gaps: GeomGap[];
  route_segments?: {
    shortest?: RouteSegment[];
    sheltered?: RouteSegment[];
  };
  route_options?: Partial<Record<TransitAccessMode, PostalRouteGeomOption>>;
  /**
   * Per-candidate route geometry keyed by `TransitCandidate.node_id`. The
   * key here matches the `node_id` suffix of `TransitCandidate.geometry_ref`
   * (which is `"<postal>_<node_id>"`). Absent when the postal has no
   * scored non-best candidates retained.
   */
  candidates?: Record<string, PostalRouteGeomOption>;
}

export interface PostalRouteGeomOption {
  shortest: string;
  sheltered: string;
  shortest_parts?: string[];
  sheltered_parts?: string[];
  exposure_gaps: GeomGap[];
  route_segments?: {
    shortest?: RouteSegment[];
    sheltered?: RouteSegment[];
  };
}

export interface Manifest {
  generated_at: string;
  data_as_of: string | null;
  provenance: string | Record<string, unknown>;
  scores?: Record<string, unknown>;
  geom?: Record<string, unknown>;
  transit?: Record<string, unknown>;
}

export interface TransitPoiProperties {
  id: string;
  kind: "mrt_station" | "mrt_exit" | "bus_stop";
  name: string;
  label?: string;
  system?: string;
  lines?: string;
  station_codes?: string;
  exit_count?: number;
  station?: string;
  exit?: string;
  code?: string;
  road?: string;
  service_count?: number;
  services?: string;
  operators?: string;
  weekday_first_bus?: string;
  weekday_last_bus?: string;
  saturday_first_bus?: string;
  saturday_last_bus?: string;
  sunday_first_bus?: string;
  sunday_last_bus?: string;
  am_peak_best_min?: number;
  pm_peak_best_min?: number;
}

export interface TransitPoiFeature {
  type: "Feature";
  geometry: {
    type: "Point";
    coordinates: [number, number];
  };
  properties: TransitPoiProperties;
}

export interface TransitPoiCollection {
  type: "FeatureCollection";
  features: TransitPoiFeature[];
  provenance?: Record<string, unknown>;
}
