const paths = {
  plus: 'M12 5v14M5 12h14',
  'arrow-up-right': 'M6 18 18 6M6 6h12v12',
  'arrow-up': 'M12 19V5m-6 6 6-6 6 6',
  'arrow-down': 'M12 5v14m-6-6 6 6 6-6',
  'arrow-left': 'M19 12H5m6-6-6 6 6 6',
  'arrow-right': 'M5 12h14m-6-6 6 6-6 6',
  download: 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
  upload: 'M12 15V3m-5 5 5-5 5 5M4 16v5h16v-5',
  check: 'm5 12 4 4L19 6',
  pin: 'm9 3 6 6m-8 2 6 6m-5-5-4 4 4 4 4-4m1-7 4-4 2 2-4 4',
  close: 'm6 6 12 12M18 6 6 18',
  menu: 'M4 6h16M4 12h16M4 18h16',
  chat: 'M5 4h14a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H9l-6 3V6a2 2 0 0 1 2-2Z',
  folder: 'M3 7V5h7l2 3h9v12H3V7Z',
  book: 'M12 5C9 3 5 3 2 4v15c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1Zm0 0v15',
  file: 'M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8M8 16h8',
  chart: 'M4 3v17h17M8 16v-5m5 5V6m5 10v-8',
  trend: 'M3 17 9 11l4 3 8-10m-6 0h6v6',
  flow: 'M3 7c4-6 8 6 12 0s6 0 6 0M3 17c4-6 8 6 12 0s6 0 6 0',
  coverage: 'M12 2 22 12 12 22 2 12 12 2ZM8 12h8m-4-4v8',
  compare: 'M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4',
  map: 'm3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2V5Zm6-2v16m6-14v16',
  layers: 'm12 3 10 6-10 6L2 9l10-6ZM2 14l10 6 10-6',
  branch: 'M6 3v12a4 4 0 0 0 4 4h10m-4-4 4 4-4 4M6 8h7a4 4 0 0 0 4-4V2',
};

export type IconName = keyof typeof paths;

export function Icon({ name }: { name: IconName }) {
  return <svg className="app-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={paths[name]} /></svg>;
}
