import { Button as AriaButton, type ButtonProps as AriaButtonProps } from 'react-aria-components/Button';
import type { Ref } from 'react';

type ButtonProps = Omit<AriaButtonProps, 'className'> & {
  className?: string;
  variant?: 'primary' | 'secondary' | 'text' | 'danger';
  size?: 'compact' | 'standard' | 'large' | 'icon';
  ref?: Ref<HTMLButtonElement>;
};

export function Button({ variant = 'secondary', size = 'standard', className, children, ...props }: ButtonProps) {
  return <AriaButton
    {...props}
    className={className ? `gw-button ${className}` : 'gw-button'}
    data-variant={variant}
    data-size={size}
  >
    {state => <>
      {state.isPending && <span className="gw-spinner gw-button-spinner" aria-hidden="true" />}
      {typeof children === 'function' ? children(state) : children}
    </>}
  </AriaButton>;
}
