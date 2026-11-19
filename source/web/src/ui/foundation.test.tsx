import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import { Input, Select } from './Fields';
import { Tabs } from './Tabs';

it('associates the visible label, input unit, existing description, hint and error', () => {
  render(<>
    <p id="datum">1985 国家高程基准</p>
    <Input label="水位高程" name="head" defaultValue="86" unit="m" aria-describedby="datum"
      description="保留两位小数" error="请输入 0 到 80 之间的高程" />
    <Select label="聚合频率" name="frequency" defaultValue="month" description="按本地历月汇总">
      <option value="month">月</option>
      <option value="year">年</option>
    </Select>
  </>);
  const input = screen.getByRole('textbox', { name: '水位高程' });
  expect(input).toHaveAttribute('aria-invalid', 'true');
  expect(input).toHaveAccessibleDescription('m 1985 国家高程基准 保留两位小数 请输入 0 到 80 之间的高程');
  const select = screen.getByRole('combobox', { name: '聚合频率' });
  expect(select).toHaveAccessibleDescription('按本地历月汇总');
  expect(select).toHaveValue('month');
});

it('keeps manual tabs inactive until activation and preserves an opted-in panel draft', async () => {
  const user = userEvent.setup();
  render(<Tabs label="水位成果" activation="manual" defaultSelectedKey="chart" items={[
    { id: 'chart', label: '图形', keepMounted: true, panel: <Input label="图名" /> },
    { id: 'map', label: '地图', disabled: true, panel: <p>空间图层</p> },
    { id: 'table', label: '数值表', panel: <p>观测数据</p> },
  ]} />);
  const tabs = within(screen.getByRole('tablist', { name: '水位成果' }));
  const chart = tabs.getByRole('tab', { name: '图形' });
  const table = tabs.getByRole('tab', { name: '数值表' });
  await user.type(screen.getByRole('textbox', { name: '图名' }), '北岸井组');
  await user.click(chart);
  await user.keyboard('{ArrowRight}');
  expect(table).toHaveFocus();
  expect(chart).toHaveAttribute('aria-selected', 'true');
  await user.keyboard('{Enter}');
  expect(screen.getByRole('tabpanel', { name: '数值表' })).toHaveTextContent('观测数据');
  await user.keyboard('{Home} ');
  expect(screen.getByRole('textbox', { name: '图名' })).toHaveValue('北岸井组');
});
