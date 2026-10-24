import * as echarts from "echarts";
import { useEffect, useRef } from "react";

interface Props {
  option: echarts.EChartsOption;
  height?: number;
  ariaLabel: string;
  onClick?: (params: any) => void;
}

/** ECharts wrapper: one instance per mount, resized with its container, disposed on unmount. */
export function EChart({ option, height = 280, ariaLabel, onClick }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    inst.current = chart;
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
      inst.current = null;
    };
  }, []);

  useEffect(() => {
    inst.current?.setOption(option, { notMerge: true, lazyUpdate: true });
  }, [option]);

  useEffect(() => {
    const chart = inst.current;
    if (!chart || !onClick) return;
    chart.on("click", onClick);
    return () => {
      chart.off("click", onClick);
    };
  }, [onClick]);

  return <div ref={ref} role="img" aria-label={ariaLabel} style={{ height, width: "100%" }} />;
}
