import type { DistanceBand } from '@moveon/shared';

/**
 * Tapered distance pricing (§6).
 *
 * Marginal, like tax brackets: the first 5 km bill at the top rate, the next 15
 * at the second, and so on. Flat per-km makes you 3x the market on long hauls
 * and nobody books an upcountry run twice.
 */
export interface DistanceSegment {
  label: string;
  km: number;
  ratePerKm: number;
  amount: number;
}

export function priceDistance(bands: readonly DistanceBand[], distanceKm: number): {
  total: number;
  segments: DistanceSegment[];
} {
  const segments: DistanceSegment[] = [];
  let lowerBound = 0;
  let total = 0;

  for (const band of bands) {
    if (lowerBound >= distanceKm) break;
    const upperBound = band.uptoKm ?? Number.POSITIVE_INFINITY;
    const km = Math.min(distanceKm, upperBound) - lowerBound;
    if (km > 0) {
      const amount = km * band.ratePerKm;
      segments.push({ label: band.label, km: round(km), ratePerKm: band.ratePerKm, amount: round(amount) });
      total += amount;
    }
    lowerBound = upperBound;
  }

  return { total: round(total), segments };
}

/** True once the trip is long enough that the open-ended intercity band applies. */
export function isIntercity(bands: readonly DistanceBand[], distanceKm: number): boolean {
  const boundaries = bands.map((b) => b.uptoKm).filter((km): km is number => km !== null);
  const lastBoundary = boundaries[boundaries.length - 1];
  // A single open-ended band means every trip is priced at one rate; nothing is
  // "intercity" because there is no city band to leave.
  if (lastBoundary === undefined) return false;
  return distanceKm > lastBoundary;
}

function round(n: number): number {
  return Math.round(n * 100) / 100;
}
