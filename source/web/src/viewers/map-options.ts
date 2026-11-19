import { defaults as defaultControls } from 'ol/control/defaults';
import { defaults as defaultInteractions } from 'ol/interaction/defaults';

export function mapOptions() {
  const reducedMotion = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const duration = reducedMotion ? 0 : 140;
  return {
    controls: defaultControls({
      zoomOptions: { duration, zoomInTipLabel: '放大地图', zoomOutTipLabel: '缩小地图' },
      rotateOptions: { duration, tipLabel: '重置地图朝向' },
    }),
    interactions: defaultInteractions({ onFocusOnly: true, zoomDuration: duration }),
  };
}
