"use client";

import { BarChart, HeatmapChart, LineChart } from "echarts/charts";
import { AriaComponent, CalendarComponent, GridComponent, LegendComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import { type EChartsCoreOption, init, use as registerEcharts } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

registerEcharts([LineChart, BarChart, HeatmapChart, GridComponent, TooltipComponent, LegendComponent, VisualMapComponent, CalendarComponent, AriaComponent, CanvasRenderer]);

/**
 * Apache ECharts (spec) on a canvas. Tooltips are rendered as rich text inside the canvas, so no HTML
 * with inline styles is injected (strict CSP) and no data label is ever parsed as HTML. ECharts'
 * ARIA description is enabled, and every chart has a visible title and the same numbers in the
 * Table view.
 */
export function EChart({ title, subtitle, option, height = 280 }: { title: string; subtitle?: string; option: EChartsCoreOption; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = init(el, undefined, { renderer: "canvas" });
    chart.setOption({
      aria: { enabled: true, label: { enabled: true, general: { withTitle: `${title}.`, withoutTitle: `${title}.` } } },
      animation: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      textStyle: { fontFamily: "inherit" },
      tooltip: { renderMode: "richText", confine: true },
      ...option,
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [option, title]);
  return (
    <figure className="flex flex-col gap-1">
      <figcaption>
        <span className="block text-sm font-semibold text-navy">{title}</span>
        {subtitle ? <span className="block text-xs">{subtitle}</span> : null}
      </figcaption>
      {/* ECharts writes its own ARIA description (series and values) onto this element. */}
      <div ref={ref} style={{ height }} className="w-full" />
    </figure>
  );
}
