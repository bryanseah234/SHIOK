from multiprocessing import Pool, cpu_count
from pathlib import Path

import geopandas as gpd
import igraph as ig
import pandas as pd
import yaml
from shapely import wkt
from shapely.geometry import MultiLineString
from shapely.ops import linemerge

from pipeline.osm_tags import load_osm_tag_schema

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "pipeline" / "config" / "params.yaml"

_BASE_EDGE_METADATA_COLUMNS = [
    "source_layer",
    "synth_class",
    "confidence",
    "shade_ratio",
    "shade_source",
    "shade_confidence",
    "highway",
]
_OSM_EDGE_METADATA_COLUMNS = list(load_osm_tag_schema().network_extra_attributes)
EDGE_METADATA_COLUMNS = list(
    dict.fromkeys([*_BASE_EDGE_METADATA_COLUMNS, *_OSM_EDGE_METADATA_COLUMNS])
)


def load_params():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def prepare_edges_for_routing(edges_df):
    """Normalize edge geometry, endpoints, and metre lengths for routing."""
    edges_df = edges_df.copy()

    if "geometry" in edges_df.columns:
        edges_df["geometry"] = edges_df["geometry"].apply(
            lambda geom: wkt.loads(geom) if isinstance(geom, str) else geom
        )

        def get_coords(geom):
            if geom is not None and hasattr(geom, "is_empty") and not geom.is_empty:
                coords = geom.coords
                return (
                    (round(coords[0][0], 2), round(coords[0][1], 2)),
                    (round(coords[-1][0], 2), round(coords[-1][1], 2)),
                )
            return None, None

        coords = edges_df["geometry"].apply(get_coords)
        edges_df["u"] = [coord[0] for coord in coords]
        edges_df["v"] = [coord[1] for coord in coords]
        edges_df = edges_df.dropna(subset=["u", "v"])
    else:
        edges_df = edges_df[(edges_df["u"] != -1) & (edges_df["v"] != -1)]

    if "length_m" not in edges_df.columns:
        edges_df["length_m"] = pd.NA

    length_m = pd.to_numeric(edges_df["length_m"], errors="coerce")
    if "length" in edges_df.columns:
        length_m = length_m.fillna(pd.to_numeric(edges_df["length"], errors="coerce"))
    if "geometry" in edges_df.columns:
        geom_lengths = edges_df["geometry"].apply(
            lambda geom: (
                geom.length
                if geom is not None and hasattr(geom, "is_empty") and not geom.is_empty
                else pd.NA
            )
        )
        length_m = length_m.fillna(pd.to_numeric(geom_lengths, errors="coerce"))

    edges_df["length_m"] = length_m.fillna(1.0).clip(lower=0.0)
    edges_df["is_covered"] = edges_df["is_covered"].fillna(0).astype(int)
    return edges_df


def build_graph(edges_df):
    """Build igraph object from edge DataFrame."""
    unique_nodes = pd.concat([edges_df["u"], edges_df["v"]]).unique()
    node_mapping = {n: i for i, n in enumerate(unique_nodes)}
    reverse_mapping = {i: n for n, i in node_mapping.items()}

    edges_mapped = [
        (node_mapping[u], node_mapping[v]) for u, v in zip(edges_df["u"], edges_df["v"])
    ]

    g = ig.Graph(edges=edges_mapped, directed=False)
    g.es["length_m"] = edges_df["length_m"].values
    g.es["is_covered"] = edges_df["is_covered"].values
    if "geometry" in edges_df.columns:
        g.es["geometry"] = edges_df["geometry"].values
    for column in EDGE_METADATA_COLUMNS:
        if column in edges_df.columns:
            g.es[column] = edges_df[column].values

    return g, node_mapping, reverse_mapping


class RoutingGraph:
    """Reusable igraph routing context for repeated same-network route queries."""

    def __init__(self, edges_df):
        self.edges_df = prepare_edges_for_routing(edges_df)
        self.graph, self.node_map, self.reverse_map = build_graph(self.edges_df)
        self.component_membership = self.graph.connected_components(mode="weak").membership
        self.lengths = [float(value) for value in self.graph.es["length_m"]]
        self.covered = [bool(value) for value in self.graph.es["is_covered"]]
        self.shade_ratios = self._shade_ratio_values()
        self.geometries = (
            list(self.graph.es["geometry"]) if "geometry" in self.graph.edge_attributes() else None
        )
        self.edge_metadata = {
            column: list(self.graph.es[column])
            for column in EDGE_METADATA_COLUMNS
            if column in self.graph.edge_attributes()
        }
        self._sheltered_costs_by_lambda = {}

    @classmethod
    def from_edges_dict(cls, edges_dict):
        return cls(pd.DataFrame(edges_dict))

    @classmethod
    def from_prepared_edges(cls, edges_df):
        obj = cls.__new__(cls)
        obj.edges_df = edges_df
        obj.graph, obj.node_map, obj.reverse_map = build_graph(edges_df)
        obj.component_membership = obj.graph.connected_components(mode="weak").membership
        obj.lengths = [float(value) for value in obj.graph.es["length_m"]]
        obj.covered = [bool(value) for value in obj.graph.es["is_covered"]]
        obj.shade_ratios = obj._shade_ratio_values()
        obj.geometries = (
            list(obj.graph.es["geometry"]) if "geometry" in obj.graph.edge_attributes() else None
        )
        obj.edge_metadata = {
            column: list(obj.graph.es[column])
            for column in EDGE_METADATA_COLUMNS
            if column in obj.graph.edge_attributes()
        }
        obj._sheltered_costs_by_lambda = {}
        return obj

    def _shade_ratio_values(self):
        if "shade_ratio" not in self.graph.edge_attributes():
            return [0.0 for _ in self.lengths]
        ratios = []
        for value in self.graph.es["shade_ratio"]:
            numeric = pd.to_numeric(value, errors="coerce")
            ratios.append(0.0 if pd.isna(numeric) else max(0.0, min(1.0, float(numeric))))
        return ratios

    def sheltered_costs(self, shelter_lambda):
        lambda_key = float(shelter_lambda)
        if lambda_key not in self._sheltered_costs_by_lambda:
            self._sheltered_costs_by_lambda[lambda_key] = [
                length * (1.0 + lambda_key * (1.0 - float(covered)))
                for length, covered in zip(self.lengths, self.covered)
            ]
        return self._sheltered_costs_by_lambda[lambda_key]

    def geometry_for_epath(self, epath):
        if self.geometries is None:
            return None
        lines = [
            self.geometries[edge_id] for edge_id in epath if self.geometries[edge_id] is not None
        ]
        if not lines:
            return None
        return linemerge(MultiLineString(lines))

    def path_edges_for_epath(self, epath):
        if self.geometries is None:
            return []
        edges = []
        for edge_id in epath:
            edge = {
                "length_m": float(self.lengths[edge_id]),
                "is_covered": bool(self.covered[edge_id]),
                "geometry": self.geometries[edge_id],
            }
            for column, values in self.edge_metadata.items():
                value = values[edge_id]
                if pd.isna(value):
                    continue
                edge[column] = value
            edges.append(edge)
        return edges

    def route(self, od_pairs, shelter_lambda, detour_budget, include_geometry=True):
        sheltered_weights = (
            self.lengths if float(shelter_lambda) == 0.0 else self.sheltered_costs(shelter_lambda)
        )
        results = []

        for origin, destinations in od_pairs.items():
            if origin not in self.node_map:
                continue
            origin_idx = self.node_map[origin]
            origin_component = self.component_membership[origin_idx]

            valid_destinations = [
                destination
                for destination in destinations
                if destination in self.node_map
                and self.component_membership[self.node_map[destination]] == origin_component
            ]
            dest_indices = [self.node_map[destination] for destination in valid_destinations]

            if not dest_indices:
                continue

            paths_shortest = self.graph.get_shortest_paths(
                origin_idx, to=dest_indices, weights=self.lengths, output="epath"
            )
            paths_sheltered = self.graph.get_shortest_paths(
                origin_idx, to=dest_indices, weights=sheltered_weights, output="epath"
            )

            for dest, epath_short, epath_shelt in zip(
                valid_destinations, paths_shortest, paths_sheltered
            ):
                if not epath_short:
                    continue

                len_short = sum(self.lengths[edge_id] for edge_id in epath_short)

                if not epath_shelt:
                    final_epath = epath_short
                    routing_type = "shortest_fallback"
                else:
                    len_shelt = sum(self.lengths[edge_id] for edge_id in epath_shelt)
                    if len_shelt <= float(detour_budget) * len_short:
                        final_epath = epath_shelt
                        routing_type = "sheltered"
                    else:
                        final_epath = epath_short
                        routing_type = "shortest_due_to_detour"

                final_length = sum(self.lengths[edge_id] for edge_id in final_epath)
                final_covered = sum(
                    self.lengths[edge_id] for edge_id in final_epath if self.covered[edge_id]
                )
                final_shade = sum(
                    self.lengths[edge_id] * self.shade_ratios[edge_id]
                    for edge_id in final_epath
                    if not self.covered[edge_id]
                )
                cov_short = sum(
                    self.lengths[edge_id] for edge_id in epath_short if self.covered[edge_id]
                )
                shade_short = sum(
                    self.lengths[edge_id] * self.shade_ratios[edge_id]
                    for edge_id in epath_short
                    if not self.covered[edge_id]
                )
                sheltered_length = (
                    sum(self.lengths[edge_id] for edge_id in epath_shelt) if epath_shelt else None
                )

                result = {
                    "origin": origin,
                    "destination": dest,
                    "routing_type": routing_type,
                    "length_m": final_length,
                    "covered_m": final_covered,
                    "covered_ratio": final_covered / final_length if final_length > 0 else 0.0,
                    "shade_m": final_shade,
                    "shade_ratio": final_shade / final_length if final_length > 0 else 0.0,
                    "shortest_length_m": len_short,
                    "shortest_covered_ratio": cov_short / len_short if len_short > 0 else 0.0,
                    "shortest_shade_m": shade_short,
                    "shortest_shade_ratio": shade_short / len_short if len_short > 0 else 0.0,
                    "sheltered_length_m": sheltered_length,
                    "geometry": None,
                    "shortest_geometry": None,
                    "shortest_path_edges": [],
                    "sheltered_path_edges": [],
                    "path_edges": [],
                }
                if include_geometry:
                    result["geometry"] = self.geometry_for_epath(final_epath)
                    result["shortest_geometry"] = self.geometry_for_epath(epath_short)
                    result["shortest_path_edges"] = self.path_edges_for_epath(epath_short)
                    result["sheltered_path_edges"] = self.path_edges_for_epath(final_epath)
                    result["path_edges"] = self.path_edges_for_epath(final_epath)
                results.append(result)

        return results


def route_worker(args):
    """Worker function for multiprocessing."""
    edges_dict, od_pairs, shelter_lambda, detour_budget = args
    return RoutingGraph.from_edges_dict(edges_dict).route(
        od_pairs,
        shelter_lambda,
        detour_budget,
        include_geometry=True,
    )


def run_routing_batch(network_path, od_pairs):
    params = load_params()
    shelter_lambda = params["shelter_lambda"]
    detour_budget = params["detour_budget"]

    print(f"Loading network from {network_path}...")
    edges_df = pd.read_parquet(network_path)
    edges_df = prepare_edges_for_routing(edges_df)

    cols = ["u", "v", "length_m", "is_covered"]
    if "geometry" in edges_df.columns:
        cols.append("geometry")
    cols.extend(column for column in EDGE_METADATA_COLUMNS if column in edges_df.columns)
    edges_dict = edges_df[cols].to_dict("list")

    origins = list(od_pairs.keys())
    num_workers = min(cpu_count(), 8)
    chunk_size = max(1, len(origins) // num_workers)

    origin_chunks = [origins[i : i + chunk_size] for i in range(0, len(origins), chunk_size)]

    worker_args = []
    for chunk in origin_chunks:
        chunk_od_pairs = {o: od_pairs[o] for o in chunk}
        worker_args.append((edges_dict, chunk_od_pairs, shelter_lambda, detour_budget))

    print(f"Starting routing on {len(origins)} origins across {len(worker_args)} workers...")

    results = []
    with Pool(num_workers) as pool:
        for res_chunk in pool.imap_unordered(route_worker, worker_args):
            results.extend(res_chunk)

    df = pd.DataFrame(results)

    # Ensure geometries are maintained
    if "geometry" in df.columns:
        gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:3414")
        return gdf
    return df


if __name__ == "__main__":
    pass
