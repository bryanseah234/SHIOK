import { decodePolyline, encodePolyline } from "../polyline";

describe("decodePolyline & encodePolyline", () => {
  it("decodes the standard Google polyline example", () => {
    const points = decodePolyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@");

    expect(points).toEqual([
      [38.5, -120.2],
      [40.7, -120.95],
      [43.252, -126.453],
    ]);
  });

  it("round-trips encode and decode", () => {
    const points: [number, number][] = [
      [1.3521, 103.8492],
      [1.3534, 103.8505],
      [1.3548, 103.8521],
    ];
    const encoded = encodePolyline(points);
    const decoded = decodePolyline(encoded);

    expect(decoded.length).toBe(points.length);
    for (let i = 0; i < points.length; i++) {
      expect(decoded[i][0]).toBeCloseTo(points[i][0], 4);
      expect(decoded[i][1]).toBeCloseTo(points[i][1], 4);
    }
  });
});
