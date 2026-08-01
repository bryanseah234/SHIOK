# No-Transit Replay Top Areas - 2026-08-02

Bundle: `generated_20260801_165500`

Raw report: `qa/no_transit_replay_top_areas_20260802.json`

## State Counts

```json
{
  "SCORED": 112880,
  "SCORED_PARTIAL": 1449,
  "NO_TRANSIT_IN_RANGE": 9384,
  "NOT_YET_SCORED": 319
}
```

## Top Remaining No-Transit Areas

- `BUKIT_TIMAH`: 2,005
- `SERANGOON`: 1,980
- `ANG_MO_KIO`: 557
- `SOUTHERN_ISLANDS`: 544
- `SUNGEI_KADUT`: 416
- `HOUGANG`: 397
- `CLEMENTI`: 361
- `TANGLIN`: 316
- `BEDOK`: 288
- `YISHUN`: 247

## Replay Result

Bounded replay sample: 80 postals selected from the pending bundle.

- `all_candidates_beyond_access_range`: 74
- `candidate_graph_disconnected`: 6

Disconnected candidate-component snap distances:

- count: 6
- min: 47.1 m
- p50: 396.1 m
- p95: 5,353.6 m
- max: 5,353.6 m
- within 25 m: 0
- within 50 m: 1
- within 75 m: 1

## Interpretation

The remaining `NO_TRANSIT_IN_RANGE` population is not mainly an obvious direct-bus
fallback miss: the pending bundle has zero direct bus candidates within 300 m for
all 9,384 remaining no-transit records.

In this replay sample, most records are routable to candidate MRT/LRT exits but
the routed walk exceeds the current 1.2 km access cutoff. A smaller group is
still graph-disconnected and needs targeted geometry QA. This points to two
separate next decisions:

- Product policy: whether a routed walk just beyond 1.2 km should remain
  `NO_TRANSIT_IN_RANGE` or become a low/zero-credit scored state.
- Data QA: inspect the disconnected samples before making another broad resnap
  or connector change.
