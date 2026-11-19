export function observeChartSize(host: HTMLElement, resize: () => void) {
  if (typeof ResizeObserver === 'undefined') return;
  let width = host.clientWidth;
  let height = host.clientHeight;
  const observer = new ResizeObserver(() => {
    const nextWidth = host.clientWidth;
    const nextHeight = host.clientHeight;
    if (!nextWidth || !nextHeight || (width === nextWidth && height === nextHeight)) return;
    width = nextWidth;
    height = nextHeight;
    resize();
  });
  observer.observe(host);
  return () => observer.disconnect();
}

export function scientificChartTheme(host: HTMLElement) {
  const style = getComputedStyle(host);
  const token = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  const textStyle = {
    fontFamily: token('--gw-font-ui', 'sans-serif'),
    fontSize: 12,
    color: token('--gw-chart-text', '#3c525b'),
  };
  const gridColor = token('--gw-chart-grid', '#cdd7d5');
  const lineColor = token('--gw-control-border', '#70827e');
  const surface = token('--gw-surface', '#fff');
  return {
    textStyle,
    palette: [
      token('--gw-series-1', '#116e69'),
      token('--gw-series-2', '#925529'),
      token('--gw-series-3', '#65558c'),
      token('--gw-series-4', '#375f88'),
      token('--gw-action', '#244a68'),
      token('--gw-running-ink', '#354c91'),
    ],
    reference: token('--gw-text-muted', '#54666d'),
    axis: {
      axisLabel: { ...textStyle, hideOverlap: true },
      axisLine: { lineStyle: { color: lineColor } },
      axisTick: { lineStyle: { color: lineColor } },
    },
    splitLine: { lineStyle: { color: gridColor, type: 'dashed' as const } },
    tooltip: { textStyle, backgroundColor: surface, borderColor: gridColor, borderWidth: 1 },
  };
}
