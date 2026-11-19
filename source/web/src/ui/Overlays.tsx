import { useId, type ReactNode } from 'react';
import { Dialog as AriaDialog, Heading, type DialogRenderProps } from 'react-aria-components/Dialog';
import { Modal, ModalOverlay } from 'react-aria-components/Modal';
import { Icon } from '../Icon';
import { Button } from './Button';

type OverlayProps = {
  title: string;
  description?: ReactNode;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  dismissDisabled?: boolean;
  dismissOnOutsidePress?: boolean;
  children: ReactNode | ((controls: DialogRenderProps) => ReactNode);
};

export function Dialog(props: OverlayProps) {
  return <Overlay kind="dialog" {...props} />;
}

export function Drawer(props: OverlayProps) {
  return <Overlay kind="drawer" {...props} />;
}

function Overlay({
  kind, title, description, isOpen, onOpenChange, dismissDisabled, dismissOnOutsidePress = false, children,
}: OverlayProps & { kind: 'dialog' | 'drawer' }) {
  const descriptionId = useId();
  return <ModalOverlay
    className={`gw-overlay gw-overlay-${kind}`}
    isOpen={isOpen}
    onOpenChange={onOpenChange}
    isDismissable={dismissOnOutsidePress && !dismissDisabled}
    isKeyboardDismissDisabled={dismissDisabled}
  >
    <Modal className="gw-overlay-surface">
      <AriaDialog className="gw-overlay-dialog" aria-describedby={description ? descriptionId : undefined}>
        {controls => <>
          <header className="gw-overlay-heading">
            <Heading slot="title" level={2} className="gw-section-title">{title}</Heading>
            {kind === 'drawer' && <Button variant="text" size="icon" autoFocus={!dismissDisabled} isDisabled={dismissDisabled}
              aria-label={`关闭${title}`} onPress={controls.close}><Icon name="close" /></Button>}
          </header>
          <div className="gw-overlay-content">
            {description && <p id={descriptionId} className="gw-note">{description}</p>}
            {typeof children === 'function' ? children(controls) : children}
          </div>
        </>}
      </AriaDialog>
    </Modal>
  </ModalOverlay>;
}
