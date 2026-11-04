import { describe, expect, it } from "vitest";
import { formatAsset, formatUSD, formatUsd, money, requestValue, sumAsset, sumUsd, termsText, toCents } from "./format";

describe("formatUSD: the one USD formatter", () => {
  it("formats business amounts as $ with 2 decimals", () => {
    expect(formatUSD(40)).toBe("$40.00");
    expect(formatUSD(0)).toBe("$0.00");
    expect(formatUSD(12.5)).toBe("$12.50");
    expect(formatUSD("1250")).toBe("$1,250.00");
    expect(formatUSD("10.50")).toBe("$10.50");
    expect(formatUsd("-3.1")).toBe("−$3.10");
  });

  it("never adds a currency code or a second $", () => {
    for (const v of ["40", "0.00", "1234567.891"]) {
      const out = formatUSD(v);
      expect(out.match(/\$/g)).toHaveLength(1);
      expect(out).not.toMatch(/USD|TAZ|ZEC/);
    }
  });

  it("is exact: rounds half up on cents without float drift", () => {
    expect(toCents("0.105")).toBe(11n);
    expect(formatUSD("0.1")).toBe("$0.10");
    expect(sumUsd(["0.10", "0.20"])).toBe("0.30"); // 0.1 + 0.2 !== 0.3 in floats
    expect(sumUsd(["19.99", "20.01", null, "x"])).toBe("40.00");
  });

  it("shows an em dash for missing values", () => {
    expect(formatUSD("")).toBe("—");
  });
});

describe("business values vs the payment asset", () => {
  it("campaign and deal amounts are USD", () => {
    expect(money("40", "USD")).toBe("$40.00");
    expect(termsText({ fixed_fee: "19", cpm: "2.5", cap: "40" }, "hybrid", "USD")).toBe("$19.00 fixed + $2.50 CPM, cap $40.00");
  });

  it("campaigns from before USD pricing are shown in $ too, never with TAZ", () => {
    expect(money("40", "TAZ")).toBe("$40.00");
    expect(termsText({ fixed_fee: "3", cpm: "0", cap: "3" }, "fixed", "TAZ")).toBe("$3.00 fixed");
  });

  it("a payment request's value is its USD amount; the asset amount stays native", () => {
    expect(requestValue({ usd_amount: "20.00", amount: "0.20000000", asset: "TAZ" })).toBe("$20.00");
    expect(requestValue({ usd_amount: null, amount: "0.5", asset: "TAZ" })).toBe("$0.50");
    expect(formatAsset("0.20000000", "TAZ")).toBe("0.20 TAZ");
    expect(formatAsset("0.00050000", "TAZ")).toBe("0.0005 TAZ");
    expect(sumAsset(["0.1", "0.2"])).toBe("0.30000000");
  });
});
