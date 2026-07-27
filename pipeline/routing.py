import pandas as pd
import igraph as ig
import yaml
from pathlib import Path
from multiprocessing import Pool, cpu_count
import geopandas as gpd
from shapely import wkt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "pipeline" / "config" / "params.yaml"


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

    return g, node_mapping, reverse_mapping


def route_worker(args):
    """Worker function for multiprocessing."""
    edges_dict, od_pairs, shelter_lambda, detour_budget = args
    edges_df = prepare_edges_for_routing(pd.DataFrame(edges_dict))

    g, node_map, rev_map = build_graph(edges_df)
    component_membership = g.connected_components(mode="weak").membership

    g.es["sheltered_cost"] = [
        L * (1.0 + shelter_lambda * (1.0 - c)) for L, c in zip(g.es["length_m"], g.es["is_covered"])
    ]

    results = []

    for origin, destinations in od_pairs.items():
        if origin not in node_map:
            continue
        origin_idx = node_map[origin]
        origin_component = component_membership[origin_idx]

        valid_destinations = [
            d
            for d in destinations
            if d in node_map and component_membership[node_map[d]] == origin_component
        ]
        dest_indices = [node_map[d] for d in valid_destinations]

        if not dest_indices:
            continue

        paths_shortest = g.get_shortest_paths(
            origin_idx, to=dest_indices, weights="length_m", output="epath"
        )
        paths_sheltered = g.get_shortest_paths(
            origin_idx, to=dest_indices, weights="sheltered_cost", output="epath"
        )

        for dest, epath_short, epath_shelt in zip(
            valid_destinations, paths_shortest, paths_sheltered
        ):
            if not epath_short:
                continue

            len_short = sum(g.es[e]["length_m"] for e in epath_short)

            if not epath_shelt:
                final_epath = epath_short
                routing_type = "shortest_fallback"
            else:
                len_shelt = sum(g.es[e]["length_m"] for e in epath_shelt)
                if len_shelt <= detour_budget * len_short:
                    final_epath = epath_shelt
                    routing_type = "sheltered"
                else:
                    final_epath = epath_short
                    routing_type = "shortest_due_to_detour"

            final_length = sum(g.es[e]["length_m"] for e in final_epath)
            final_covered = sum(g.es[e]["length_m"] for e in final_epath if g.es[e]["is_covered"])
            cov_short = sum(g.es[e]["length_m"] for e in epath_short if g.es[e]["is_covered"])
            sheltered_length = (
                sum(g.es[e]["length_m"] for e in epath_shelt) if epath_shelt else None
            )

            # Reconstruct geometry if available
            path_geom = None
            if "geometry" in g.edge_attributes():
                lines = [
                    g.es[e]["geometry"] for e in final_epath if g.es[e]["geometry"] is not None
                ]
                if lines:
                    from shapely.ops import linemerge
                    from shapely.geometry import MultiLineString

                    merged = linemerge(MultiLineString(lines))
                    path_geom = merged

            path_edges = []
            if "geometry" in g.edge_attributes():
                for edge_id in final_epath:
                    path_edges.append(
                        {
                            "length_m": float(g.es[edge_id]["length_m"]),
                            "is_covered": bool(g.es[edge_id]["is_covered"]),
                            "geometry": g.es[edge_id]["geometry"],
                        }
                    )

            results.append(
                {
                    "origin": origin,
                    "destination": dest,
                    "routing_type": routing_type,
                    "length_m": final_length,
                    "covered_m": final_covered,
                    "covered_ratio": final_covered / final_length if final_length > 0 else 0.0,
                    "shortest_length_m": len_short,
                    "shortest_covered_ratio": cov_short / len_short if len_short > 0 else 0.0,
                    "sheltered_length_m": sheltered_length,
                    "geometry": path_geom,
                    "path_edges": path_edges,
                }
            )

    return results


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
