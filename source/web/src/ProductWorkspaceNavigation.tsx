import { useEffect, useRef, type ReactNode } from 'react';
import { Icon, type IconName } from './Icon';
import { Loading } from './ui/Feedback';
import { workItemHref } from './work-item-route';
import './product-workspace-navigation.css';

const directoryTitles = { data: '数据库', knowledge: '知识与方法', capabilities: '能力目录' };
export type ProductDirectory = keyof typeof directoryTitles;
export type ProductDestination = ProductDirectory | 'work' | 'home';

export function readProductDirectory(hash: string): ProductDirectory | null {
  const key = hash.slice(2);
  return hash.startsWith('#/') && Object.hasOwn(directoryTitles, key) ? key as ProductDirectory : null;
}

const destinations: readonly { key: ProductDestination; label: string; href: string; icon: IconName }[] = [
  { key: 'work', label: '业务工作', href: workItemHref(), icon: 'file' },
  { key: 'data', label: directoryTitles.data, href: '#/data', icon: 'folder' },
  { key: 'knowledge', label: directoryTitles.knowledge, href: '#/knowledge', icon: 'book' },
  { key: 'capabilities', label: directoryTitles.capabilities, href: '#/capabilities', icon: 'layers' },
  { key: 'home', label: '对话首页', href: '#/', icon: 'chat' },
];

export function ProductNavigation({ current, available }: {
  current: ProductDestination | null;
  available: ReadonlySet<ProductDestination>;
}) {
  return <nav className="product-navigation" aria-label="工作台导航">
    {destinations.filter(item => available.has(item.key)).map(({ key, label, href, icon }) =>
      <a key={key} href={href} aria-current={current === key ? 'page' : undefined} aria-label={label} title={label}>
        <Icon name={icon} /><span>{label}</span>
      </a>)}
  </nav>;
}

export function ProductDirectoryPage({ directory, navigation, signOut, brand, mark, status, retry, children }: {
  directory: ProductDirectory;
  navigation: ReactNode;
  signOut: ReactNode;
  brand: string;
  mark: string;
  status: 'loading' | 'error' | 'unavailable' | 'ready';
  retry: () => void;
  children: ReactNode;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, [directory]);
  return <div className="product-directory-shell">
    <a className="main-content-skip" href="#product-directory-heading" onClick={event => {
      event.preventDefault();
      heading.current?.focus();
    }}>跳到主要内容</a>
    <header className="product-directory-header">
      <a className="product-directory-brand" href="#/"><img src={mark} width={28} height={28} alt="" />{brand}</a>
      {navigation}
      {signOut}
    </header>
    <main className="product-directory-main" aria-labelledby="product-directory-heading" aria-busy={status === 'loading'}>
      <h1 id="product-directory-heading" tabIndex={-1} ref={heading}>{directoryTitles[directory]}</h1>
      {status === 'loading' && <Loading>正在连接工作区…</Loading>}
      {status === 'error' && <div role="alert"><p>目录暂时无法连接。</p><button onClick={retry}>重试连接</button></div>}
      {status === 'unavailable' && <p role="status">当前项目未配置此目录。</p>}
      {status === 'ready' && children}
    </main>
  </div>;
}
