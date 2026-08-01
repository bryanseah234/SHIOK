# No-Transit Breakdown - 2026-08-02

Bundle: `generated_20260801_165500`

State counts:

```json
{
  "SCORED": 112880,
  "SCORED_PARTIAL": 1449,
  "NO_TRANSIT_IN_RANGE": 9384,
  "NOT_YET_SCORED": 319
}
```

Remaining `NO_TRANSIT_IN_RANGE`: 9384

- Direct bus candidates within 300 m: 0
- Zero direct bus candidates within 300 m: 9384
- Nearest routed transit p50/p90/p95/max: 1966.7 m / 3297 m / 4675.9 m / 13776.3 m
- Origin snap p95/max: 114.6 m / 7961.7 m

Top areas by remaining no-transit count are in `qa/no_transit_breakdown_20260802.json`.

Interpretation: the direct-bus fallback is not leaving obvious 300 m bus candidates behind in this full bundle. The next QA should focus on access threshold/product policy and sampled graph gaps in top areas, not another broad bus fallback patch.
