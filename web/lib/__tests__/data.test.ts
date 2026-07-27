/**
 * F1 gate: verifies mock files load and conform to TypeScript interfaces.
 * Run with: npx jest (or npm test)
 */
import type {
  ScoreRecord,
  PostalGeom,
  Manifest,
  ScoreState,
} from "../types";
import { readFileSync } from "fs";
import { join } from "path";

const MOCK_DIR = join(__dirname, "../../public/data/mock");

function readJson<T>(rel: string): T {
  return JSON.parse(readFileSync(join(MOCK_DIR, rel), "utf-8")) as T;
}

describe("mock manifest", () => {
  it("has required fields", () => {
    const m = readJson<Manifest>("manifest.json");
    expect(typeof m.generated_at).toBe("string");
    expect(typeof m.data_as_of).toBe("string");
    expect(typeof m.provenance).toBe("string");
  });
});

describe("mock score records", () => {
  const records = readJson<ScoreRecord[]>("scores/ANG_MO_KIO.json");
  const VALID_STATES: ScoreState[] = [
    "SCORED",
    "NOT_YET_SCORED",
    "NO_TRANSIT_IN_RANGE",
  ];

  it("has at least 5 records", () => {
    expect(records.length).toBeGreaterThanOrEqual(5);
  });

  it("covers all three states", () => {
    const states = new Set(records.map((r) => r.state));
    expect(states.has("SCORED")).toBe(true);
    expect(states.has("NOT_YET_SCORED")).toBe(true);
    expect(states.has("NO_TRANSIT_IN_RANGE")).toBe(true);
  });

  it("has at least one high (~85), mid (~60), low (~35) SCORED record", () => {
    const scored = records.filter((r) => r.state === "SCORED");
    expect(scored.some((r) => r.total !== null && r.total >= 80)).toBe(true);
    expect(
      scored.some((r) => r.total !== null && r.total >= 55 && r.total < 75)
    ).toBe(true);
    expect(scored.some((r) => r.total !== null && r.total < 45)).toBe(true);
  });

  records.forEach((r) => {
    it(`record ${r.postal}: state is valid`, () => {
      expect(VALID_STATES).toContain(r.state);
    });

    it(`record ${r.postal}: SCORED fields present iff state=SCORED`, () => {
      if (r.state === "SCORED") {
        expect(r.total).not.toBeNull();
        expect(r.subscores).not.toBeNull();
        expect(r.best_node).not.toBeNull();
        expect(r.paths).not.toBeNull();
        expect(r.exposure_gaps).not.toBeNull();
        // subscores keys
        const sk = Object.keys(r.subscores!);
        for (const k of ["access", "bus", "rain", "heat", "crossing"]) {
          expect(sk).toContain(k);
        }
      } else {
        expect(r.total).toBeNull();
        expect(r.subscores).toBeNull();
      }
    });
  });
});

describe("mock geom files", () => {
  const cells = ["88652636c5fffff", "88652636c1fffff"];

  cells.forEach((cell) => {
    it(`cell ${cell}: records have required polyline fields`, () => {
      const records = readJson<PostalGeom[]>(`geom/h3/${cell}.json`);
      expect(records.length).toBeGreaterThan(0);
      for (const g of records) {
        expect(typeof g.postal).toBe("string");
        expect(typeof g.shortest).toBe("string");
        expect(typeof g.sheltered).toBe("string");
        expect(Array.isArray(g.exposure_gaps)).toBe(true);
        for (const gap of g.exposure_gaps) {
          expect(typeof gap.geom).toBe("string");
          expect(typeof gap.len_m).toBe("number");
          expect(typeof gap.label).toBe("string");
        }
      }
    });
  });
});
