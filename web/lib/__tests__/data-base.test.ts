import { normalizeDataBase } from "../data";

describe("normalizeDataBase", () => {
  it("defaults to mock data", () => {
    expect(normalizeDataBase()).toBe("/data/mock/");
    expect(normalizeDataBase("")).toBe("/data/mock/");
    expect(normalizeDataBase("   ")).toBe("/data/mock/");
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
