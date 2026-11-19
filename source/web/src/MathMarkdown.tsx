import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

export default function MathMarkdown({ source, components }: { source: string; components: Components }) {
  return <Markdown skipHtml remarkPlugins={[remarkGfm, remarkMath]}
    rehypePlugins={[[rehypeKatex, { trust: false, output: 'mathml', maxExpand: 200, maxSize: 16 }]]}
    components={components}>{source}</Markdown>;
}
