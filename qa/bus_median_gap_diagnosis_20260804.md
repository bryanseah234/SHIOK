Bus Median Gap Diagnosis - 2026-08-04

INPUT: qa/onemap_validation_full_results_honesty50_20260803.json (1999 postals, all bus_stop + mrt_lrt_exit)

BUS_STOP SUB-POPULATION (n=1611):
  Signed direction split:
    project_shorter_than_onemap: 833 (52%) - over-permissive tail
    project_longer_than_onemap:  778 (48%) - under-permissive tail
  Signed pct delta distribution (buckets):
    lt_-50 (project MUCH shorter):    108
    lt_-25:                            142
    lt_-10:                            234
    lt_0:                              349
    0_to_10:                           326
    10_to_25:                          178
    25_to_50:                          100
    50_to_100:                          74
    >=100 (project MUCH longer):       100

DISTANCE SANITY (bus_stop):
  plausible:                                 1563  (both walks >= direct line)
  onemap_slightly_shorter_than_direct:         31  (OneMap answer < crow-flies)
  onemap_materially_shorter_than_direct:       17  (OneMap answer materially < crow-flies)
  Total OneMap-impossible cases:                48  (3.0%)
  Project-impossible cases (project<direct):    80  (5.0%)

GATE UNDER DIFFERENT FILTERS:
  All bus_stop (n=1611):                        median 12.893  p95 112.692  FAIL
  Excluding OneMap-impossible (n=1563):         median 12.444  p95  95.806  FAIL
  Also excluding project-snap-bug (n=1490):     median 11.786  p95  98.451  FAIL

ROOT CAUSE CATEGORIES:
  1. Project snap bug (~80 postals): project_shortest_m < direct_distance_m.
     Common cause: endpoint connector snapping origin+destination to same/near nodes.
     Current code catches this via "implausible_graph_route_to_datamall_bus_stop_within_direct_radius"
     fallback reason - 116 of top-200 replayed postals hit this fallback.
  2. OneMap-impossible (~48 postals): OneMap returns walk < direct distance.
     OneMap data quality issue, not fixable in this project.
  3. Systemic OSM-vs-OneMap graph diff (~1483 postals): median ~11-12% is the noise floor
     of comparing two independently-built pedestrian graphs. Not a fixable bug.

WIDER REPLAY OUTCOME (top 200 over-permissive bus_stop outliers):
  Replay: qa/onemap_outlier_replay_bus_shorter_profile_current_200_20260804.json
  141/200 = 70.5% downgrade to direct-bus fallback under current code
  76/200 get direct-bus fallback as NEW best transit
  Triage: qa/onemap_outlier_triage_bus_shorter_200_summary_20260804.json
  Compare-targeted (208 union w/ prior 8): blocking_count=103, safe_promotable=55
  103 blocking cases have score-drops beyond compare tolerance - correcting them
  requires manual review/policy override, not batch promotion.

RELEASE CANDIDATE (this run):
  Bundle: generated_20260804_safe_bus_route_honesty55_targeted (55 postals)
  Gate against 2000-postal cache:
    median: 11.582 -> 11.185 (-0.397pp, still over 10.0 max)
    p95:    103.196 -> 103.203 (+0.007pp, still over 25.0 max)
  Strictly better than honesty50 on median; p95 unchanged (safe filter blocks the worst
  offenders because their corrections would exceed score-drop tolerance).

CONCLUSION:
  OneMap walk-validation gate cannot be closed by the safe-correction-only path alone.
  Path 1 (safe promotion): honesty55 is the maximum safely promotable set today.
  Path 2 (audited hand-review): manually vet the 103 blocking cases (e.g., 489929)
  and allow policy-overridden promotions for cases where the OneMap answer is
  clearly right and the score drop is acceptable.
  Path 3 (accept structural noise): recognize that OSM/OneMap graph difference
  produces a ~11% median floor; adjust thresholds to median_max=15, p95_max=50
  and separately require ""distance_sanity=plausible"" gate to catch bugs.
