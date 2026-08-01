import { cleanTransitPoiProperties, transitPoiLabelText, transitPoiPopupHtml } from "../transit-popup";

describe("transit popup formatting", () => {
  it("shows static bus stop service details without claiming live arrivals", () => {
    const html = transitPoiPopupHtml({
      kind: "bus_stop",
      name: "OPP MAYFLOWER SEC SCH",
      code: "54211",
      road: "ANG MO KIO AVE 4",
      services: "71, 76, 262",
      weekday_first_bus: "05:45",
      weekday_last_bus: "00:38",
      am_peak_best_min: 4,
      pm_peak_best_min: 6,
      operators: "SBST",
    });

    expect(html).toContain("Opp Mayflower Sec Sch");
    expect(html).toContain("Bus stop");
    expect(html).toContain("54211");
    expect(html).toContain("71, 76, 262");
    expect(html).toContain("05:45");
    expect(html).toContain("00:38");
    expect(html).toContain("4 min best");
    expect(html).toContain("6 min best");
    expect(html).not.toMatch(/arrival|eta/i);
  });

  it("shows MRT station and exit details", () => {
    const stationHtml = transitPoiPopupHtml({
      kind: "mrt_station",
      name: "MAYFLOWER MRT STATION",
      system: "MRT",
      lines: "TE",
      exit_count: 5,
    });
    const exitHtml = transitPoiPopupHtml({
      kind: "mrt_exit",
      name: "MAYFLOWER MRT STATION EXIT 5",
      station: "MAYFLOWER MRT STATION",
      exit: "Exit 5",
      system: "MRT",
      line: "TE",
    });

    expect(stationHtml).toContain("Mayflower MRT Station");
    expect(stationHtml).toContain("MRT/LRT station");
    expect(stationHtml).toContain("Exits");
    expect(stationHtml).toContain("TE");
    expect(exitHtml).toContain("MRT/LRT exit");
    expect(exitHtml).toContain("Mayflower MRT Station");
    expect(exitHtml).toContain("Exit 5");
  });

  it("cleans labels and escapes unsafe popup text", () => {
    expect(transitPoiLabelText({ kind: "mrt_station", name: "MAYFLOWER MRT STATION" })).toBe("Mayflower");
    expect(transitPoiLabelText({ kind: "bus_stop", code: "54211" })).toBe("Bus 54211");

    const clean = cleanTransitPoiProperties({
      kind: "bus_stop",
      name: "OPP MAYFLOWER SEC SCH",
      geometry: { type: "Point" },
    });
    expect(clean).toMatchObject({ kind: "bus_stop", label_text: "Opp Mayflower Sec Sch" });
    expect(clean).not.toHaveProperty("geometry");

    const html = transitPoiPopupHtml({
      kind: "bus_stop",
      name: "<script>alert(1)</script>",
      code: "54211",
    });
    expect(html).toContain("&lt;Script&gt;Alert(1)&lt;/Script&gt;");
    expect(html).not.toContain("<script>");
  });
});
