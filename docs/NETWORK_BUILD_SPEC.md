# NETWORK_BUILD_SPEC.md  Authoritative spec for the S.H.I.O.K. pilot pedestrian network
**Supersedes** all prior T1.1 instructions and every round-by-round correction. Where this
conflicts with earlier chat guidance, this wins. Where it conflicts with the PRD, the PRD wins.
**Scope:** build one correct, routable, covered-attributed pilot network (Toa Payoh, Bukit Timah,
Downtown Core) and pass the acceptance gates in 7. **T1.2 stays LOCKED until 7 passes with pasted evidence.**

Reviewer: Claude (AI), relayed by the owner; binding. Owner overrides.

---

## 0. Diagnosis this spec is built on (context, not opinion)
The covered-tag census is settled fact: OSM holds ~58.7 km of `covered` ways across the three
pilot areas (Toa Payoh 28.6, Downtown Core 16.3, Bukit Timah 13.8) vs ~20 km of LTA linkways.
**The data is rich; the failures are in the matcher and the topology, not the inputs.** Two root
causes are already identified and must be fixed, not re-diagnosed:
1. `approx_centerline` (minimum-bounding-rectangle long axis) is geometrically wrong for any
   L-shaped/curved polygon  it fabricates a diagonal line floating meters from the real path,
   which alone can produce the observed 315% match rate with no real offset.
2. The pilot graph fragments into a 36%-giant-component (64% stranded). A pedestrian OSM extract of
   three contiguous areas must not fragment like this  this points to a graph-construction bug
   (edges not sharing nodes / geometry-based instead of topology-based / disabled component), not
   to the linkways at all.

The linkway-match question and the graph-connectivity question are **independent**. Do not let a
linkway problem mask a graph problem or vice versa. 3 fixes the graph; 4 fixes matching; 5 does
snapping only on the true residue.

---

## 1. Execution order (do not reorder; each step ends with the stated evidence)
1. 2 diagnostics  run ALL, paste ALL, change no build code until they're pasted.
2. 3  rebuild the OSM pedestrian graph correctly; pass 3 gate before proceeding.
3. 4  implement the real matcher; pass 4 gate before proceeding.
4. 5  snap the residual unmatched linkways; pass 5 gate.
5. 6  emit final QA + debug artifact.
6. 7  acceptance gates. All green + evidence  request the T1.2 verdict.

If any step's gate fails, fix within that step and re-run it; do not advance a failing gate.

---

## 2. Diagnostics first (cheap, decisive  these end all open arguments)
Run every item; paste verbatim output with a `Get-Date` header. **No build-code changes until this section is pasted.**

- **D-A Nearest-distance histogram (arbiter of the "offset" question).** For each LTA linkway
  polygon in the pilot, distance from the polygon itself (not any centerline) to the nearest
  `covered` OSM way. Per area: p50, p90, and buckets 01 / 13 / 36 / 610 / >10 m.
- **D-B Polygon-direct match at 3 m.** Linkway matched if
  `(polygon  buffer(covered_osm_ways, 3m)).area / polygon.area  0.5`. Report per-area % of
  linkway polygons matched (count-based here; length weighting comes in 4).
- **D-C Length-estimator cross-check.** Pilot linkway total under **perimeter/2** vs the current
  MBR-axis method, side by side (perimeter/2 is the estimator calibrated to the ~250 km national truth).
- **D-D Graph-fragmentation probe (the important one).** BEFORE any linkway logic  build the graph
  from OSM pedestrian edges ONLY (no linkways, no synthesis) and report: node count, edge count,
  connected-component count, giant-component edge share %, top-5 component sizes. This isolates
  whether fragmentation is an OSM-graph bug or a synthesis artifact.
- **D-E Node-sharing check.** For 5 pairs of OSM ways that share a coordinate at a junction,
  confirm they share the same graph node id (not two coincident-but-distinct nodes). Paste the 5
  results. This directly tests the topology-vs-geometry construction bug.

Interpretation is fixed in advance: if D-D shows the OSM-only graph already fragmented  3 is the
real fix. If D-B jumps far above 15% at unchanged 3 m tolerance  the MBR centerline is convicted
and 4's rewrite is mandatory. If D-A clusters under 2 m  there is no real offset; do not widen buffers.

---

## 2.5 Diagnostic verdicts (RATIFIED 2026-07-27  these are now settled facts)
The 2 battery ran clean and decides three open questions:
1. **The graph was never broken.** D-D shows the top-3 components = 32,303 + 30,250 + 19,143 =
   97.9% of 84,869 nodes. The three pilot areas are geographically separate, so three big
   components is the *correct* structure, not a failure. D-E confirms junction nodes are genuinely
   shared (410 edges per node). The "64%-stranded" alarm was a metric error (single-giant-share is
   the wrong gate for a multi-area pilot), now fixed in 3 and 7. **No graph-construction bug exists.**
2. **The matcher is NOT simply fabricating the low match rate.** D-A is *bimodal*: in Toa Payoh 123
   linkways are within 1 m of a covered OSM way while 129 are beyond 10 m (p90 = 106 m). This is not
   a uniform 310 m offset  widening the buffer alone is the wrong fix. The low D-B match rates
   (36/32/12%) are substantially real, not an MBR-centerline artifact.
3. **Downtown Core is a genuine surface-vs-underground data mismatch** (p50 = 17.9 m, 8/65 matched):
   downtown shelter is underground/in-mall MRT links that OSM maps on separate levels or omits, so
   LTA's surface linkway polygons have no nearby *surface* covered-way. This is a data-domain gap,
   not a bug  handle by classification in 4, not by force-matching.

`approx_centerline` (MBR axis) is still deleted regardless  D-C shows it under-measures vs
perimeter/2, and it is geometrically wrong for curved polygons. Perimeter/2 is the length estimator.

## 3. OSM pedestrian graph  accept the multi-area structure
**Goal:** a correctly-built pedestrian graph where ways sharing a junction share a node. Per 2.5(1)
this is already achieved  this section's gate is corrected to match the multi-area reality.

- **Clip:** union of the three planning-area polygons (row 10) buffered 500 m, built in EPSG:3414;
  reproject to 4326 only if the PBF reader's bbox filter requires it. Assert every loaded layer
  falls inside the SVY21 envelope (CLAUDE.md rule). Print clip area (expect ~30.8 km pre-buffer).
- **Reader:** pyrosm (pinned). Use pyrosm's network graph construction  `get_network(nodes=True,
  network_type="walking")` returns nodes and edges with a shared node index. **Do not** rebuild
  topology yourself by intersecting geometries; use the reader's node/edge tables so junction nodes
  are shared by construction. If you keep any custom graph assembly, it must key edges on OSM node
  ids, never on coordinate proximity.
- **Edge whitelist:** Keep Pyrosm's `network_type="walking"`, which naturally includes primary/secondary/tertiary/residential/service because pedestrians use their sidewalks. **Exclude:** ONLY where genuinely foot-forbidden, i.e., `highway` in `motorway, motorway_link, trunk, trunk_link, construction` AND NOT `foot=yes|permissive|designated`. Also exclude `access=private|no` (unless `foot=yes`).
- **Build the graph in igraph** from the (source_node, target_node, length_m) triples; length in
  metres computed in EPSG:3414.
- **3 GATE (CORRECTED for the 3-area pilot; paste evidence):** on the OSM-only graph, there must be exactly ~3 dominant components (one large component per geographically-separate pilot area).  * **GATE PASS CONDITION:**
  Every residual component larger than 50 nodes must be individually classified by coordinate as:
  - `PRIVATE_ESTATE`: legitimately isolated behind access controls.
  - `CLIP_EDGE`: artifact of the 500m buffer cutting off its connecting path.
  - `ISOLATED_NON_TRANSIT`: parkland/forest/water paths with no transit relevance.
  - `REAL_DISCONNECTION`: genuine bug where a public component is stranded.
  
  The gate PASSES when zero components are `REAL_DISCONNECTION`, regardless of the aggregate percentage. Any real OSM gap <5m can be auto-bridged, while 5-15m gaps require owner confirmation. Classify programmatically (e.g. `access=private`, or near clip boundary) but report every >50-node component with coordinate and class so the owner can spot-check. Mean edge length ~10-40 m; total edges in a sane range (the ~95k observed across the three buffered clips is fine).

---

## 4. Linkway matcher  classify the bimodal reality, don't force-match
`approx_centerline` is deleted (MBR axis, geometrically wrong). The D-A verdict (2.5) governs this
section: linkways fall into three classes, and each is handled differently. Do NOT try to push the
global match rate up by widening buffers  that would false-match unrelated paths.

- **Attribution feeding scoring is unchanged and is the real output (PRD D1):** an OSM edge
  `is_covered = 1` if EITHER `covered{yes, covered, arcade, colonnade, ...}` on the OSM way, OR
   60% of the edge's length lies within `buffer(LTA_linkway_polygons, 3m)`. This attribute  driven
  mostly by OSM's own ~58.7 km of covered tags  is what routing/scoring consume. Match% below is QA.
- **Classify every pilot linkway polygon by its D-A nearest-distance to a covered OSM way:**
  - **ALIGNED ( 3 m):** counts as matched. These are the ~123/89/14 polygons in the 01 m + 13 m
    buckets  the OSM covered network already represents them; no synthesis needed.
  - **OFFSET (310 m):** small digitization drift. Snap-eligible in 5 (residual centerline snapped
    to the nearby OSM node), NOT buffer-matched (a 10 m buffer over-captures). Report the count.
  - **UNREPRESENTED (> 10 m):** genuinely no nearby surface covered-way  dominated by Downtown
    Core's underground/in-mall links. These are candidates for synthesis in 5, BUT first apply the
    underground test below so we don't synthesize a fake *surface* path over an underground reality.
- **Underground/level test for UNREPRESENTED polygons:** check whether an OSM way of ANY kind
  (covered or not) tagged `layer<0`, `tunnel=yes`, `level=*<0`, or inside a `building`/`indoor` area
  exists within 10 m. If yes  classify `UNDERGROUND_OR_INDOOR`: attribute the corresponding graph
  edge (if any) as covered/underground but do NOT synthesize a new surface edge; if no routable edge
  exists there, mark the linkway `NEEDS_MANUAL` and log it  do not fabricate geometry. If no such
  way exists  it is a true surface gap, synthesize in 5.
- **Match% (QA metric only, explicitly not the shelter ceiling):** report per area the counts in each
  class (ALIGNED / OFFSET / UNREPRESENTED-surface / UNDERGROUND_OR_INDOOR / NEEDS_MANUAL) and
  ALIGNED length (Σ perimeter/2)  total length. State in words that covered attribution does not
  depend on this number because OSM tags carry ~3 the LTA length.
- **4 GATE (paste evidence):** the five-way classification counts per area, and a one-line
  explanation per area consistent with D-A (e.g., "Downtown Core: 37 UNREPRESENTED of which N are
  UNDERGROUND_OR_INDOOR  expected, downtown shelter is subterranean"). No minimum match% to pass;
  the gate is that every class count is explained and the OFFSET+surface-gap residue handed to 5 is
  small relative to total (the bulk should be ALIGNED + UNDERGROUND_OR_INDOOR, i.e. already
  represented or legitimately not synthesizable).

---

## 5. Synthesis + snapping  only OFFSET and true-surface-gap residue
Operates ONLY on the 4 classes that warrant it: **OFFSET** (snap) and **UNREPRESENTED-surface**
(synthesize). ALIGNED needs nothing; UNDERGROUND_OR_INDOOR and NEEDS_MANUAL are never synthesized
as surface geometry.

- **OFFSET polygons (310 m):** derive a real centerline (proper skeleton via `centerline` package,
  or a straight segment only for near-rectangular thin polygons  never MBR axis) and snap its
  endpoints to the nearest existing graph node  (offset distance + 1 m, cap 11 m). These weld drift-
  displaced linkways onto the OSM path they parallel; they should almost all snap.
- **UNREPRESENTED-surface polygons:** skeleton centerline, snap endpoints  2 m to a node, else split
  the nearest edge within  5 m and insert a node; if neither, record unsnapped and log as
  NEEDS_MANUAL rather than leaving a floating stub.
- After insertion, **re-run connected components per area**. A synthesized edge in its own tiny
  component is a failed weld, not a success  the earlier "20 km of floating islands" was this
  failure state and is not acceptable.
- **5 GATE (paste evidence):** total synthesized-surface length  ~15% of total pilot linkway length
  (small because most linkways are ALIGNED or legitimately UNDERGROUND_OR_INDOOR); unsnapped/
  NEEDS_MANUAL counts reported explicitly; post-snap per-area top-3 component share still  97% (no
  new fragmentation introduced).

---

## 6. Final QA report + debug artifact
Write `qa/conflation_qa_pilot.json` (never under `raw/`, which gets wiped) and paste it in full:
- nodes, edges, mean_edge_length_m
- connected_components_count, giant_component_edge_share_pct, top_5_component_sizes
- per_area_match_pct (corrected 4 metric)
- linkway_total_length_m (perimeter/2), synthesized_length_m, synthesized_pct_of_total
- unsnapped_endpoints_count
- covered_edge_length_m via OSM tags alone, via LTA-match alone, and via the union (shows the OSM
  term dominates)
- flags[] for any 7 threshold breached
Also export `qa/pilot_debug.geojson`: OSM edges (colored covered/uncovered), LTA polygons
(matched/unmatched), synthesized edges. Open it once on geojson.io and confirm visually that
covered paths trace real corridors and synthesized edges are few and connected.

---

## 7. Acceptance gates  ALL must be green, with pasted evidence, to unlock T1.2
1. OSM-only graph: the three dominant components are the expected geographically separate pilot
   areas, and every residual component >50 nodes is classified by coordinate. The gate passes with
   zero `REAL_DISCONNECTION` residuals; top-3 component share is reported as a diagnostic, not a
   hard fail, because legitimate clip-edge/private/non-transit residuals exist.
2. Final graph (post-synthesis): no new `REAL_DISCONNECTION` residuals are introduced; top-3
   component share is still reported to catch unexpected fragmentation regressions.
3. Mean edge length 1040 m; total edges in a sane range for the buffered 3-area clip (~95k is fine;
   there is no 15k40k cap).
4. Every 4 five-way class count (ALIGNED/OFFSET/UNREPRESENTED-surface/UNDERGROUND_OR_INDOOR/
   NEEDS_MANUAL) is reported per area with a one-line explanation consistent with D-A. Downtown Core
   having many UNDERGROUND_OR_INDOOR is expected and passes  it is not a defect.
5. Synthesized-surface length  15% of total pilot linkway length; unsnapped + NEEDS_MANUAL counts
   reported explicitly (near zero unsnapped; NEEDS_MANUAL logged, not fabricated).
6. `qa/pilot_debug.geojson` visually sane on geojson.io (state you inspected it and what you saw:
   covered edges trace real corridors; synthesized edges few and connected; underground gaps not
   papered over with fake surface lines).
7. No fabrication: every number comes from pasted command output in this same report, each evidence
   block headed by `Get-Date`, silent commands appended with `; echo "exit=$LASTEXITCODE"`.

When all seven are green, present the QA JSON prominently and request the T1.2 verdict. Do not start
T1.2 before the reviewer rules.

---

## 8. Standing rules (unchanged, restated because they keep being broken)
- **Diagnostics before fixes.** Never edit build code before pasting the 2 battery.
- **No stub/placeholder data in any metric or QA output**  stubs raise or print `STUB`.
- **No inventing** URLs, IDs, rates, or limits. Discovery parses live listings; the OneMap throttle
  is 2.0 s (0.5 req/s); there is no "120/min" or "250/min".
- **No silent endpoint/parameter switches**  every change is logged with the evidence that justified it.
- **Failure states are not successes.** A stranded graph or a mostly-synthesized linkway set is a
  FAIL regardless of how the summary is phrased.
- **Parallel lane still open:** T1.4 scoring engine (pure functions, no geodata) may proceed
  alongside per its own BUILD_PLAN AC; it does not gate on this spec and this spec does not gate on it.
- Long-running geocode job: the pilot job already running must still deliver its retro-evidence
  (status output, first 10 log lines, 5 timestamps ~2.0 s apart, final deduplicated pilot universe
  counts, 3 sample cached responses) in the next report.
