/**
 * Map palette, derived from the design tokens (packages/ui/tokens.css). WebGL layers need literal
 * colours. Category colour is never the only cue: the legend and drawer always name the category.
 */
export const MAP_COLORS = {
  background: "#EDEEEB", // surface-2
  regionLine: "#0F2340", // navy
  stationLine: "#246A67", // teal-text
  beatLine: "#7A828B", // control-border
  cellLow: "#CFE3E2",
  cellHigh: "#1B4F4D",
  point: "#2D7D7A", // teal
  dqOutline: "#8F5A16", // saffron-strong
  record: "#C9822B", // saffron (fill only, with dark outline)
  selected: "#0F2340",
} as const;

/** Colour-blind-safe categorical palette (Okabe–Ito based), fixed per category code. */
export const PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#6B6B6B", "#882255", "#117733", "#332288", "#999933"];

export function categoryColor(code: string, codes: string[]): string {
  const index = codes.indexOf(code);
  return index < 0 ? "#6B6B6B" : (PALETTE[index % PALETTE.length] ?? "#6B6B6B");
}
