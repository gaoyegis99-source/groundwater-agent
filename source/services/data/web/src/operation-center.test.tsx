import { OperationCenter, operatorActivityGuidance } from "@platform/operator-ui";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "./test-setup";

const activity = (overrides: Record<string, unknown>) => ({
  service: "data",
  operationId: "operation-1",
  kind: "publication",
  state: "SUCCEEDED",
  title: "监测数据发布",
  subject: "operator@example.test",
  createdAt: "2026-11-01T01:00:00Z",
  updatedAt: "2026-11-01T01:00:00Z",
  href: "/data/?view=publication&publication=operation-1",
  details: {},
  ...overrides,
});

describe("Operator activity center", () => {
  it("translates platform states into impact and an actionable next step", () => {
    expect(operatorActivityGuidance(activity({
      kind: "quality_alert",
      state: "FAILED",
      title: "质量告警",
    }) as never)).toMatchObject({
      category: "attention",
      impact: expect.stringContaining("不应进入"),
      action: expect.stringContaining("修正源数据"),
    });
    expect(operatorActivityGuidance(activity({ state: "RUNNING" }) as never)).toMatchObject({
      category: "in-progress",
      impact: expect.stringContaining("无需重复提交"),
    });
  });

  it("loads both services immediately and prioritizes work needing attention", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("*/data/v3/activity", () => HttpResponse.json({
        generatedAt: "2026-11-01T01:06:00Z",
        items: [
          activity({
            operationId: "quality-alert-1",
            kind: "quality_alert",
            state: "FAILED",
            title: "质量告警 value-range · 监测数据",
            updatedAt: "2026-11-01T01:05:00Z",
            href: "/data/?view=catalog&version=version-1",
            details: { errorCode: "CONSTRAINT_VIOLATION" },
          }),
          activity({
            operationId: "publication-1",
            state: "READY",
            title: "监测数据发布",
            updatedAt: "2026-11-01T01:04:00Z",
          }),
          activity({ operationId: "completed-1" }),
        ],
      })),
      http.get("*/knowledge/v3/activity", () => HttpResponse.json({
        generatedAt: "2026-11-01T01:06:00Z",
        items: [activity({
          service: "knowledge",
          operationId: "import-1",
          kind: "import",
          state: "RUNNING",
          title: "知识包导入",
          updatedAt: "2026-11-01T01:03:00Z",
          href: "/knowledge/?view=import&operation=import-1",
        })],
      })),
    );

    render(<OperationCenter />);

    expect(await screen.findByText(/下一步：.*阻断性质量问题/)).toBeVisible();
    const overview = screen.getByLabelText("操作状态摘要");
    const cards = within(overview).getAllByText(/待处理|进行中|近期完成/);
    expect(cards).toHaveLength(3);
    expect(within(overview).getByText("2")).toBeVisible();
    expect(within(overview).getAllByText("1")).toHaveLength(2);

    await user.click(screen.getByText("查看全部活动（4）"));
    const technical = screen.getAllByText("技术详情")[0]!;
    await user.click(technical);
    expect(screen.getByText(/操作 ID quality-alert-1/)).toBeVisible();
    expect(screen.getByText(/错误码 CONSTRAINT_VIOLATION/)).toBeVisible();
    await waitFor(() => expect(screen.getByText(/更新于/)).toBeVisible());
  });
});
