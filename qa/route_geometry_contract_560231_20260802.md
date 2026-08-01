# Route Geometry Contract Check - 560231 - 2026-08-02

Bundle: `generated_20260801_165500`

Postal index cell: `88652636c1fffff`

## Segment Availability

| Mode | Shortest segments | Shiokest segments | Shortest fallback parts | Shiokest fallback parts |
|---|---:|---:|---:|---:|
| Best transit | 5 | 5 | 4 | 4 |
| MRT/LRT | 6 | 6 | 4 | 4 |

This confirms the pending bundle has the upgraded route-geometry contract for
560231. It exports separate `route_segments.shortest` and
`route_segments.sheltered` for both the default best-transit route and the
MRT/LRT-only route.

## MRT/LRT Shiokest Segment Classes

| Length m | Covered | Source class |
|---:|---:|---|
| 41.7 | true | `inferred_hdb_void_deck` |
| 12.6 | true | `inferred_hdb_void_deck` |
| 34.9 | true | `inferred_hdb_void_deck` |
| 35.4 | true | `inferred_hdb_void_deck` |
| 8.4 | true | `bridge_underpass` |
| 292.9 | false | `exposed` |

## Interpretation

The previous fake-connector/MultiLineString display bug is guarded by exporter
tests and the pending bundle has segment arrays for this postal. The remaining
560231/Mayflower issue is not a missing frontend geometry contract. It is the
underlying data/model still leaving one major exposed segment on the MRT/LRT
route.
