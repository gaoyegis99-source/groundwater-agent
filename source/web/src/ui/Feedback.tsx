import type { ReactNode } from 'react';
import { Icon, type IconName } from '../Icon';

const badgeIcons = {
  success: 'check',
  warning: 'warning',
  error: 'error',
  running: 'clock',
  neutral: 'circle',
} satisfies Record<string, IconName>;

export type BadgeTone = keyof typeof badgeIcons;

export function Badge({ children, tone = 'neutral', title }: { children: ReactNode; tone?: BadgeTone | 'info' | 'danger'; title?: string }) {
  const badgeTone = tone === 'info' ? 'running' : tone === 'danger' ? 'error' : tone;
  return <span className="gw-badge" data-tone={badgeTone} title={title}>
    <Icon name={badgeIcons[badgeTone]} />{children}
  </span>;
}

export function Loading({ children }: { children: ReactNode }) {
  return <div className="gw-loading" role="status">
    <span className="gw-spinner" aria-hidden="true" />
    {children}
  </div>;
}

type StateProps = {
  title?: string;
  description: ReactNode;
  action?: ReactNode;
};

export function EmptyState({ title, description, action }: StateProps) {
  return <div className="gw-empty">
    <div role="status">
      {title && <strong>{title}</strong>}
      <p>{description}</p>
    </div>
    {action && <div className="gw-state-action">{action}</div>}
  </div>;
}

export function ErrorState({ title, description, action, children, className }: StateProps & { children?: ReactNode; className?: string }) {
  return <div className={className ? `gw-error ${className}` : 'gw-error'}>
    <div role="alert">
      <div className="gw-error-message">
        <Icon name="error" />
        <div>{title && <strong>{title}</strong>}<p>{description}</p></div>
      </div>
      {children}
    </div>
    {action && <div className="gw-state-action">{action}</div>}
  </div>;
}
