import { Tab, TabList, TabPanel, Tabs as AriaTabs } from 'react-aria-components/Tabs';
import type { ReactNode } from 'react';

type TabItem<Key extends string> = {
  id: Key;
  label: string;
  panel: ReactNode;
  disabled?: boolean;
  keepMounted?: boolean;
};

type TabsProps<Key extends string> = {
  label: string;
  items: readonly TabItem<Key>[];
  selectedKey?: Key;
  defaultSelectedKey?: Key;
  onSelectionChange?: (key: Key) => void;
  activation?: 'automatic' | 'manual';
};

export function Tabs<Key extends string>({
  label, items, selectedKey, defaultSelectedKey, onSelectionChange, activation = 'automatic',
}: TabsProps<Key>) {
  return <AriaTabs
    className="gw-tabs"
    selectedKey={selectedKey}
    defaultSelectedKey={defaultSelectedKey}
    keyboardActivation={activation}
    onSelectionChange={onSelectionChange && (key => {
      const item = items.find(item => item.id === key);
      if (item) onSelectionChange(item.id);
    })}
  >
    <TabList className="gw-tab-list" aria-label={label} items={items}>
      {item => <Tab className="gw-tab" id={item.id} isDisabled={item.disabled}>{item.label}</Tab>}
    </TabList>
    {items.map(item => <TabPanel key={item.id} className="gw-tab-panel" id={item.id} shouldForceMount={item.keepMounted}>
      {item.panel}
    </TabPanel>)}
  </AriaTabs>;
}
