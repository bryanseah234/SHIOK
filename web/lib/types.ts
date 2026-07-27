export type ScoreState = "SCORED" | "SCORED_PARTIAL" | "NOT_YET_SCORED" | "NO_TRANSIT_IN_RANGE";

export interface Subscores {
  access: number;
  bus: number;
  rain: number;
  heat: number;
  crossing: number;
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
}
