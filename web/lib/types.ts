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
  route_options?: Partial<Record<TransitAccessMode, RouteOption>>;
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
