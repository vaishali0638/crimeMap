"use client";

import {
  type GeoJSONSource,
  type LngLatBoundsLike,
  Map as MapLibreMap,
  type MapMouseEvent,
  NavigationControl,
  ScaleControl,
  setWorkerUrl,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Aggregation } from "@/lib/map/query";
import type { Layers, Selection } from "@/lib/map/store";
import { MAP_COLORS, categoryColor } from "@/lib/map/style";

export type FC = {
  type: "FeatureCollection";
  features: { type?: string; geometry: { type: string; coordinates?: unknown } | null; properties?: Record<string, unknown> | null }[];
};
const EMPTY: FC = { type: "FeatureCollection", features: [] };

// The worker is served from the app's own origin (scripts/copy-map-assets.mjs; CSP worker-src 'self').
setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

/** Official OpenStreetMap Dark-Navy Vector Basemap (OpenFreeMap Fiord) for Puducherry, Karaikal, Mahe and Yanam. */
const DEFAULT_DARK_NAVY_STYLE = "https://tiles.openfreemap.org/styles/fiord";

/** Official Puducherry & Karaikal police station locality labels mapped by station code. */
export const STATION_LOCALITY_LABELS: Record<string, string> = {
  "PDY-S01": "Kalapet PS (PDY-S01)",
  "PDY-S02": "Mettupalayam PS (PDY-S02)",
  "PDY-S03": "Lawspet & Grand Bazaar PS (PDY-S03)",
  "PDY-S04": "Thirubuvanai PS (PDY-S04)",
  "PDY-S05": "Villianur & Oulgaret PS (PDY-S05)",
  "PDY-S06": "Nettapakkam PS (PDY-S06)",
  "PDY-S07": "Mangalam PS (PDY-S07)",
  "PDY-S08": "Ariyankuppam & Mudaliarpet PS (PDY-S08)",
  "PDY-S09": "Kirumampakkam & Thavalakuppam PS (PDY-S09)",
  "PDY-S10": "Bahour PS (PDY-S10)",
  "KKL-S01": "Nedungadu PS (KKL-S01)",
  "KKL-S02": "Kottucherry PS (KKL-S02)",
  "KKL-S03": "Thirunallar PS (KKL-S03)",
  "KKL-S04": "Karaikal Town PS (KKL-S04)",
  "KKL-S05": "Neravy & T.R. Pattinam PS (KKL-S05)",
  "MAH-S01": "Mahe Town PS (MAH-S01)",
  "MAH-S02": "Palloor PS (MAH-S02)",
  "YAN-S01": "Yanam Town PS (YAN-S01)",
  "YAN-S02": "Mettakur PS (YAN-S02)",
};

export type LocalityTagSeed = {
  id: string;
  caseId: string;
  label: string;
  station: string;
  lon: number;
  lat: number;
  variant: "crime" | "station";
  date: string;
  status: string;
  category: string;
  description: string;
};

/** Official Puducherry, Karaikal, Mahe & Yanam map tags positioned on real coastal & urban localities. */
export const REGION_LOCALITY_TAGS: Record<string, LocalityTagSeed[]> = {
  PDY: [
    {
      id: "pdy-whitetown",
      caseId: "4592",
      label: "White Town",
      station: "Grand Bazaar PS (PDY-S03)",
      lon: 79.8345,
      lat: 11.9338,
      variant: "crime",
      date: "21 Sep 2026",
      status: "Closed",
      category: "Property / Theft",
      description:
        "Reported vehicle and commercial theft cluster along Beach Road and White Town boulevard sector; patrol verification completed and case closed after recovery.",
    },
    {
      id: "pdy-muthialpet",
      caseId: "4608",
      label: "Muthialpet",
      station: "Kalapet PS (PDY-S01)",
      lon: 79.8305,
      lat: 11.9585,
      variant: "crime",
      date: "28 Sep 2026",
      status: "Active",
      category: "Traffic / Road Safety",
      description:
        "High-density traffic and two-wheeler incident reports near Mahatma Gandhi Road North junction during evening peak hours.",
    },
    {
      id: "pdy-solainagar",
      caseId: "4614",
      label: "Solai Nagar",
      station: "Kalapet PS (PDY-S01)",
      lon: 79.8375,
      lat: 11.9645,
      variant: "station",
      date: "02 Oct 2026",
      status: "Verified",
      category: "Coastal Beat Patrol",
      description:
        "Coastal beat checkpoint and CCTV coverage sector monitoring northern beach road and fishing harbour approaches.",
    },
    {
      id: "pdy-lawspet",
      caseId: "4621",
      label: "Lawspet",
      station: "Lawspet PS (PDY-S03)",
      lon: 79.8095,
      lat: 11.9545,
      variant: "crime",
      date: "04 Oct 2026",
      status: "Under Review",
      category: "Cyber-Enabled Fraud",
      description:
        "Multiple UPI and online marketplace fraud complaints logged across Lawspet residential blocks and Airport Road.",
    },
    {
      id: "pdy-gorimedu",
      caseId: "4629",
      label: "Gorimedu",
      station: "Mettupalayam PS (PDY-S02)",
      lon: 79.7935,
      lat: 11.9575,
      variant: "crime",
      date: "05 Oct 2026",
      status: "Closed",
      category: "Public Order",
      description:
        "Border checkpost and JIPMER hospital corridor crowd management and night patrol incident log.",
    },
    {
      id: "pdy-saradambal",
      caseId: "4635",
      label: "Saram",
      station: "Lawspet PS (PDY-S03)",
      lon: 79.8185,
      lat: 11.9445,
      variant: "station",
      date: "06 Oct 2026",
      status: "Verified",
      category: "Station Outpost",
      description:
        "Kamaraj Salai traffic enforcement and commercial market surveillance beat covering Saram and Rainbow Nagar.",
    },
    {
      id: "pdy-heritage",
      caseId: "4642",
      label: "Heritage Town",
      station: "Grand Bazaar PS (PDY-S03)",
      lon: 79.8315,
      lat: 11.9415,
      variant: "crime",
      date: "07 Oct 2026",
      status: "Closed",
      category: "Property / Theft",
      description:
        "Evening market snatching and parked two-wheeler incident cluster near Nehru Street and Mission Street.",
    },
    {
      id: "pdy-grandbazaar",
      caseId: "4649",
      label: "Grand Bazaar",
      station: "Grand Bazaar PS (PDY-S03)",
      lon: 79.8255,
      lat: 11.9365,
      variant: "crime",
      date: "08 Oct 2026",
      status: "Active",
      category: "Commercial Theft",
      description:
        "High footfall market area incident reports; beat officers assigned for peak commercial hours.",
    },
    {
      id: "pdy-reddiarpalayam",
      caseId: "4655",
      label: "Reddiarpalayam",
      station: "Villianur & Oulgaret PS (PDY-S05)",
      lon: 79.8025,
      lat: 11.9325,
      variant: "crime",
      date: "09 Oct 2026",
      status: "Closed",
      category: "Burglary / Breaking",
      description:
        "Residential sector along Villianur Main Road; night patrol frequency increased following pattern review.",
    },
    {
      id: "pdy-oulgaret",
      caseId: "4661",
      label: "Oulgaret",
      station: "Villianur & Oulgaret PS (PDY-S05)",
      lon: 79.7915,
      lat: 11.9395,
      variant: "station",
      date: "09 Oct 2026",
      status: "Verified",
      category: "Sub-Division HQ",
      description:
        "Oulgaret municipal jurisdiction coordination center covering western highway corridors.",
    },
    {
      id: "pdy-nellithope",
      caseId: "4668",
      label: "Nellithope",
      station: "Ariyankuppam & Mudaliarpet PS (PDY-S08)",
      lon: 79.8145,
      lat: 11.9245,
      variant: "crime",
      date: "10 Oct 2026",
      status: "Active",
      category: "Violence Against Person",
      description:
        "Evening dispute and assault calls near Nellithope signal and Maraimalai Adigal Salai bus corridor.",
    },
    {
      id: "pdy-mudaliarpet",
      caseId: "4674",
      label: "Mudaliarpet",
      station: "Ariyankuppam & Mudaliarpet PS (PDY-S08)",
      lon: 79.8115,
      lat: 11.9155,
      variant: "crime",
      date: "10 Oct 2026",
      status: "Closed",
      category: "Vehicle Theft",
      description:
        "Cuddalore Road corridor two-wheeler theft reports cross-matched with ANPR camera checkpoints.",
    },
    {
      id: "pdy-moolakulam",
      caseId: "4680",
      label: "Moolakulam",
      station: "Villianur & Oulgaret PS (PDY-S05)",
      lon: 79.7865,
      lat: 11.9235,
      variant: "station",
      date: "11 Oct 2026",
      status: "Verified",
      category: "Highway Checkpoint",
      description:
        "NH-332 junction traffic monitoring and ERSS emergency response dispatch unit.",
    },
    {
      id: "pdy-villianur",
      caseId: "4687",
      label: "Villianur",
      station: "Villianur & Oulgaret PS (PDY-S05)",
      lon: 79.7665,
      lat: 11.9145,
      variant: "crime",
      date: "11 Oct 2026",
      status: "Closed",
      category: "Traffic / Road Safety",
      description:
        "Temple car street and Villianur bypass road accident and traffic congestion cluster.",
    },
    {
      id: "pdy-ariyankuppam",
      caseId: "4693",
      label: "Ariyankuppam",
      station: "Ariyankuppam & Mudaliarpet PS (PDY-S08)",
      lon: 79.8125,
      lat: 11.8985,
      variant: "crime",
      date: "12 Oct 2026",
      status: "Active",
      category: "Property / Theft",
      description:
        "Southern coastal bridge and Ariyankuppam market incident cluster under active station review.",
    },
    {
      id: "pdy-veerampattinam",
      caseId: "4699",
      label: "Veerampattinam",
      station: "Ariyankuppam & Mudaliarpet PS (PDY-S08)",
      lon: 79.8245,
      lat: 11.9025,
      variant: "station",
      date: "12 Oct 2026",
      status: "Verified",
      category: "Coastal Outpost",
      description:
        "Coastal security and beach patrol post covering Veerampattinam and Chunnambar estuary.",
    },
  ],
  KKL: [
    {
      id: "kkl-town",
      caseId: "5102",
      label: "Karaikal Town",
      station: "Karaikal Town PS (KKL-S04)",
      lon: 79.838,
      lat: 10.9254,
      variant: "crime",
      date: "24 Sep 2026",
      status: "Closed",
      category: "Property / Theft",
      description:
        "Bharathiar Road and Karaikal bus stand commercial area theft cluster; suspect apprehended and property recovered.",
    },
    {
      id: "kkl-beach",
      caseId: "5110",
      label: "Karaikal Beach",
      station: "Karaikal Town PS (KKL-S04)",
      lon: 79.851,
      lat: 10.9195,
      variant: "station",
      date: "27 Sep 2026",
      status: "Verified",
      category: "Coastal Patrol",
      description:
        "Arasalar estuary and Karaikal Beach Road evening tourist safety and traffic patrol unit.",
    },
    {
      id: "kkl-kovilpathu",
      caseId: "5118",
      label: "Kovilpathu",
      station: "Karaikal Town PS (KKL-S04)",
      lon: 79.8315,
      lat: 10.9395,
      variant: "crime",
      date: "01 Oct 2026",
      status: "Active",
      category: "Traffic / Road Safety",
      description:
        "ECR highway corridor speeding and two-wheeler collision cluster near Kovilpathu junction.",
    },
    {
      id: "kkl-thalatheru",
      caseId: "5124",
      label: "Thalatheru",
      station: "Kottucherry PS (KKL-S02)",
      lon: 79.8275,
      lat: 10.9515,
      variant: "crime",
      date: "03 Oct 2026",
      status: "Closed",
      category: "Public Order",
      description:
        "Residential and railway crossing sector patrol verification completed.",
    },
    {
      id: "kkl-kottucherry",
      caseId: "5131",
      label: "Kottucherry",
      station: "Kottucherry PS (KKL-S02)",
      lon: 79.8365,
      lat: 10.9695,
      variant: "station",
      date: "05 Oct 2026",
      status: "Verified",
      category: "Station HQ",
      description:
        "Northern Karaikal border checkpost and Kottucherry coastal jurisdiction headquarters.",
    },
    {
      id: "kkl-thirunallar",
      caseId: "5139",
      label: "Thirunallar",
      station: "Thirunallar PS (KKL-S03)",
      lon: 79.7915,
      lat: 10.9265,
      variant: "crime",
      date: "07 Oct 2026",
      status: "Active",
      category: "Property / Theft",
      description:
        "Pilgrim corridor and temple town market snatching alerts during weekend peak footfall.",
    },
    {
      id: "kkl-nedungadu",
      caseId: "5145",
      label: "Nedungadu",
      station: "Nedungadu PS (KKL-S01)",
      lon: 79.7745,
      lat: 10.9645,
      variant: "station",
      date: "09 Oct 2026",
      status: "Verified",
      category: "Rural Beat HQ",
      description:
        "Western agricultural belt and inter-state border road checkpost in Nedungadu commune.",
    },
    {
      id: "kkl-neravy",
      caseId: "5152",
      label: "Neravy",
      station: "Neravy & T.R. Pattinam PS (KKL-S05)",
      lon: 79.8165,
      lat: 10.8965,
      variant: "crime",
      date: "10 Oct 2026",
      status: "Closed",
      category: "Cyber-Enabled Fraud",
      description:
        "Banking and OTP fraud awareness and investigation cluster in Neravy commune.",
    },
    {
      id: "kkl-trpattinam",
      caseId: "5159",
      label: "T.R. Pattinam",
      station: "Neravy & T.R. Pattinam PS (KKL-S05)",
      lon: 79.8345,
      lat: 10.8765,
      variant: "crime",
      date: "12 Oct 2026",
      status: "Active",
      category: "Vehicle Theft",
      description:
        "Southern highway checkpost and coastal port road surveillance alerts in T.R. Pattinam.",
    },
  ],
  MAH: [
    {
      id: "mah-town",
      caseId: "6101",
      label: "Mahe Town",
      station: "Mahe Town PS (MAH-S01)",
      lon: 75.5376,
      lat: 11.7031,
      variant: "crime",
      date: "08 Oct 2026",
      status: "Closed",
      category: "Traffic / Road Safety",
      description: "NH-66 Mahe bridge and walkway riverfront patrol log.",
    },
    {
      id: "mah-palloor",
      caseId: "6108",
      label: "Palloor",
      station: "Palloor PS (MAH-S02)",
      lon: 75.5542,
      lat: 11.7064,
      variant: "station",
      date: "11 Oct 2026",
      status: "Verified",
      category: "Station HQ",
      description: "Eastern Mahe jurisdiction and Spinners Nagar checkpoint.",
    },
  ],
  YAN: [
    {
      id: "yan-town",
      caseId: "7101",
      label: "Yanam Town",
      station: "Yanam Town PS (YAN-S01)",
      lon: 82.211,
      lat: 16.734,
      variant: "crime",
      date: "09 Oct 2026",
      status: "Closed",
      category: "Public Order",
      description: "Godavari riverfront and Yanam market patrol report.",
    },
    {
      id: "yan-mettakur",
      caseId: "7109",
      label: "Mettakur",
      station: "Mettakur PS (YAN-S02)",
      lon: 82.192,
      lat: 16.738,
      variant: "station",
      date: "12 Oct 2026",
      status: "Verified",
      category: "Station HQ",
      description: "NH-216 corridor and industrial sector outpost.",
    },
  ],
};

/** Default coastal urban camera views matching the Puducherry & Karaikal police command map. */
const REGION_CAMERA: Record<string, { center: [number, number]; zoom: number }> = {
  PDY: { center: [79.818, 11.936], zoom: 12.4 },
  KKL: { center: [79.825, 10.926], zoom: 12.1 },
  MAH: { center: [75.545, 11.704], zoom: 13.5 },
  YAN: { center: [82.205, 16.735], zoom: 13.2 },
};

function bounds(fc: FC): LngLatBoundsLike | null {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const visit = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number") {
      const [x, y] = c as [number, number];
      minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
    } else if (Array.isArray(c)) c.forEach(visit);
  };
  for (const f of fc.features) if (f.geometry?.coordinates) visit(f.geometry.coordinates);
  return Number.isFinite(minX) ? [[minX, minY], [maxX, maxY]] : null;
}

/** Cells are outlined when at least 20% of their records carry a data-quality warning. */
const DQ_SHARE = [">=", ["/", ["get", "dq_warnings"], ["max", ["get", "count"], 1]], 0.2] as never;

export type ViewBox = [number, number, number, number];

/** The API accepts a bbox within ±180/±90 with min < max; a zoomed-out or wrapped view can exceed that. */
export function clampView(west: number, south: number, east: number, north: number): ViewBox {
  const lon = (v: number) => Math.max(-180, Math.min(180, v));
  const lat = (v: number) => Math.max(-90, Math.min(90, v));
  if (east - west >= 360) return [-180, lat(south), 180, lat(north)];
  return [lon(west), lat(south), lon(east), lat(north)];
}

const CLICKABLE = ["points", "cells-fill", "records"];

export function MapCanvas({
  label,
  styleUrl,
  regionKey,
  cases,
  aggregation,
  categoryCodes,
  boundaries,
  records,
  layers,
  selection,
  picking,
  onSelect,
  onPick,
  onView,
  onError,
  highlight = null,
  selectedStation = null,
  fitTo = null,
}: {
  label: string;
  styleUrl: string | null;
  regionKey: string;
  cases: FC | null;
  aggregation: Aggregation;
  categoryCodes: string[];
  boundaries: { region: FC | null; station: FC | null; beat: FC | null };
  records: FC | null;
  layers: Layers;
  selection: Selection;
  picking: boolean;
  onSelect: (selection: Selection) => void;
  onPick: (point: { lon: number; lat: number }) => void;
  onView: (box: ViewBox) => void;
  onError: (failed: boolean) => void;
  /** Cases to emphasise (e.g. one station's); when set, the other cases are dimmed (PY Dashboard). */
  highlight?: FC | null;
  /** Station code whose boundary is drawn prominently. */
  selectedStation?: string | null;
  /** Geometry to fit the view to when it changes (a station or a cell). */
  fitTo?: FC | null;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const ready = useRef(false);
  const pending = useRef<(() => void)[]>([]);
  const [activeTagId, setActiveTagId] = useState<string | null>("pdy-whitetown");
  const [projectedTags, setProjectedTags] = useState<(LocalityTagSeed & { x: number; y: number })[]>([]);
  const regionKeyRef = useRef(regionKey);
  useEffect(() => {
    regionKeyRef.current = regionKey;
  });

  const syncProjectedTags = (m: MapLibreMap, rk: string) => {
    if (!ready.current) return;
    const seeds = REGION_LOCALITY_TAGS[rk] ?? [];
    const canvas = m.getCanvas?.();
    const w = canvas?.clientWidth || 1000;
    const h = canvas?.clientHeight || 600;
    const next = seeds
      .map((tag) => {
        const pt = m.project([tag.lon, tag.lat]);
        return { ...tag, x: Math.round(pt.x), y: Math.round(pt.y), visible: pt.x >= 30 && pt.x <= w - 40 && pt.y >= 30 && pt.y <= h - 30 };
      })
      .filter((t) => t.visible);
    setProjectedTags(next);
  };

  const whenReady = (fn: () => void) => {
    if (ready.current) fn();
    else pending.current.push(fn);
  };
  const handlers = useRef({ onSelect, onPick, onView, onError, picking });
  useEffect(() => {
    handlers.current = { onSelect, onPick, onView, onError, picking };
  });

  // Create the map once.
  useEffect(() => {
    if (!container.current) return;
    const cam = REGION_CAMERA[regionKey] ?? { center: [79.818, 11.936] as [number, number], zoom: 12.4 };
    const m = new MapLibreMap({
      container: container.current,
      style: styleUrl ?? DEFAULT_DARK_NAVY_STYLE,
      center: cam.center,
      zoom: cam.zoom,
      attributionControl: undefined,
      dragRotate: false,
      pitchWithRotate: false,
    });
    m.addControl(new NavigationControl({ showCompass: false }), "top-right");
    m.addControl(new ScaleControl({ unit: "metric" }), "bottom-left");
    const initLayers = () => {
      if (ready.current) return;
      try {
        if (!m.getSource("region")) {
          for (const id of ["region", "station", "beat", "cases", "records", "highlight"]) {
            m.addSource(id, { type: "geojson", data: EMPTY });
          }
        }
        if (!m.getLayer("beat-line")) {
          m.addLayer({ id: "beat-line", type: "line", source: "beat", paint: { "line-color": "#3A5F8A", "line-width": 1, "line-dasharray": [2, 2] } });
          m.addLayer({ id: "cells-fill", type: "fill", source: "cases", filter: ["==", ["geometry-type"], "Polygon"],
            paint: { "fill-color": ["interpolate", ["linear"], ["get", "count"], 1, "#1C3F60", 40, "#2A7B78"], "fill-opacity": 0.12 } });
          m.addLayer({ id: "cells-line", type: "line", source: "cases", filter: ["==", ["geometry-type"], "Polygon"],
            paint: { "line-color": ["case", DQ_SHARE, MAP_COLORS.dqOutline, "#2C4D75"], "line-width": 0.5, "line-opacity": 0.35 } });
          m.addLayer({ id: "station-line", type: "line", source: "station", paint: { "line-color": "#2E6F7E", "line-width": 1.6, "line-opacity": 0.65 } });
          m.addLayer({ id: "region-line", type: "line", source: "region", paint: { "line-color": "#4CA6A8", "line-width": 2.2, "line-opacity": 0.75 } });
          m.addLayer({ id: "points", type: "circle", source: "cases", filter: ["==", ["geometry-type"], "Point"],
            paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 4, 15, 7], "circle-color": "#E25B4B",
              "circle-stroke-color": "#FFFFFF", "circle-stroke-width": 1.2 } });
          m.addLayer({ id: "records", type: "circle", source: "records",
            paint: { "circle-radius": 7, "circle-color": MAP_COLORS.record, "circle-stroke-color": "#FFFFFF", "circle-stroke-width": 1.8 } });
          m.addLayer({ id: "selected-cell", type: "line", source: "cases", filter: ["==", ["get", "key"], ""], paint: { "line-color": "#9CE5C8", "line-width": 3 } });
          m.addLayer({ id: "selected-point", type: "circle", source: "cases", filter: ["==", ["get", "id"], ""],
            paint: { "circle-radius": 11, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#9CE5C8", "circle-stroke-width": 3 } });
          m.addLayer({ id: "highlight-fill", type: "fill", source: "highlight", filter: ["==", ["geometry-type"], "Polygon"],
            paint: { "fill-color": "#2A7B78", "fill-opacity": 0.35 } });
          m.addLayer({ id: "highlight-points", type: "circle", source: "highlight", filter: ["==", ["geometry-type"], "Point"],
            paint: { "circle-radius": 7, "circle-color": "#E25B4B", "circle-stroke-color": "#FFFFFF", "circle-stroke-width": 1.5 } });
          m.addLayer({ id: "station-selected", type: "line", source: "station", filter: ["==", ["get", "code"], ""],
            paint: { "line-color": "#9CE5C8", "line-width": 3.5 } });
          m.addLayer({ id: "selected-beat", type: "line", source: "cases", filter: ["==", ["get", "code"], ""], paint: { "line-color": "#9CE5C8", "line-width": 3 } });
        }
        ready.current = true;
        if (container.current) container.current.dataset.mapReady = "true";
        handlers.current.onError(false);
        for (const fn of pending.current.splice(0)) fn();
        syncProjectedTags(m, regionKeyRef.current);
      } catch {
        // Style is still loading; will succeed on style.load or load.
      }
    };
    m.on("style.load", initLayers);
    m.on("styledata", initLayers);
    m.on("load", initLayers);
    m.on("move", () => syncProjectedTags(m, regionKeyRef.current));
    m.on("zoom", () => syncProjectedTags(m, regionKeyRef.current));
    m.on("resize", () => syncProjectedTags(m, regionKeyRef.current));
    m.on("error", () => {
      if (!ready.current && styleUrl) handlers.current.onError(true);
    });
    for (const layer of [...CLICKABLE, "highlight-fill", "highlight-points"]) {
      m.on("mouseenter", layer, () => { m.getCanvas().style.cursor = handlers.current.picking ? "crosshair" : "pointer"; });
      m.on("mouseleave", layer, () => { m.getCanvas().style.cursor = handlers.current.picking ? "crosshair" : ""; });
    }
    m.on("click", (e: MapMouseEvent) => {
      if (handlers.current.picking) {
        handlers.current.onPick({ lon: e.lngLat.lng, lat: e.lngLat.lat });
        return;
      }
      const [hit] = m.queryRenderedFeatures(e.point, {
        layers: ["highlight-points", "highlight-fill", ...CLICKABLE],
      });
      const p = (hit?.properties ?? {}) as Record<string, unknown>;
      if (hit?.layer.id === "records") handlers.current.onSelect({ kind: "record", id: String(p.id), properties: p });
      else if (p.kind === "incident") handlers.current.onSelect({ kind: "incident", id: String(p.id) });
      else if (p.kind === "cell") handlers.current.onSelect({ kind: "cell", key: String(p.key), properties: p });
      else if (p.kind === "beat") handlers.current.onSelect({ kind: "beat", code: String(p.code), properties: p });
    });
    let moveTimer: ReturnType<typeof setTimeout> | undefined;
    m.on("moveend", () => {
      clearTimeout(moveTimer);
      moveTimer = setTimeout(() => {
        const b = m.getBounds();
        handlers.current.onView(clampView(b.getWest(), b.getSouth(), b.getEast(), b.getNorth()));
      }, 400);
    });
    map.current = m;
    return () => {
      clearTimeout(moveTimer);
      ready.current = false;
      pending.current = [];
      m.remove();
      map.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [styleUrl]);

  // Push data into sources whenever it changes (after the style has loaded).
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const push = () => {
      const set = (id: string, data: FC | null, visible: boolean) =>
        (m.getSource(id) as GeoJSONSource | undefined)?.setData((visible && data ? data : EMPTY) as never);
      set("region", boundaries.region, layers.regions);
      set("station", boundaries.station, layers.stations);
      set("beat", boundaries.beat, layers.beats);
      set("cases", cases, layers.cases);
      if (container.current) {
        container.current.dataset.caseFeatures = String(layers.cases && cases ? cases.features.length : 0);
        container.current.dataset.regionFeatures = String(boundaries.region?.features.length ?? 0);
      }
      set("records", records, layers.records);
      set("highlight", highlight, layers.cases);
      m.setPaintProperty("cells-fill", "fill-opacity", highlight ? 0.08 : 0.12);
      m.setPaintProperty("points", "circle-opacity", highlight ? 0.25 : 0.9);
      m.setFilter("station-selected", ["==", ["get", "code"], selectedStation ?? ""]);
      if (aggregation === "POINTS" && categoryCodes.length) {
        const match: unknown[] = ["match", ["get", "category_code"]];
        for (const code of categoryCodes) match.push(code, categoryColor(code, categoryCodes));
        match.push("#E25B4B");
        m.setPaintProperty("points", "circle-color", match as never);
      }
      syncProjectedTags(m, regionKeyRef.current);
    };
    whenReady(push);
  }, [cases, records, boundaries, layers, aggregation, categoryCodes, highlight, selectedStation]);

  // Move to the coastal urban view of Puducherry or Karaikal (or fit to a selected station/cell).
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    whenReady(() => {
      const target = fitTo ? bounds(fitTo) : null;
      if (target) {
        m.fitBounds(target, { padding: 48, duration: 0, maxZoom: 15 });
      } else {
        const cam = REGION_CAMERA[regionKey];
        if (cam) {
          m.jumpTo({ center: cam.center, zoom: cam.zoom });
        } else if (boundaries.region) {
          const box = bounds(boundaries.region);
          if (box) m.fitBounds(box, { padding: 32, duration: 0, maxZoom: 15 });
        }
      }
      syncProjectedTags(m, regionKey);
    });
  }, [regionKey, boundaries.region, fitTo]);

  // Selection highlight
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    whenReady(() => {
      m.setFilter("selected-cell", ["==", ["get", "key"], selection?.kind === "cell" ? selection.key : ""]);
      m.setFilter("selected-beat", ["==", ["get", "code"], selection?.kind === "beat" ? selection.code : ""]);
      m.setFilter("selected-point", ["==", ["get", "id"], selection?.kind === "incident" ? selection.id : ""]);
    });
  }, [selection]);

  useEffect(() => {
    const m = map.current;
    if (m) m.getCanvas().style.cursor = picking ? "crosshair" : "";
  }, [picking]);

  const activeTag = useMemo(() => {
    if (activeTagId === null) return null;
    const seeds = REGION_LOCALITY_TAGS[regionKey] ?? [];
    return seeds.find((t) => t.id === activeTagId) ?? seeds[0] ?? null;
  }, [regionKey, activeTagId]);

  return (
    <div className="relative h-full min-h-[540px] w-full overflow-hidden bg-[#0B192C]">
      {/* Dark-navy police map canvas */}
      <div
        ref={container}
        role="region"
        aria-label={label}
        className="crimemap-dark-map h-full min-h-[540px] w-full"
      />

      {/* Interactive SVG Pill Tags Overlay (CSP-safe SVG attributes, true colors) */}
      <svg
        aria-label="Puducherry and Karaikal Map Tags"
        className="pointer-events-none absolute inset-0 z-10 h-full w-full"
      >
        {projectedTags.map((tag) => {
          const isSelected = activeTag?.id === tag.id;
          const isCrime = tag.variant === "crime";
          const pillWidth = Math.max(76, tag.label.length * 6.8 + 26);

          if (isSelected) {
            const selWidth = Math.max(115, tag.label.length * 7.2 + 34);
            return (
              <g
                key={tag.id}
                transform={`translate(${tag.x}, ${tag.y})`}
                className="pointer-events-auto cursor-pointer"
                onClick={() => setActiveTagId(tag.id)}
              >
                {/* Glowing cyan-mint dot below callout */}
                <circle cx={0} cy={22} r={8} fill="#9CE5C8" opacity={0.35} />
                <circle cx={0} cy={22} r={5} fill="#9CE5C8" stroke="#0B192C" strokeWidth={1.5} />
                {/* Active mint-aqua callout pill */}
                <rect
                  x={-selWidth / 2}
                  y={-14}
                  width={selWidth}
                  height={26}
                  rx={6}
                  fill="#A8E6CF"
                  stroke="#FFFFFF"
                  strokeWidth={1.2}
                />
                {/* Pin icon inside active callout */}
                <circle cx={-selWidth / 2 + 12} cy={-1} r={3.5} fill="#0B192C" />
                <text
                  x={-selWidth / 2 + 22}
                  y={3}
                  fill="#0B192C"
                  fontSize="11.5"
                  fontWeight="700"
                  fontFamily="system-ui, -apple-system, sans-serif"
                >
                  {tag.label}
                </text>
              </g>
            );
          }

          return (
            <g
              key={tag.id}
              transform={`translate(${tag.x}, ${tag.y})`}
              className="pointer-events-auto cursor-pointer"
              onClick={() => setActiveTagId(tag.id)}
            >
              {/* Dark pill label to the right of the circular icon badge */}
              <rect
                x={8}
                y={-11}
                width={pillWidth}
                height={22}
                rx={11}
                fill="#15253B"
                fillOpacity={0.92}
                stroke={isCrime ? "#E25B4B" : "#9CE5C8"}
                strokeOpacity={0.45}
                strokeWidth={1}
              />
              <text
                x={20}
                y={4}
                fill="#E2E8F0"
                fontSize="11"
                fontWeight="600"
                fontFamily="system-ui, -apple-system, sans-serif"
              >
                {tag.label}
              </text>
              {/* Circular coral-red or mint-teal pin badge */}
              <circle
                cx={0}
                cy={0}
                r={12}
                fill={isCrime ? "#E25B4B" : "#9CE5C8"}
                stroke="#0B192C"
                strokeWidth={2}
              />
              {isCrime ? (
                /* Crime / Incident icon */
                <path
                  d="M0 -5 C1.8 -5 3 -3.8 3 -2 C3 -0.5 1.8 0.6 0 0.6 C-1.8 0.6 -3 -0.5 -3 -2 C-3 -3.8 -1.8 -5 0 -5 Z M-4.5 5 C-4.5 2.5 -2.5 1.5 0 1.5 C2.5 1.5 4.5 2.5 4.5 5"
                  stroke="#FFFFFF"
                  strokeWidth={1.3}
                  fill="none"
                  strokeLinecap="round"
                />
              ) : (
                /* Police Station / Shield icon */
                <path
                  d="M-4.5 -1.5 L0 -5 L4.5 -1.5 L3.5 4.5 L-3.5 4.5 Z"
                  stroke="#0B192C"
                  strokeWidth={1.4}
                  fill="none"
                  strokeLinejoin="round"
                />
              )}
            </g>
          );
        })}
      </svg>

      {/* Floating "Case Details" Card over the map (matching the reference design) */}
      {activeTag ? (
        <aside
          aria-label="Case Details"
          className="absolute right-14 top-20 z-20 w-80 rounded-xl border border-slate-700/80 bg-[#16263D]/95 p-5 text-slate-100 shadow-2xl backdrop-blur-md"
        >
          <button
            type="button"
            onClick={() => setActiveTagId(null)}
            className="mb-3 inline-flex items-center gap-1.5 rounded-md border border-slate-600/80 bg-[#1E324E] px-2.5 py-1 text-xs font-medium text-slate-200 hover:bg-slate-700"
          >
            <span>‹</span>
            <span>Back</span>
          </button>

          <h2 className="text-lg font-bold tracking-tight text-white">Case Details</h2>

          <dl className="mt-3 space-y-2.5 text-xs">
            <div className="grid grid-cols-3 gap-2">
              <dt className="text-slate-400">Case ID:</dt>
              <dd className="col-span-2 font-semibold text-slate-100">Case ID: {activeTag.caseId}</dd>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <dt className="text-slate-400">Location:</dt>
              <dd className="col-span-2 font-medium text-teal-300">
                {activeTag.label} · {activeTag.station}
              </dd>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <dt className="text-slate-400">Date:</dt>
              <dd className="col-span-2 text-slate-200">{activeTag.date}</dd>
            </div>
            <div className="grid grid-cols-3 items-center gap-2">
              <dt className="text-slate-400">Status:</dt>
              <dd className="col-span-2">
                <span className="inline-block rounded bg-emerald-600/30 px-2 py-0.5 text-[11px] font-semibold text-emerald-300 border border-emerald-500/40">
                  {activeTag.status}
                </span>
              </dd>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <dt className="text-slate-400">Category:</dt>
              <dd className="col-span-2 font-medium text-amber-300">{activeTag.category}</dd>
            </div>
            <div className="grid grid-cols-3 gap-2 pt-1">
              <dt className="text-slate-400">Description:</dt>
              <dd className="col-span-2 leading-relaxed text-slate-300">{activeTag.description}</dd>
            </div>
          </dl>
        </aside>
      ) : null}
    </div>
  );
}
