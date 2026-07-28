# 560234 Shelter Audit

Generated: 2026-07-28

## Shipped Score Record

- Postal: 560234
- State: SCORED
- Total: 14.0/100
- Best node: MAYFLOWER MRT STATION Exit 5
- Shiokest distance: 834.5 m
- Shortest distance: 834.5 m
- Covered length: 25.6 m
- Covered ratio: 3.1%
- Shortest covered ratio: 3.1%

## Corridor Evidence

Current graph coverage near the shipped Shiokest route:

```json
[
  {
    "threshold_m": 5,
    "edge_count": 75,
    "covered_edge_count": 8,
    "edge_len_m": 1284.4,
    "covered_len_m": 165.7
  },
  {
    "threshold_m": 10,
    "edge_count": 122,
    "covered_edge_count": 9,
    "edge_len_m": 2092.6,
    "covered_len_m": 170.1
  },
  {
    "threshold_m": 20,
    "edge_count": 210,
    "covered_edge_count": 26,
    "edge_len_m": 3408.5,
    "covered_len_m": 355.3
  },
  {
    "threshold_m": 50,
    "edge_count": 408,
    "covered_edge_count": 38,
    "edge_len_m": 6476.7,
    "covered_len_m": 954.8
  },
  {
    "threshold_m": 100,
    "edge_count": 631,
    "covered_edge_count": 52,
    "edge_len_m": 9445.2,
    "covered_len_m": 1117.6
  }
]
```

## Candidate / Lambda Diagnostics

```json
{
  "origin_xy": [
    28370.99,
    38800.657
  ],
  "origin_snap_m": 13.5,
  "candidate_count": 7,
  "mayflower_candidates": [
    {
      "name": "MAYFLOWER MRT STATION Exit 1",
      "straight_line_m": 611.5,
      "snap_distance_m": 9.7
    },
    {
      "name": "MAYFLOWER MRT STATION Exit 2",
      "straight_line_m": 725.0,
      "snap_distance_m": 2.4
    },
    {
      "name": "MAYFLOWER MRT STATION Exit 3",
      "straight_line_m": 708.8,
      "snap_distance_m": 6.3
    },
    {
      "name": "MAYFLOWER MRT STATION Exit 4",
      "straight_line_m": 453.9,
      "snap_distance_m": 11.6
    },
    {
      "name": "MAYFLOWER MRT STATION Exit 5",
      "straight_line_m": 398.6,
      "snap_distance_m": 11.9
    },
    {
      "name": "MAYFLOWER MRT STATION Exit 6",
      "straight_line_m": 413.4,
      "snap_distance_m": 13.3
    },
    {
      "name": "MAYFLOWER MRT STATION Exit 7",
      "straight_line_m": 466.6,
      "snap_distance_m": 2.3
    }
  ],
  "lambda_sweep_destination": "MAYFLOWER MRT STATION Exit 5",
  "lambda_sweep": [
    {
      "lambda": 0,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 834.5,
      "shortest_m": 834.5,
      "extra_walk_m": 0.0,
      "covered_m": 38.2,
      "covered_ratio_pct": 4.6,
      "within_25pct_detour": true
    },
    {
      "lambda": 0.6,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 834.5,
      "shortest_m": 834.5,
      "extra_walk_m": 0.0,
      "covered_m": 38.2,
      "covered_ratio_pct": 4.6,
      "within_25pct_detour": true
    },
    {
      "lambda": 1.5,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 889.4,
      "shortest_m": 834.5,
      "extra_walk_m": 54.9,
      "covered_m": 136.1,
      "covered_ratio_pct": 15.3,
      "within_25pct_detour": true
    },
    {
      "lambda": 3,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 889.4,
      "shortest_m": 834.5,
      "extra_walk_m": 54.9,
      "covered_m": 136.1,
      "covered_ratio_pct": 15.3,
      "within_25pct_detour": true
    },
    {
      "lambda": 6,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 889.4,
      "shortest_m": 834.5,
      "extra_walk_m": 54.9,
      "covered_m": 136.1,
      "covered_ratio_pct": 15.3,
      "within_25pct_detour": true
    },
    {
      "lambda": 12,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 889.4,
      "shortest_m": 834.5,
      "extra_walk_m": 54.9,
      "covered_m": 136.1,
      "covered_ratio_pct": 15.3,
      "within_25pct_detour": true
    },
    {
      "lambda": 30,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 889.4,
      "shortest_m": 834.5,
      "extra_walk_m": 54.9,
      "covered_m": 136.1,
      "covered_ratio_pct": 15.3,
      "within_25pct_detour": true
    }
  ],
  "nearest_covered_to_origin_m": 9.8,
  "nearest_covered_to_best_exit_m": 38.7,
  "covered_edges_within_30m_of_route": 32,
  "covered_len_within_30m_of_route": 696.8
}
```

## Initial Classification

- The shipped score is not a frontend display bug: the score artifact itself reports only 25.6 m covered.
- Covered graph edges do exist near the route corridor: within 20 m there are 26 covered edges totalling about 355.3 m.
- The original shelter lambda was too weak for this case: lambda 0.6 leaves the sheltered route identical to shortest, while lambda 1.5+ finds a valid +55 m route within the 25% detour cap.
- After rebuilding the island network with overhead/underpass polygons, OSM roof/canopy attribution, and inferred HDB void-deck connectors, the Exit 5 route improves from the shipped 3.1% covered to 15.3% covered at lambda 2.0.
- Lambda tuning and inferred public-housing shelter still do not solve the owner-verified ground truth: even lambda 30 reaches only 15.3% covered, and the nearest covered graph edge is 9.8 m from the postal origin and 38.7 m from Exit 5.
- Root-cause classification: mixed algorithm/data issue. The general model is improved, but the known fully sheltered HDB/overpass path still lacks routable covered geometry in available sources. Do not hardcode a postal-specific score override.

## Implemented General Fixes

- `shelter_lambda` raised from 0.6 to 2.0, still bounded by the PRD 25% detour cap.
- LTA overhead bridge / underpass polygons are now included in shelter conflation, not only crossing-friction exemption.
- OSM `building=roof|canopy` polygons now mark intersecting pedestrian edges as covered.
- HDB void decks are inferred only from HDB building points matched to OSM residential footprints, then emitted as short covered pass-through connectors with strict geometric caps.

The rebuilt island QA is green: zero real disconnections, no flags, 5,422 inferred HDB void-deck connectors, 3,631 roof/canopy-attributed edges, and 923 overhead/underpass polygons included in the shelter layer.

## Files

- `qa/560234_shelter_audit.geojson`

Open the GeoJSON in geojson.io or QGIS and inspect the route corridor around Mayflower MRT / Postal 560234. The next manual step is to draw the actual sheltered overpass / HDB cut-through path as an audited correction if it is missing or disconnected.
