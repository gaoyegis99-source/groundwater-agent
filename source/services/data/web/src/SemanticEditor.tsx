import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { PlatformUiError, validateSemanticDraft } from "./api";
import type { Schema, Version } from "./api";

type FieldDraft = { meaning: string; unit: string };
type FormValues = {
  description: string;
  grain: string;
  timeRange: string;
  timezone: string;
  crs: string;
  verticalDatum: string;
  limitations: string;
  source: string;
  fields: Record<string, FieldDraft>;
};

function text(value: string | null | undefined): string {
  return value ?? "";
}

function defaults(version: Version): FormValues {
  return {
    description: text(version.semantics.description),
    grain: text(version.semantics.grain),
    timeRange: text(version.semantics.timeRange),
    timezone: text(version.semantics.timezone),
    crs: text(version.semantics.crs),
    verticalDatum: text(version.semantics.verticalDatum),
    limitations: (version.semantics.limitations ?? []).join("\n"),
    source: text(version.semantics.source),
    fields: Object.fromEntries(version.descriptor.fields.map(field => {
      const current = (version.semantics.fields ?? {})[field.fieldId];
      return [field.fieldId, {
        meaning: text(current?.meaning),
        unit: text(current?.unit),
      }];
    })),
  };
}

function optional(value: string): string | null {
  const normalized = value.trim();
  return normalized || null;
}

function toSemantics(value: FormValues): Schema["Semantics"] {
  return {
    description: value.description.trim(),
    grain: optional(value.grain),
    timeRange: optional(value.timeRange),
    timezone: optional(value.timezone),
    crs: optional(value.crs),
    verticalDatum: optional(value.verticalDatum),
    limitations: value.limitations.split("\n").map(item => item.trim()).filter(Boolean),
    source: value.source.trim(),
    fields: Object.fromEntries(Object.entries(value.fields).map(([fieldId, field]) => [
      fieldId,
      { meaning: optional(field.meaning), unit: optional(field.unit) },
    ])),
  };
}

function differences(current: Schema["Semantics"], draft: Schema["Semantics"]): string[] {
  const changed: string[] = [];
  for (const key of [
    "description", "grain", "timeRange", "timezone", "crs", "verticalDatum", "limitations", "source",
  ] as const) {
    if (JSON.stringify(current[key]) !== JSON.stringify(draft[key])) changed.push(key);
  }
  const currentFields = current.fields ?? {};
  const draftFields = draft.fields ?? {};
  for (const fieldId of new Set([...Object.keys(currentFields), ...Object.keys(draftFields)])) {
    if (JSON.stringify(currentFields[fieldId]) !== JSON.stringify(draftFields[fieldId])) {
      changed.push(`fields.${fieldId}`);
    }
  }
  return changed;
}

export function SemanticEditor({
  version,
  allowed,
  onSave,
}: {
  version: Version;
  allowed: boolean;
  onSave: (draft: Schema["Semantics"]) => Promise<void>;
}) {
  const form = useForm<FormValues>({ defaultValues: defaults(version) });
  const [pending, setPending] = useState<Schema["Semantics"] | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  useEffect(() => {
    form.reset(defaults(version));
    setPending(null);
    setValidation(null);
  }, [version.semanticId]);
  const changed = useMemo(
    () => pending ? differences(version.semantics, pending) : [],
    [pending, version.semantics],
  );
  const preview = form.handleSubmit(async value => {
    const draft = toSemantics(value);
    try {
      await validateSemanticDraft(version.semanticId, draft);
      setValidation(null);
      setPending(draft);
    } catch (error) {
      setValidation(error instanceof PlatformUiError
        ? `${error.code} ${error.issues.map(issue => issue.pointer).join("、")}`
        : "API_REQUEST_INVALID");
    }
  });
  return (
    <div className="semantic-editor">
      <p>空值表示尚未确认；保存会以当前语义 ID 作为 CAS 前置条件创建新修订。</p>
      <form className="form-grid" onSubmit={event => void preview(event)}>
        <label className="wide">描述<textarea {...form.register("description")} /></label>
        <label>数据粒度<input {...form.register("grain")} /></label>
        <label>时间范围<input {...form.register("timeRange")} /></label>
        <label>时区<input {...form.register("timezone")} placeholder="例如 Asia/Shanghai" /></label>
        <label>坐标参考系<input {...form.register("crs")} placeholder="例如 EPSG:4326" /></label>
        <label>垂向基准<input {...form.register("verticalDatum")} /></label>
        <label>来源<input {...form.register("source", { required: true })} /></label>
        <label className="wide">限制（每行一项）<textarea {...form.register("limitations")} /></label>
        <fieldset className="wide">
          <legend>字段含义与单位</legend>
          {version.descriptor.fields.map(field => (
            <div className="field-row" key={field.fieldId}>
              <span>{field.name}<small>{field.fieldId}</small></span>
              <label>含义<input {...form.register(`fields.${field.fieldId}.meaning`)} /></label>
              <label>单位<input {...form.register(`fields.${field.fieldId}.unit`)} /></label>
            </div>
          ))}
        </fieldset>
        <button type="submit" disabled={!allowed}>预览修订</button>
      </form>
      {!allowed && <p className="muted">当前身份没有语义修订权限。</p>}
      {validation && <p role="alert" className="inline-error">{validation}</p>}
      {pending && (
        <div className="diff" aria-label="语义修订差异">
          <h4>差异预览</h4>
          {changed.length === 0
            ? <p>草稿与当前修订没有差异。</p>
            : <ul>{changed.map(item => <li key={item}><code>{item}</code> 已修改</li>)}</ul>}
          <button
            type="button"
            disabled={changed.length === 0 || !allowed}
            onClick={() => void onSave(pending).then(() => setPending(null))}
          >
            创建语义修订
          </button>
          <button type="button" className="secondary" onClick={() => setPending(null)}>返回编辑</button>
        </div>
      )}
    </div>
  );
}
