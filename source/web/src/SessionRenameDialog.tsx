import { useRef, useState } from 'react';
import { Button, Dialog, DialogTrigger, Heading, Modal, TextField, Label, Input } from 'react-aria-components';
import { ApiError } from './api';
import { ErrorNotice } from './ErrorNotice';

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
    <Modal isDismissable={!saving} isKeyboardDismissDisabled={saving} className="dialog-backdrop">
      <Dialog className="dialog-card" aria-label="重命名对话">
        {({ close }) => <form onSubmit={event => {
          event.preventDefault();
          const normalized = value.trim();
          if (!normalized || inFlight.current) return;
          inFlight.current = true; setSaving(true); setError(null);
          void onRename(normalized).then(close).catch(caught => {
            if (!(caught instanceof ApiError)) throw caught;
            setError(caught);
          }).finally(() => { inFlight.current = false; setSaving(false); });
        }}>
          <Heading slot="title">重命名对话</Heading>
          <TextField value={value} onChange={setValue} isRequired isDisabled={saving}>
            <Label>对话名称</Label>
            <Input autoFocus maxLength={160} />
          </TextField>
          {error && <ErrorNotice error={error} title="名称未保存，请重试" />}
          <div className="dialog-actions"><Button isDisabled={saving} onPress={close}>取消</Button><button type="submit" disabled={saving || !value.trim()}>{saving ? '正在保存…' : '保存'}</button></div>
        </form>}
      </Dialog>
    </Modal>
  </DialogTrigger>;
}
