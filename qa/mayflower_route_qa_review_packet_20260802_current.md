# Mayflower Route QA Summary

Bundle: `generated_20260801_165500`

## Route Scores
- `560231`: state `SCORED`, total `100.0`
  - best_transit: `SCORED`, total `100.0`, node `Opp Mayflower Sec Sch`, distance `128.1`, covered `1.0`
  - mrt_lrt: `SCORED`, total `72.1`, node `MAYFLOWER MRT STATION Exit 5`, distance `425.9`, covered `0.312`
  - bus: `SCORED`, total `100.0`, node `Opp Mayflower Sec Sch`, distance `128.1`, covered `1.0`
- `560234`: state `SCORED`, total `90.4`
  - best_transit: `SCORED`, total `90.4`, node `Mayflower Sec Sch`, distance `326.2`, covered `0.761`
  - mrt_lrt: `SCORED`, total `72.6`, node `MAYFLOWER MRT STATION Exit 5`, distance `632.9`, covered `0.523`
  - bus: `SCORED`, total `90.4`, node `Mayflower Sec Sch`, distance `326.2`, covered `0.761`
- `560225`: state `SCORED`, total `85.4`
  - best_transit: `SCORED`, total `85.4`, node `Mayflower Sec Sch`, distance `427.4`, covered `0.7`
  - mrt_lrt: `SCORED`, total `59.7`, node `MAYFLOWER MRT STATION Exit 5`, distance `646.9`, covered `0.363`
  - bus: `SCORED`, total `85.4`, node `Mayflower Sec Sch`, distance `427.4`, covered `0.7`

## Feedback Segment Classes
- `560225`: `{'hdb_void_deck_component_gap': 5, 'covered_evidence_nearby_check_connectivity_or_snap': 6, 'hdb_void_deck_evidence_nearby_check_connectivity': 2}`
- `560231`: `{'covered_evidence_nearby_check_connectivity_or_snap': 5, 'hdb_void_deck_component_gap': 3}`
- `560234`: `{'hdb_void_deck_component_gap': 7, 'covered_evidence_nearby_check_connectivity_or_snap': 4, 'hdb_void_deck_evidence_nearby_check_connectivity': 1}`

## Connector Candidates
- candidate count: `15`
- promotion statuses: `{'review_ready_not_scoring': 9, 'blocked_insufficient_source_overlap_not_scoring': 6}`
- classifications: `{'hdb_source_overlap_review': 5, 'insufficient_source_overlap': 6, 'short_partial_hdb_overlap_review': 1, 'covered_source_overlap_review': 3}`

## Candidate Details
- `560225`
  - `feedback-560225-segment-0-insufficient-source-overlap`: segment `0`, label `void_deck`, length `64` m, status `blocked_insufficient_source_overlap_not_scoring`, class `insufficient_source_overlap`, covered overlap `0.309`, HDB overlap `0.309`, OSM shelter overlap `0`, official shelter overlap `0`
  - `feedback-560225-segment-3-hdb-source-overlap-review`: segment `3`, label `sheltered`, length `17.8` m, status `review_ready_not_scoring`, class `hdb_source_overlap_review`, covered overlap `1`, HDB overlap `1`, OSM shelter overlap `0.45`, official shelter overlap `0`
  - `feedback-560225-segment-7-hdb-source-overlap-review`: segment `7`, label `sheltered`, length `16.5` m, status `review_ready_not_scoring`, class `hdb_source_overlap_review`, covered overlap `1`, HDB overlap `1`, OSM shelter overlap `1`, official shelter overlap `0`
  - `feedback-560225-segment-8-covered-source-overlap-review`: segment `8`, label `covered_bridge`, length `37` m, status `review_ready_not_scoring`, class `covered_source_overlap_review`, covered overlap `1`, HDB overlap `0.216`, OSM shelter overlap `1`, official shelter overlap `0`
  - `feedback-560225-segment-11-insufficient-source-overlap`: segment `11`, label `void_deck`, length `129` m, status `blocked_insufficient_source_overlap_not_scoring`, class `insufficient_source_overlap`, covered overlap `0.105`, HDB overlap `0.105`, OSM shelter overlap `0`, official shelter overlap `0`
- `560231`
  - `feedback-560231-segment-1-hdb-source-overlap-review`: segment `1`, label `void_deck`, length `92.9` m, status `review_ready_not_scoring`, class `hdb_source_overlap_review`, covered overlap `1`, HDB overlap `1`, OSM shelter overlap `1`, official shelter overlap `0`
  - `feedback-560231-segment-3-covered-source-overlap-review`: segment `3`, label `covered_bridge`, length `34.2` m, status `review_ready_not_scoring`, class `covered_source_overlap_review`, covered overlap `1`, HDB overlap `0.016`, OSM shelter overlap `1`, official shelter overlap `0`
  - `feedback-560231-segment-6-insufficient-source-overlap`: segment `6`, label `void_deck`, length `129` m, status `blocked_insufficient_source_overlap_not_scoring`, class `insufficient_source_overlap`, covered overlap `0.105`, HDB overlap `0.105`, OSM shelter overlap `0`, official shelter overlap `0`
- `560234`
  - `feedback-560234-segment-0-hdb-source-overlap-review`: segment `0`, label `void_deck`, length `52` m, status `review_ready_not_scoring`, class `hdb_source_overlap_review`, covered overlap `1`, HDB overlap `1`, OSM shelter overlap `0.015`, official shelter overlap `0`
  - `feedback-560234-segment-1-insufficient-source-overlap`: segment `1`, label `sheltered`, length `68.3` m, status `blocked_insufficient_source_overlap_not_scoring`, class `insufficient_source_overlap`, covered overlap `0.469`, HDB overlap `0.234`, OSM shelter overlap `0.351`, official shelter overlap `0`
  - `feedback-560234-segment-3-insufficient-source-overlap`: segment `3`, label `sheltered`, length `41.1` m, status `blocked_insufficient_source_overlap_not_scoring`, class `insufficient_source_overlap`, covered overlap `0.395`, HDB overlap `0.395`, OSM shelter overlap `0`, official shelter overlap `0`
  - `feedback-560234-segment-4-short-partial-hdb-overlap-review`: segment `4`, label `sheltered`, length `40` m, status `review_ready_not_scoring`, class `short_partial_hdb_overlap_review`, covered overlap `0.463`, HDB overlap `0.463`, OSM shelter overlap `0.221`, official shelter overlap `0`
  - `feedback-560234-segment-6-hdb-source-overlap-review`: segment `6`, label `sheltered`, length `22` m, status `review_ready_not_scoring`, class `hdb_source_overlap_review`, covered overlap `1`, HDB overlap `1`, OSM shelter overlap `1`, official shelter overlap `0`
  - `feedback-560234-segment-7-covered-source-overlap-review`: segment `7`, label `covered_bridge`, length `34.2` m, status `review_ready_not_scoring`, class `covered_source_overlap_review`, covered overlap `1`, HDB overlap `0.016`, OSM shelter overlap `1`, official shelter overlap `0`
  - `feedback-560234-segment-10-insufficient-source-overlap`: segment `10`, label `void_deck`, length `129` m, status `blocked_insufficient_source_overlap_not_scoring`, class `insufficient_source_overlap`, covered overlap `0.105`, HDB overlap `0.105`, OSM shelter overlap `0`, official shelter overlap `0`

## Conclusion
- score override used: `False`
- approved source-backed corrections: `3`
- ready for owner review: `6`
- blocked without more source evidence: `6`
