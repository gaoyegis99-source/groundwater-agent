import { useRef, useState } from 'react';
import { DialogTrigger } from 'react-aria-components/Dialog';
import { ApiError } from './api';
import { ErrorNotice } from './ErrorNotice';
import { Button } from './ui/Button';
import { Input } from './ui/Fields';
import { Dialog } from './ui/Overlays';

export function SessionRenameDialog({
  title,
  onRename,
  disabled = false,
}: {
  title: string;
  onRename: (title: string) => Promise<void>;
  disabled?: boolean;
}) {
  const [value, setValue] = useState(title);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const inFlight = useRef(false);
  return <DialogTrigger>
    <Button isDisabled={disabled} onPress={() => { setValue(title); setError(null); }}>重命名</Button>
    <Dialog title="重命名对话" dismissOnOutsidePress dismissDisabled={saving}>
      {({ close }) => <form className="gw-form" onSubmit={event => {
        event.preventDefault();
        const normalized = value.trim();
        if (!normalized || inFlight.current) return;
        inFlight.current = true; setSaving(true); setError(null);
        void onRename(normalized).then(close).catch(caught => {
          if (!(caught instanceof ApiError)) throw caught;
          setError(caught);
        }).finally(() => { inFlight.current = false; setSaving(false); });
      }}>
        <Input label="对话名称" value={value} onChange={event => setValue(event.target.value)}
          autoFocus required readOnly={saving} maxLength={120} />
        {error && <ErrorNotice error={error} title="名称未保存，请重试" />}
        <div className="gw-overlay-actions">
          <Button isDisabled={saving} onPress={close}>取消</Button>
          <Button type="submit" variant="primary" isDisabled={!value.trim()} isPending={saving}>保存</Button>
        </div>
      </form>}
    </Dialog>
  </DialogTrigger>;
}
