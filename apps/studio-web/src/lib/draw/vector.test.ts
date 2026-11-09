import { describe, expect, it } from "vitest";

import { parseVectorData } from "./engine";

describe("parseVectorData", () => {
  it("parses a JSON path list", () => {
    expect(
      parseVectorData(
        JSON.stringify([
          { d: "M10 10 L50 50", stroke: "#111", stroke_width: 3, fill: "none" },
          { d: "M0 0 L9 9" },
        ]),
      ),
    ).toEqual([
      { d: "M10 10 L50 50", stroke: "#111", stroke_width: 3, fill: "none" },
      { d: "M0 0 L9 9" },
    ]);
  });

  it("returns [] for missing or corrupt payloads", () => {
    expect(parseVectorData(null)).toEqual([]);
    expect(parseVectorData(undefined)).toEqual([]);
    expect(parseVectorData("")).toEqual([]);
    expect(parseVectorData("not-json")).toEqual([]);
    expect(parseVectorData(JSON.stringify({ d: "M0 0 L1 1" }))).toEqual([]);
  });

  it("drops entries without a d string", () => {
    expect(
      parseVectorData(JSON.stringify([{ d: "M1 1 L2 2" }, { stroke: "#000" }, null, 7])),
    ).toEqual([{ d: "M1 1 L2 2" }]);
  });
});
