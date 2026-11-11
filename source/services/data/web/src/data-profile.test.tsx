import { render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { decodeVersion, type Version } from "./api";
import { FieldProfile, SpatialPreviewProfile } from "./DataProfile";

const version = {
  versionId: "version-1",
  datasetId: "dataset-1",
  currentVersion: "version-1",
  publicationId: "publication-1",
  semanticId: "semantic-1",
  title: "Groundwater observations",
  descriptor: {
    rowCount: 6,
    quality: {},
    profile: {
      schemaVersion: "field-profile.v1",
      statisticsScope: "all_rows",
      sampleScope: "first_non_null_values",
      sampleLimitPerField: 5,
    },
    fields: [
      { fieldId: "f0", name: "well_id", type: "string", nullCount: 0, profile: {
        nonNullCount: 6, sampleValues: ["001", "002", "003", "004", "005"],
        numericString: { checkedCount: 6, parsedCount: 6, parseSuccessRate: 1, identifierLike: true },
      } },
      { fieldId: "f1", name: "level_text", type: "string", nullCount: 1, profile: {
        nonNullCount: 5, sampleValues: ["12.5", "13.75", "14", "15.25", "16"],
        numericString: { checkedCount: 5, parsedCount: 5, parseSuccessRate: 1, identifierLike: false, suggestedType: "float64" },
      } },
      { fieldId: "f2", name: "level", type: "double", nullCount: 1, profile: {
        nonNullCount: 5, sampleValues: [12.5, 13.75, 15.25, 16, 17.5],
        valueRange: { minimum: 12.5, maximum: 17.5 },
      } },
      { fieldId: "f3", name: "observed_at", type: "timestamp[us, tz=Asia/Shanghai]", nullCount: 0, profile: {
        nonNullCount: 6,
        sampleValues: ["2026-01-01T08:00:00+08:00"],
        valueRange: { minimum: "2026-01-01T08:00:00+08:00", maximum: "2026-01-06T08:00:00+08:00" },
        temporal: { kind: "timestamp", checkedCount: 6, parsedCount: 6, parseSuccessRate: 1, physicalTimezone: "Asia/Shanghai" },
      } },
    ],
  },
  semantics: {
    description: "Six observations", grain: null, timeRange: "2026-01-01/2026-01-06",
    timezone: "Asia/Shanghai", crs: null, verticalDatum: null, limitations: [], source: "test",
    fields: {
      f0: { meaning: "Well identifier", unit: null },
      f1: { meaning: "Unconverted water level", unit: "m" },
      f2: { meaning: "Water level", unit: "m" },
      f3: { meaning: "Observation time", unit: null },
    },
  },
  objects: [], inputVersions: [], createdAt: "2026-11-10T00:00:00Z", tags: [], project: "project-1",
} as Version;

it("explains whole-table field facts, bounded samples and conversion evidence", () => {
  render(<FieldProfile version={version} />);

  expect(screen.getByText("全量统计 · 6 行")).toBeVisible();
  expect(screen.getByText("样例为每字段最先出现的 5 个非空值，不代表值分布。")).toBeVisible();

  const identifier = screen.getByTestId("field-profile-f0");
  expect(identifier).toHaveTextContent("well_id");
  expect(identifier).toHaveTextContent("f0");
  expect(identifier).toHaveTextContent("string");
  expect(identifier).toHaveTextContent("6 / 6 (100%)");
  expect(identifier).toHaveTextContent("包含前导零，可能是编号");
  expect(identifier).not.toHaveTextContent("建议转换");

  const numericString = screen.getByTestId("field-profile-f1");
  expect(numericString).toHaveTextContent("Unconverted water level");
  expect(numericString).toHaveTextContent("单位 m");
  expect(numericString).toHaveTextContent("5 / 6 (83.3%)");
  expect(numericString).toHaveTextContent("5 / 5 可解析为数字");
  expect(numericString).toHaveTextContent("可考虑转换为 float64");

  const numeric = screen.getByTestId("field-profile-f2");
  expect(numeric).toHaveTextContent("12.5 – 17.5");
  expect(within(numeric).getAllByRole("listitem")).toHaveLength(5);

  const temporal = screen.getByTestId("field-profile-f3");
  expect(within(temporal).getByText("日期解析")).toBeVisible();
  expect(within(temporal).getAllByText("6 / 6 (100%)")).toHaveLength(2);
  expect(within(temporal).getByText("物理时区")).toBeVisible();
  expect(within(temporal).getByText("业务时区")).toBeVisible();
  expect(within(temporal).getAllByText("Asia/Shanghai")).toHaveLength(2);
  expect(temporal).toHaveTextContent("2026-01-01T08:00:00+08:00 – 2026-01-06T08:00:00+08:00");
});

it("labels spatial preview facts as sampled when the preview is truncated", () => {
  render(<SpatialPreviewProfile metadata={{
    feature_count: 150,
    geometry_types: ["Point", "MultiPoint"],
    crs: "EPSG:4326",
    bbox_wgs84: [116.2, 39.8, 116.9, 40.2],
  }} truncated />);

  const profile = screen.getByRole("region", { name: "空间画像" });
  expect(profile).toHaveTextContent("预览样本统计");
  expect(within(profile).getByText("完整要素数")).toBeVisible();
  expect(within(profile).getByText("150")).toBeVisible();
  expect(profile).toHaveTextContent("Point、MultiPoint");
  expect(profile).toHaveTextContent("EPSG:4326");
  expect(profile).toHaveTextContent("116.2, 39.8, 116.9, 40.2");
  expect(profile).toHaveTextContent("范围与几何类型仅基于当前受限预览，不代表全量空间范围");
});

it("rejects an internally inconsistent persisted profile at the UI boundary", () => {
  const invalid = {
    ...version,
    descriptor: {
      ...version.descriptor,
      profile: { ...version.descriptor.profile!, sampleLimitPerField: 1 },
    },
  };
  let failure: unknown;
  try {
    decodeVersion(invalid as Parameters<typeof decodeVersion>[0]);
  } catch (error) {
    failure = error;
  }
  expect(failure).toMatchObject({ code: "API_RESPONSE_INVALID" });
});

it("rejects a descriptor profile whose field facts are incomplete", () => {
  const { profile: _missing, ...fieldWithoutProfile } = version.descriptor.fields[0];
  const invalid = {
    ...version,
    descriptor: {
      ...version.descriptor,
      fields: [fieldWithoutProfile, ...version.descriptor.fields.slice(1)],
    },
  };

  expect(() => decodeVersion(invalid as Parameters<typeof decodeVersion>[0]))
    .toThrowError(expect.objectContaining({ code: "API_RESPONSE_INVALID" }));
});
