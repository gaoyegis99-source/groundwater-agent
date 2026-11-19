import { useEffect, useRef, useState } from 'react';
import { Tooltip as AriaTooltip, TooltipTrigger } from 'react-aria-components/Tooltip';
import { Button } from './Button';

export function Tooltip({ label, description }: { label: string; description: string }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const tooltip = useRef<HTMLDivElement>(null);
  const hoveringTooltip = useRef(false);
  useEffect(() => {
    if (!open) return;
    const content = tooltip.current;
    function enterContent(event: PointerEvent) {
      if (event.pointerType === 'mouse') hoveringTooltip.current = true;
    }
    function leaveContent(event: PointerEvent) {
      if (event.pointerType !== 'mouse') return;
      hoveringTooltip.current = false;
      if (!trigger.current?.matches(':focus-visible') &&
          !(event.relatedTarget instanceof Node && trigger.current?.contains(event.relatedTarget))) setOpen(false);
    }
    function dismissOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }
    content?.addEventListener('pointerenter', enterContent);
    content?.addEventListener('pointerleave', leaveContent);
    document.addEventListener('keydown', dismissOnEscape, true);
    return () => {
      hoveringTooltip.current = false;
      content?.removeEventListener('pointerenter', enterContent);
      content?.removeEventListener('pointerleave', leaveContent);
      document.removeEventListener('keydown', dismissOnEscape, true);
    };
  }, [open]);
  return <TooltipTrigger isOpen={open} onOpenChange={next => { if (next || !hoveringTooltip.current) setOpen(next); }}
    delay={230} closeDelay={190} shouldCloseOnPress={false}>
    <Button ref={trigger} variant="text" size="icon" aria-label={label} onPress={() => setOpen(true)}>
      <span className="gw-tooltip-mark" aria-hidden="true">?</span>
    </Button>
    <AriaTooltip ref={tooltip} className="gw-tooltip" offset={4}>{description}</AriaTooltip>
  </TooltipTrigger>;
}
