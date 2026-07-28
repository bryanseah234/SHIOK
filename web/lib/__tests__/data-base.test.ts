import { normalizeDataBase } from "../data";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("normalizeDataBase", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to generated data", () => {
    expect(normalizeDataBase()).toBe("/data/generated_20260728_1405/");
    expect(normalizeDataBase("")).toBe("/data/generated_20260728_1405/");
    expect(normalizeDataBase("   ")).toBe("/data/generated_20260728_1405/");
  });

  it("normalizes relative and absolute paths", () => {
    expect(normalizeDataBase("data")).toBe("/data/");
    expect(normalizeDataBase("/data")).toBe("/data/");
    expect(normalizeDataBase("/data/generated/")).toBe("/data/generated/");
  });

  it("preserves absolute URLs while ensuring a trailing slash", () => {
    expect(normalizeDataBase("https://example.test/data")).toBe("https://example.test/data/");
  });
});
