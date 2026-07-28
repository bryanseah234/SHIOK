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

## Initial Classification

- The shipped score is not a frontend display bug: the score artifact itself reports only 25.6 m covered.
- Covered graph edges do exist near the route corridor: within 20 m there are 21 covered edges totalling about 264.9 m.
- This points to a data/topology issue rather than a scoring-formula issue: the known sheltered walk is likely missing, disconnected, snapped to an uncovered parallel path, or represented as HDB/indoor/void-deck geometry that public OSM/LTA layers do not expose as a routable connected corridor.

## Files

- `qa/560234_shelter_audit.geojson`

Open the GeoJSON in geojson.io or QGIS and inspect the route corridor around Mayflower MRT / Postal 560234. The next manual step is to draw the actual sheltered overpass / HDB cut-through path as an audited correction if it is missing or disconnected.
