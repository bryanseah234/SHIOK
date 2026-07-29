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
  routed_m: number;
}

export interface Paths {
  shortest_m: number;
  sheltered_m: number;
  detour_pct: number;
  routing_type?: string;
  covered_m?: number;
  covered_ratio?: number;
  shortest_covered_ratio?: number;
}

export interface ExposureGap {
  len_m: number;
  label: string;
  location?: {
    lat: number;
    lon: number;
  };
}

export interface ScoreRecord {
  postal: string;
  state: ScoreState;
  total: number | null;
  subscores: Subscores | null;
  best_node: BestNode | null;
  paths: Paths | null;
  exposure_gaps: ExposureGap[] | null;
  data_as_of: string | null;
  provenance: string | Record<string, unknown>;
}

export interface GeomGap {
  geom: string;
  len_m: number;
  label: string;
}

export interface PostalGeom {
  postal: string;
  shortest: string;
  sheltered: string;
  exposure_gaps: GeomGap[];
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
  exit_count?: number;
  station?: string;
  exit?: string;
  code?: string;
  road?: string;
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
