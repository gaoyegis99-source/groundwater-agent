import { useState } from 'react';
import { Button, Dialog, DialogTrigger, Heading, Modal, TextField, Label, Input } from 'react-aria-components';

export function SessionRenameDialog({
  title,
  onRename,
}: {
  title: string;
  onRename: (title: string) => Promise<void>;
}) {
  const [value, setValue] = useState(title);
  return <DialogTrigger>
    <Button onPress={() => setValue(title)}>重命名</Button>
    <Modal isDismissable className="dialog-backdrop">
      <Dialog className="dialog-card" aria-label="重命名对话">
        {({ close }) => <form onSubmit={event => {
          event.preventDefault();
          const normalized = value.trim();
          if (!normalized) return;
          void onRename(normalized).then(close);
        }}>
          <Heading slot="title">重命名对话</Heading>
          <TextField value={value} onChange={setValue} isRequired>
            <Label>对话名称</Label>
            <Input autoFocus maxLength={160} />
          </TextField>
          <div className="dialog-actions"><Button onPress={close}>取消</Button><button type="submit" disabled={!value.trim()}>保存</button></div>
        </form>}
      </Dialog>
    </Modal>
  </DialogTrigger>;
}
