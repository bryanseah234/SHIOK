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
    "edge_count": 74,
    "covered_edge_count": 5,
    "edge_len_m": 1218.3,
    "covered_len_m": 87.0
  },
  {
    "threshold_m": 10,
    "edge_count": 121,
    "covered_edge_count": 5,
    "edge_len_m": 2026.4,
    "covered_len_m": 87.0
  },
  {
    "threshold_m": 20,
    "edge_count": 209,
    "covered_edge_count": 21,
    "edge_len_m": 3342.3,
    "covered_len_m": 264.9
  },
  {
    "threshold_m": 50,
    "edge_count": 399,
    "covered_edge_count": 23,
    "edge_len_m": 5895.0,
    "covered_len_m": 279.0
  },
  {
    "threshold_m": 100,
    "edge_count": 618,
    "covered_edge_count": 31,
    "edge_len_m": 8833.3,
    "covered_len_m": 390.3
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
  "lambda_sweep": [
    {
      "lambda": 0,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 834.5,
      "shortest_m": 834.5,
      "extra_walk_m": 0.0,
      "covered_m": 25.6,
      "covered_ratio_pct": 3.1,
      "within_25pct_detour": true
    },
    {
      "lambda": 0.6,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 834.5,
      "shortest_m": 834.5,
      "extra_walk_m": 0.0,
      "covered_m": 25.6,
      "covered_ratio_pct": 3.1,
      "within_25pct_detour": true
    },
    {
      "lambda": 1.5,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 889.4,
      "shortest_m": 834.5,
      "extra_walk_m": 54.9,
      "covered_m": 123.5,
      "covered_ratio_pct": 13.9,
      "within_25pct_detour": true
    },
    {
      "lambda": 3,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 889.4,
      "shortest_m": 834.5,
      "extra_walk_m": 54.9,
      "covered_m": 123.5,
      "covered_ratio_pct": 13.9,
      "within_25pct_detour": true
    },
    {
      "lambda": 6,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 889.4,
      "shortest_m": 834.5,
      "extra_walk_m": 54.9,
      "covered_m": 123.5,
      "covered_ratio_pct": 13.9,
      "within_25pct_detour": true
    },
    {
      "lambda": 12,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 889.4,
      "shortest_m": 834.5,
      "extra_walk_m": 54.9,
      "covered_m": 123.5,
      "covered_ratio_pct": 13.9,
      "within_25pct_detour": true
    },
    {
      "lambda": 30,
      "candidate": "MAYFLOWER MRT STATION Exit 5",
      "length_m": 889.4,
      "shortest_m": 834.5,
      "extra_walk_m": 54.9,
      "covered_m": 123.5,
      "covered_ratio_pct": 13.9,
      "within_25pct_detour": true
    }
  ],
  "nearest_covered_to_origin_m": 55.2,
  "nearest_covered_to_best_exit_m": 38.7,
  "covered_edges_within_30m_of_route": 23,
  "covered_len_within_30m_of_route": 279.0
}
```

## Initial Classification

- The shipped score is not a frontend display bug: the score artifact itself reports only 25.6 m covered.
- Covered graph edges do exist near the route corridor: within 20 m there are 21 covered edges totalling about 264.9 m.
- The current shelter lambda is also too weak for this case: lambda 0.6 leaves the sheltered route identical to shortest, while lambda 1.5+ finds a valid +55 m route within the 25% detour cap and lifts covered ratio from 3.1% to 13.9%.
- Lambda tuning alone does not solve the owner-verified ground truth: even lambda 30 only reaches 13.9% covered, and the nearest covered graph edge is 55.2 m from the postal origin and 38.7 m from Exit 5.
- Root-cause classification: mixed algorithm/data issue. Raise lambda only after a broader safety sweep, and separately investigate missing/disconnected/untagged HDB void-deck, overpass, and final-MRT-approach shelter geometry. Do not hardcode a postal-specific score override.

## Files

- `qa/560234_shelter_audit.geojson`

Open the GeoJSON in geojson.io or QGIS and inspect the route corridor around Mayflower MRT / Postal 560234. The next manual step is to draw the actual sheltered overpass / HDB cut-through path as an audited correction if it is missing or disconnected.
