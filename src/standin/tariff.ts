// Deterministic water-cost calculator (the LLM never does tariff arithmetic — PRD §16).
// Source: Air Selangor / Selangor State Government, "Water tariff review in Peninsula Malaysia and FT Labuan",
// 1 Aug 2025, effective 1 Sep 2025. Domestic (individual houses):
//   0–20 m³: RM0.65/m³ · 20–35 m³: RM1.62/m³ · above 35 m³: RM3.51/m³ · minimum charge RM6.50.
// https://content.airselangor.com/cmi-prod-blobstorageanon-001-sea/media/0t0p130p/mb-water-tariff-review-202508.pdf

export const WATER_TARIFF = {
  source: 'Air Selangor domestic tariff, effective 1 Sep 2025',
  url: 'https://content.airselangor.com/cmi-prod-blobstorageanon-001-sea/media/0t0p130p/mb-water-tariff-review-202508.pdf',
  blocks: [
    { upTo: 20, rate: 0.65 },
    { upTo: 35, rate: 1.62 },
    { upTo: Infinity, rate: 3.51 },
  ],
  minimum: 6.5,
};

export function monthlyWaterBill(m3: number): number {
  let left = m3, prev = 0, total = 0;
  for (const b of WATER_TARIFF.blocks) {
    const inBlock = Math.max(0, Math.min(left, b.upTo - prev));
    total += inBlock * b.rate;
    left -= inBlock;
    prev = b.upTo;
    if (left <= 0) break;
  }
  return Math.max(total, WATER_TARIFF.minimum);
}

// Extra cost of `extraM3` on top of the household's normal monthly use, with the reasoning spelled out.
export function marginalWaterCost(baselineM3: number, extraM3: number) {
  const rm = monthlyWaterBill(baselineM3 + extraM3) - monthlyWaterBill(baselineM3);
  const band = WATER_TARIFF.blocks.find((b) => baselineM3 + extraM3 <= b.upTo) ?? WATER_TARIFF.blocks[2];
  const crosses = WATER_TARIFF.blocks.some((b) => baselineM3 < b.upTo && baselineM3 + extraM3 > b.upTo);
  const how = crosses
    ? `Your usual ${baselineM3.toFixed(1)} m³/month plus ${extraM3.toFixed(1)} m³ crosses into a higher tariff band, so the extra water costs RM${rm.toFixed(2)}.`
    : `${extraM3.toFixed(1)} m³ extra a month × RM${band.rate.toFixed(2)}/m³ (your usage sits in the ${band.upTo === 35 ? '20–35' : band.upTo === 20 ? '0–20' : 'above 35'} m³ band) = RM${rm.toFixed(2)}.`;
  return { rm, how, source: WATER_TARIFF.source };
}
