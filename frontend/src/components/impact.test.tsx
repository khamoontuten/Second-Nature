import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ImpactMetric } from "../types/api";
import { DeltaText, fmtPct, fmtVal, StatusBadge, STATUS_META } from "./impact";

const metric = (over: Partial<ImpactMetric>): ImpactMetric => ({
  key: "facility_mwh", label: "Total facility electricity", group: "energy", unit: "MWh/yr", lower_is_better: true,
  baseline: 36711, value: 32553, delta: -4158, pct: -11.33, pct_note: null, status: "modelled", baseline_status: "modelled",
  note: "", direction: "decrease", assessment: "improvement", ...over,
});

describe("impact value formatting", () => {
  it("formats by unit without false precision", () => {
    expect(fmtVal(36711.2, "MWh/yr")).toBe("36,711");
    expect(fmtVal(1.1176, "–")).toBe("1.118");
    expect(fmtVal(46410302, "USD")).toBe("46.41M");
    expect(fmtVal(0.00024, "L/kWh")).toBe("0.00024");
    expect(fmtVal(null, "m²")).toBe("—");
    expect(fmtPct(-11.33)).toBe("-11 %");
    expect(fmtPct(2.04)).toBe("+2.0 %");
    expect(fmtPct(null)).toBe("");
  });
});

describe("DeltaText", () => {
  it("shows direction, signed change and percentage", () => {
    const { container } = render(<DeltaText m={metric({})} />);
    expect(container.textContent).toContain("▼");
    expect(container.textContent).toContain("-4,158");
    expect(container.textContent).toContain("(-11 %)");
    expect(container.textContent).toContain("improvement");
  });

  it("never renders a missing value as zero", () => {
    const { container } = render(<DeltaText m={metric({ value: null, delta: null, pct: null, status: "insufficient_data", note: "unit rates missing" })} />);
    expect(container.textContent).not.toMatch(/\b0\b/);
    expect(container.querySelector('[data-status="insufficient_data"]')).not.toBeNull();
  });

  it("withholds a misleading percentage but keeps the absolute change", () => {
    const { container } = render(<DeltaText m={metric({ key: "water_withdrawal_m3", unit: "m³/yr", delta: 7258, pct: null, pct_note: "baseline below the materiality floor", direction: "increase", assessment: "trade-off" })} />);
    expect(container.textContent).toContain("+7,258");
    expect(container.textContent).toContain("% n/a");
  });
});

describe("status labels", () => {
  it("every status has an icon and a text label (never colour alone)", () => {
    for (const [status, m] of Object.entries(STATUS_META)) {
      expect(m.icon.length).toBeGreaterThan(0);
      render(<StatusBadge status={status as keyof typeof STATUS_META} />);
      expect(screen.getAllByText(m.label).length).toBeGreaterThan(0);
    }
  });
});
