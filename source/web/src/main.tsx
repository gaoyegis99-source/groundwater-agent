import '@platform/ui-boundary/workspace-tokens.css';
import { createRoot } from 'react-dom/client';
import App from './App';
import './style.css';
import { loadProductUi, setProductUi } from './product-ui';

const root = createRoot(document.getElementById('root')!);
void loadProductUi().then(profile => {
  setProductUi(profile);
  root.render(<App />);
}).catch(() => root.render(<main className="configuration-error" role="alert"><h1>产品配置不可用</h1><p>无法验证当前产品的交互配置。业务操作已停用，请检查装配候选。</p></main>));
