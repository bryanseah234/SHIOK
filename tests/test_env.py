"""Environment lockdown tests for T0.2."""

import duckdb
import geopandas as gpd
import h3
import httpx
import igraph as ig
import pyproj
import shapely


def test_imports() -> None:
    """Verify all core stack libraries import without error."""
    assert gpd.__version__ is not None
    assert ig.__version__ is not None
    assert duckdb.__version__ is not None
    assert h3.__file__ is not None
    assert shapely.__version__ is not None
    assert pyproj.__version__ is not None
    assert httpx.__version__ is not None


def test_igraph_shortest_path_smoke() -> None:
    """Deterministic 100-edge igraph smoke test asserting exact distance and path."""
    # Create a directed graph with 100 vertices
    g = ig.Graph(directed=True)
    g.add_vertices(100)

    # Add 100 ring edges: 0->1, 1->2, ..., 98->99, 99->0 with weight 1.0
    ring_edges = [(i, (i + 1) % 100) for i in range(100)]
    g.add_edges(ring_edges)
    weights = [1.0] * 100

    # Add a shortcut chord edge from 0 to 50 with weight 2.0
    g.add_edge(0, 50)
    weights.append(2.0)
    g.es["weight"] = weights

    assert g.ecount() == 101

    # Calculate shortest path from node 0 to node 50 using edge weights
    shortest_paths = g.get_shortest_paths(0, to=50, weights="weight", output="vpath")
    path_nodes = shortest_paths[0]

    # Calculate distance along path
    path_edges = g.get_shortest_paths(0, to=50, weights="weight", output="epath")[0]
    total_distance = sum(g.es[e]["weight"] for e in path_edges)

    assert path_nodes == [0, 50]
    assert total_distance == 2.0
