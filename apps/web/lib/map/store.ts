"use client";

import { create } from "zustand";
import { type Aggregation, type MapFilters, defaultFilters } from "./query";

/** Local UI state for the Home Map (spec: Zustand only for map/filter/layout state). */

export type Layers = { cases: boolean; regions: boolean; stations: boolean; beats: boolean; records: boolean };

export type Selection =
  | { kind: "incident"; id: string }
  | { kind: "cell"; key: string; properties: Record<string, unknown> }
  | { kind: "beat"; code: string; properties: Record<string, unknown> }
  | { kind: "record"; id: string; properties: Record<string, unknown> }
  | null;

type MapState = {
  draft: MapFilters;
  applied: MapFilters;
  aggregation: Aggregation;
  hexSizeM: number;
  layers: Layers;
  selection: Selection;
  picking: boolean;
  pickedPoint: { lon: number; lat: number } | null;
  dialogOpen: boolean;
  setDialogOpen: (open: boolean) => void;
  setDraft: (patch: Partial<MapFilters>) => void;
  apply: () => void;
  reset: () => void;
  switchRegion: (region: string) => void;
  setAggregation: (aggregation: Aggregation) => void;
  setHexSize: (size: number) => void;
  toggleLayer: (layer: keyof Layers) => void;
  select: (selection: Selection) => void;
  startPicking: () => void;
  cancelPicking: () => void;
  pick: (point: { lon: number; lat: number } | null) => void;
};

export const useMapStore = create<MapState>((set) => ({
  draft: defaultFilters("PDY"),
  applied: defaultFilters("PDY"),
  aggregation: "HEX",
  hexSizeM: 500,
  layers: { cases: true, regions: true, stations: true, beats: false, records: false },
  selection: null,
  picking: false,
  pickedPoint: null,
  dialogOpen: false,
  setDialogOpen: (dialogOpen) => set({ dialogOpen }),
  setDraft: (patch) => set((s) => ({ draft: { ...s.draft, ...patch } })),
  apply: () => set((s) => ({ applied: s.draft, selection: null })),
  reset: () =>
    set((s) => {
      const fresh = defaultFilters(s.applied.region);
      return { draft: fresh, applied: fresh, selection: null };
    }),
  // A picked point belongs to the region it was picked in, so it does not survive a region switch.
  switchRegion: (region) =>
    set(() => {
      const fresh = defaultFilters(region);
      return { draft: fresh, applied: fresh, selection: null, pickedPoint: null, picking: false };
    }),
  setAggregation: (aggregation) => set({ aggregation, selection: null }),
  setHexSize: (hexSizeM) => set({ hexSizeM }),
  toggleLayer: (layer) => set((s) => ({ layers: { ...s.layers, [layer]: !s.layers[layer] } })),
  select: (selection) => set({ selection }),
  startPicking: () => set({ picking: true }),
  // Leaving picking mode without a point returns to the dialog it was started from.
  cancelPicking: () => set({ picking: false, dialogOpen: true }),
  // Choosing a point (re)opens the record dialog it was picked for.
  pick: (pickedPoint) => set((s) => ({ pickedPoint, picking: false, dialogOpen: pickedPoint ? true : s.dialogOpen })),
}));
