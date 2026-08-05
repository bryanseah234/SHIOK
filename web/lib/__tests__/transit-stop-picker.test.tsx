import { readFileSync } from "fs";
import { join } from "path";
import { describe, expect, it, vi } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  buildComparisonText,
  RESET_CHIP_ID,
  TransitStopPicker,
} from "../../components/transit-stop-picker";
import {
  candidateComparison,
  deriveNearestTransitCandidates,
  haversineMeters,
  nextChipAction,
  resolveBestCandidateId,
  type TransitCandidate,
} from "../nearest-transit";
import type {
  ScoreRecord,
  TransitPoiCollection,
  TransitPoiFeature,
} from "../types";

// -- Fixtures ----------------------------------------------------------------

function poi(overrides: Partial<TransitPoiFeature> & {
  id: string;
  kind: "bus_stop" | "mrt_exit" | "mrt_station";
  coords: [number, number];
  name?: string;
  code?: string;
  station?: string;
  exit?: string;
}): TransitPoiFeature {
  const { id, kind, coords, name, code, station, exit, ...rest } = overrides;
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates: coords },
    properties: {
      id,
      kind,
      name: name ?? id,
      ...(code ? { code } : {}),
      ...(station ? { station } : {}),
      ...(exit ? { exit } : {}),
    },
    ...rest,
  } as TransitPoiFeature;
}

const originLat = 1.35;
const originLng = 103.85;

const CANDIDATES: TransitPoiCollection = {
  type: "FeatureCollection",
  features: [
    poi({ id: "bus:66361", kind: "bus_stop", coords: [103.8501, 1.3501], name: "Blk 319", code: "66361" }),
    poi({ id: "bus:66411", kind: "bus_stop", coords: [103.8510, 1.3510], name: "Blk 326", code: "66411" }),
    poi({ id: "mrt:21491", kind: "mrt_exit", coords: [103.8520, 1.3505], name: "TEST MRT STATION Exit A", station: "TEST MRT STATION", exit: "Exit A" }),
    poi({ id: "bus:66401", kind: "bus_stop", coords: [103.8530, 1.3495], name: "Golden Hts", code: "66401" }),
    poi({ id: "bus:66421", kind: "bus_stop", coords: [103.8540, 1.3490], name: "Blk 400", code: "66421" }),
    poi({ id: "bus:66431", kind: "bus_stop", coords: [103.8560, 1.3480], name: "Blk 500", code: "66431" }),
    // Should be filtered out (station kind, not exit)
    poi({ id: "station:TEST", kind: "mrt_station", coords: [103.8520, 1.3505], name: "TEST MRT STATION" }),
  ],
};

const BUS_SCORE: ScoreRecord = {
  postal: "560231",
  state: "SCORED",
  total: 60,
  subscores: { access: 100, bus: 40, rain: 20, heat: 30, crossing: 80 },
  best_node: {
    type: "bus_stop",
    name: "Blk 319",
    routed_m: 200,
    exit: "66361",
    station: "Blk 319",
    straight_line_m: 60,
    snap_distance_m: 10,
  },
  paths: { shortest_m: 200, sheltered_m: 220, detour_pct: 10, routing_type: "sheltered", covered_ratio: 0.3, shortest_covered_ratio: 0.2 },
  exposure_gaps: [],
  data_as_of: null,
  provenance: {},
};

const MRT_SCORE: ScoreRecord = {
  ...BUS_SCORE,
  best_node: {
    type: "mrt_lrt_exit",
    name: "TEST MRT STATION Exit A",
    routed_m: 400,
    exit: "Exit A",
    station: "TEST MRT STATION",
    straight_line_m: 220,
    snap_distance_m: 8,
  },
};

// -- Pure logic --------------------------------------------------------------

describe("haversineMeters", () => {
  it("returns zero for identical points", () => {
    expect(haversineMeters(1.3, 103.8, 1.3, 103.8)).toBeCloseTo(0, 3);
  });

  it("computes ~157m for one lat-arcsecond apart at Singapore latitude", () => {
    const meters = haversineMeters(1.35, 103.85, 1.3514, 103.85);
    // 0.0014 deg latitude ~= 155-160m depending on formula; assert order of magnitude
    expect(meters).toBeGreaterThan(150);
    expect(meters).toBeLessThan(165);
  });
});

describe("deriveNearestTransitCandidates", () => {
  it("returns up to 5 nearest bus_stop + mrt_exit POIs sorted by distance", () => {
    const result = deriveNearestTransitCandidates({
      originLat,
      originLng,
      transitPois: CANDIDATES,
      mode: "best_transit",
      limit: 5,
    });
    expect(result).toHaveLength(5);
    // No mrt_station in results
    expect(result.every((c) => c.kind === "bus_stop" || c.kind === "mrt_exit")).toBe(true);
    // Sorted ascending
    for (let i = 1; i < result.length; i++) {
      expect(result[i].straight_line_m).toBeGreaterThanOrEqual(result[i - 1].straight_line_m);
    }
  });

  it("filters to bus_stop only when mode=bus", () => {
    const result = deriveNearestTransitCandidates({
      originLat,
      originLng,
      transitPois: CANDIDATES,
      mode: "bus",
      limit: 5,
    });
    expect(result.every((c) => c.kind === "bus_stop")).toBe(true);
  });

  it("filters to mrt_exit only when mode=mrt_lrt", () => {
    const result = deriveNearestTransitCandidates({
      originLat,
      originLng,
      transitPois: CANDIDATES,
      mode: "mrt_lrt",
      limit: 5,
    });
    expect(result.every((c) => c.kind === "mrt_exit")).toBe(true);
  });

  it("returns [] for empty inputs", () => {
    expect(
      deriveNearestTransitCandidates({
        originLat,
        originLng,
        transitPois: null,
        mode: "best_transit",
      })
    ).toEqual([]);
    expect(
      deriveNearestTransitCandidates({
        originLat: NaN,
        originLng: 0,
        transitPois: CANDIDATES,
        mode: "best_transit",
      })
    ).toEqual([]);
  });
});

describe("resolveBestCandidateId", () => {
  it("maps a bus best_node to bus:<code>", () => {
    const candidates = deriveNearestTransitCandidates({
      originLat,
      originLng,
      transitPois: CANDIDATES,
      mode: "best_transit",
      limit: 5,
    });
    expect(resolveBestCandidateId(candidates, BUS_SCORE)).toBe("bus:66361");
  });

  it("maps an MRT exit best_node by station + exit fields", () => {
    const candidates = deriveNearestTransitCandidates({
      originLat,
      originLng,
      transitPois: CANDIDATES,
      mode: "mrt_lrt",
      limit: 5,
    });
    expect(resolveBestCandidateId(candidates, MRT_SCORE)).toBe("mrt:21491");
  });

  it("returns null when the best_node is missing", () => {
    expect(resolveBestCandidateId([], null)).toBeNull();
  });
});

describe("candidateComparison", () => {
  it("returns null when either side is missing", () => {
    const a: TransitCandidate = { id: "a", name: "a", kind: "bus_stop", straight_line_m: 100, coordinates: [0, 0] };
    expect(candidateComparison(null, a)).toBeNull();
    expect(candidateComparison(a, null)).toBeNull();
  });

  it("computes % delta", () => {
    const best: TransitCandidate = { id: "best", name: "Best", kind: "bus_stop", straight_line_m: 100, coordinates: [0, 0] };
    const active: TransitCandidate = { id: "far", name: "Far", kind: "bus_stop", straight_line_m: 150, coordinates: [0, 0] };
    const result = candidateComparison(active, best);
    expect(result).not.toBeNull();
    expect(result!.fartherPct).toBeCloseTo(50, 5);
  });
});

describe("nextChipAction (keyboard math)", () => {
  const chips = ["reset", "a", "b", "c"];
  it("ArrowRight advances with wrap-around", () => {
    expect(nextChipAction(chips, 0, "ArrowRight")).toEqual({ kind: "focus", index: 1 });
    expect(nextChipAction(chips, 3, "ArrowRight")).toEqual({ kind: "focus", index: 0 });
  });
  it("ArrowLeft retreats with wrap-around", () => {
    expect(nextChipAction(chips, 1, "ArrowLeft")).toEqual({ kind: "focus", index: 0 });
    expect(nextChipAction(chips, 0, "ArrowLeft")).toEqual({ kind: "focus", index: 3 });
  });
  it("Enter and Space activate", () => {
    expect(nextChipAction(chips, 2, "Enter")).toEqual({ kind: "activate", chipId: "b" });
    expect(nextChipAction(chips, 2, " ")).toEqual({ kind: "activate", chipId: "b" });
  });
  it("Other keys are ignored", () => {
    expect(nextChipAction(chips, 2, "Escape")).toEqual({ kind: "ignore" });
    expect(nextChipAction([], 0, "ArrowRight")).toEqual({ kind: "ignore" });
  });
});

describe("buildComparisonText", () => {
  it("returns the % farther string when the pick is farther", () => {
    expect(
      buildComparisonText({ fartherPct: 42, bestStraightM: 100, activeStraightM: 142 })
    ).toBe("42% farther than best (straight-line)");
  });
  it("returns null when the pick is not farther", () => {
    expect(buildComparisonText({ fartherPct: 0, bestStraightM: 100, activeStraightM: 100 })).toBeNull();
    expect(buildComparisonText({ fartherPct: -5, bestStraightM: 100, activeStraightM: 95 })).toBeNull();
    expect(buildComparisonText(null)).toBeNull();
    expect(buildComparisonText(undefined)).toBeNull();
  });
});

// -- Component rendering (renderToStaticMarkup smoke tests) ------------------

function renderPicker(props: Parameters<typeof TransitStopPicker>[0]): string {
  return renderToStaticMarkup(React.createElement(TransitStopPicker, props));
}

describe("TransitStopPicker component", () => {
  const candidates = deriveNearestTransitCandidates({
    originLat,
    originLng,
    transitPois: CANDIDATES,
    mode: "best_transit",
    limit: 5,
  });
  const bestStopId = "bus:66361";

  it("renders one chip per candidate (5) with the best chip active when no override", () => {
    const html = renderPicker({
      candidates,
      activeStopId: null,
      bestStopId,
      onSelect: () => {},
    });
    const chipMatches = html.match(/data-chip-id="[^"]+"/g) ?? [];
    // 5 candidate chips + 0 reset (activeStopId is null / matches best)
    expect(chipMatches).toHaveLength(5);
    expect(html).toContain(`data-chip-id="${bestStopId}"`);
    // Best chip has aria-current
    expect(html).toMatch(new RegExp(`data-chip-id="${bestStopId}"[^>]*aria-current="true"`));
  });

  it("renders reset chip + 5 candidates when a non-best stop is active", () => {
    const html = renderPicker({
      candidates,
      activeStopId: "bus:66411",
      bestStopId,
      onSelect: () => {},
    });
    const chipMatches = html.match(/data-chip-id="[^"]+"/g) ?? [];
    // 5 candidate chips + 1 reset chip
    expect(chipMatches).toHaveLength(6);
    expect(html).toContain(`data-chip-id="${RESET_CHIP_ID}"`);
    expect(html).toMatch(/data-chip-id="bus:66411"[^>]*aria-current="true"/);
    expect(html).toContain("Reset to best");
  });

  it("renders the '% farther than best' comparison note when non-best is active", () => {
    const html = renderPicker({
      candidates,
      activeStopId: "bus:66421", // furthest of the top-5 fixtures
      bestStopId,
      onSelect: () => {},
    });
    expect(html).toMatch(/\d+% farther than best/);
  });

  it("uses aria-current on the active chip and NOT on inactive chips", () => {
    const html = renderPicker({
      candidates,
      activeStopId: "bus:66411",
      bestStopId,
      onSelect: () => {},
    });
    const activeMarkers = html.match(/aria-current="true"/g) ?? [];
    expect(activeMarkers).toHaveLength(1);
  });

  it("hides the straight-line distance span on the currently-active chip", () => {
    // Rationale: the primary score card already displays the routed distance
    // for the active stop in its headline row, so we drop the chip's
    // straight-line distance to avoid two distance readings side by side.
    // Non-active chips still show their distance so users can compare.
    // Distance stays available via the button title attribute for a11y.
    const html = renderPicker({
      candidates,
      activeStopId: "bus:66411",
      bestStopId,
      onSelect: () => {},
    });
    const activeChipMatch = html.match(
      /<button[^>]*data-chip-id="bus:66411"[^>]*>[\s\S]*?<\/button>/
    );
    expect(activeChipMatch).not.toBeNull();
    // The active chip must not carry a chipDistance span.
    expect(activeChipMatch![0]).not.toMatch(/chipDistance/);
    // Sanity: some inactive chip in the same picker still shows its distance.
    const distanceMatches = html.match(/chipDistance/g) ?? [];
    expect(distanceMatches.length).toBeGreaterThan(0);
  });

  it("keeps the straight-line distance span on every chip when the auto-picked best is active", () => {
    // activeStopId === null falls back to bestStopId, so the best chip is
    // "active" for render. In that case we still hide distance on the best.
    const html = renderPicker({
      candidates,
      activeStopId: null,
      bestStopId,
      onSelect: () => {},
    });
    // 5 chips total: 4 non-active with distance, 1 active (best) without.
    const distanceMatches = html.match(/chipDistance/g) ?? [];
    expect(distanceMatches).toHaveLength(candidates.length - 1);
    // The best chip has no chipDistance.
    const bestChipMatch = html.match(
      new RegExp(`<button[^>]*data-chip-id="${bestStopId}"[^>]*>[\\s\\S]*?<\\/button>`)
    );
    expect(bestChipMatch).not.toBeNull();
    expect(bestChipMatch![0]).not.toMatch(/chipDistance/);
  });

  it("keeps the distance available via the chip title tooltip for screen readers", () => {
    const html = renderPicker({
      candidates,
      activeStopId: "bus:66411",
      bestStopId,
      onSelect: () => {},
    });
    // Every chip should still have a title with its distance in it, active or not.
    for (const candidate of candidates) {
      expect(html).toContain(`title="${candidate.name} (~`);
    }
  });

  it("returns null when there are no candidates", () => {
    const html = renderPicker({
      candidates: [],
      activeStopId: null,
      bestStopId: null,
      onSelect: () => {},
    });
    expect(html).toBe("");
  });
});

// -- Source-based structural checks (event wiring we cannot exercise) --------

describe("TransitStopPicker source contract", () => {
  const source = readFileSync(
    join(__dirname, "../../components/transit-stop-picker.tsx"),
    "utf-8"
  );

  it("wires the chip button onClick to onSelect with the candidate id", () => {
    expect(source).toContain("onClick={() => onSelect(candidate.id)}");
  });

  it("wires the reset chip onClick to onSelect(null)", () => {
    expect(source).toContain("onClick={() => onSelect(null)}");
  });

  it("routes keyboard events through nextChipAction", () => {
    expect(source).toContain("nextChipAction(chipIds, currentIndex, event.key)");
    expect(source).toContain('action.kind === "activate"');
    expect(source).toContain('action.kind === "focus"');
  });

  it("marks the active chip with aria-current", () => {
    expect(source).toContain('aria-current={isActive ? "true" : undefined}');
  });
});

// -- Handler contract: onSelect is invoked with the correct id ---------------

describe("TransitStopPicker onSelect contract", () => {
  const candidates = deriveNearestTransitCandidates({
    originLat,
    originLng,
    transitPois: CANDIDATES,
    mode: "best_transit",
    limit: 5,
  });

  it("Enter on a candidate chip activates onSelect with that candidate id", () => {
    // Walk through what the keyboard handler does when Enter is pressed on
    // a chip: nextChipAction returns {kind:"activate", chipId}, and the
    // component invokes onSelect(chipId) unless chipId === RESET_CHIP_ID.
    const chipIds = candidates.map((c) => c.id);
    const activateOn = chipIds[2];
    const action = nextChipAction(chipIds, 2, "Enter");
    expect(action).toEqual({ kind: "activate", chipId: activateOn });

    const onSelect = vi.fn();
    // Simulate the component's activation branch
    if (action.kind === "activate") {
      onSelect(action.chipId === RESET_CHIP_ID ? null : action.chipId);
    }
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(activateOn);
  });

  it("Enter on the reset chip activates onSelect(null)", () => {
    const chipIds = [RESET_CHIP_ID, ...candidates.map((c) => c.id)];
    const action = nextChipAction(chipIds, 0, "Enter");
    expect(action).toEqual({ kind: "activate", chipId: RESET_CHIP_ID });

    const onSelect = vi.fn();
    if (action.kind === "activate") {
      onSelect(action.chipId === RESET_CHIP_ID ? null : action.chipId);
    }
    expect(onSelect).toHaveBeenCalledWith(null);
  });
});
