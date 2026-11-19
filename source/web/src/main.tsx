import '@platform/ui-boundary/workspace-tokens.css';
import { Component, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './style.css';
import './ui/tokens.css';
import './ui/foundation.css';
import './ui/interaction.css';
import { Loading } from './ui/Feedback';
import { loadProductUi, setProductUi } from './product-ui';

const root = createRoot(document.getElementById('root')!);

function StartupError({ title, onRetry }: { title: string; onRetry: () => void }) {
  return <main className="configuration-error"><h1>{title}</h1><p role="alert">请检查连接后重新打开工作区。</p>
    <button type="button" onClick={onRetry}>重新加载</button>
  </main>;
}

class WorkspaceLoadBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <StartupError title="工作区加载失败" onRetry={() => window.location.reload()} /> : this.props.children;
  }
}

async function startWorkspace() {
  root.render(<main className="configuration-error"><Loading>正在读取工作区配置…</Loading></main>);
  try {
    const profile = await loadProductUi();
    setProductUi(profile);
    root.render(<WorkspaceLoadBoundary><App /></WorkspaceLoadBoundary>);
  } catch {
    root.render(<StartupError title="产品配置不可用" onRetry={() => void startWorkspace()} />);
  }
}

void startWorkspace();
