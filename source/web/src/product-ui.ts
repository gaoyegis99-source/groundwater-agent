import type { Capabilities } from './types';
import type { IconName } from './Icon';
import validateProductUiSchema from './generated/product-ui.v1.validator.js';

export type ViewerKind = 'auto' | 'table' | 'chart' | 'map' | 'image' | 'json' | 'metadata';
export interface ParameterPresentation { label: string; group: string; unit?: string; values?: Record<string, string> }
export interface ModelPresentation {
  displayName: string; description: string; applicability: string;
  inputs: Record<string, { label: string; description: string; formats: string[] }>;
  parameterOverrides: Record<string, ParameterPresentation>;
  confirmationSummary: { label: string; parameters: string[] }[];
}
export interface ProductUiProfile {
  schemaVersion: 'product-ui.v1'; productId: string; locale: string;
  brand: { productName: string; workspaceName: string; assistantName: string; welcome: string; icon: { path: 'product-icon.svg'; checksum: string; mediaType: 'image/svg+xml' } };
  examples: { id: string; title: string; prompt: string; icon: IconName; featured: boolean }[];
  models: Record<string, ModelPresentation>;
  parameterNames: Record<string, ParameterPresentation>;
  parameterStages: Record<string, { label: string; group: string }>;
  outputs: Record<string, { label: string; viewer: ViewerKind }>;
  cases: Record<string, { label: string; profiles: Record<string, string> }>;
}

let active: ProductUiProfile | null = null;
const iconNames = new Set(['chart', 'trend', 'flow', 'coverage', 'compare', 'map', 'layers', 'branch']);
const viewers = new Set(['auto', 'table', 'chart', 'map', 'image', 'json', 'metadata']);
const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('PRODUCT_UI_INVALID');
  return value as Record<string, unknown>;
};
const text = (value: unknown) => {
  if (typeof value !== 'string' || !value.trim()) throw new TypeError('PRODUCT_UI_INVALID');
  return value;
};
function exact(value: Record<string, unknown>, fields: string[]) {
  if (Object.keys(value).sort().join('\0') !== [...fields].sort().join('\0')) throw new TypeError('PRODUCT_UI_INVALID');
}
function parameter(value: unknown): ParameterPresentation {
  const item = object(value); const allowed = new Set(['label', 'group', 'unit', 'values']);
  if (Object.keys(item).some(key => !allowed.has(key))) throw new TypeError('PRODUCT_UI_INVALID');
  const result: ParameterPresentation = { label: text(item.label), group: text(item.group) };
  if (item.unit !== undefined) result.unit = text(item.unit);
  if (item.values !== undefined) result.values = Object.fromEntries(Object.entries(object(item.values)).map(([key, label]) => [key, text(label)]));
  return result;
}

export function decodeProductUi(value: unknown): ProductUiProfile {
  if (!validateProductUiSchema(value)) throw new TypeError('PRODUCT_UI_INVALID');
  const root = object(value);
  exact(root, ['schemaVersion', 'productId', 'locale', 'brand', 'examples', 'models', 'parameterNames', 'parameterStages', 'outputs', 'cases']);
  if (root.schemaVersion !== 'product-ui.v1') throw new TypeError('PRODUCT_UI_INVALID');
  const brand = object(root.brand); exact(brand, ['productName', 'workspaceName', 'assistantName', 'welcome', 'icon']);
  const icon = object(brand.icon); exact(icon, ['path', 'checksum', 'mediaType']);
  if (icon.path !== 'product-icon.svg' || icon.mediaType !== 'image/svg+xml' || !/^sha256:[0-9a-f]{64}$/.test(text(icon.checksum))) throw new TypeError('PRODUCT_UI_INVALID');
  if (!Array.isArray(root.examples) || root.examples.length < 1 || root.examples.length > 12) throw new TypeError('PRODUCT_UI_INVALID');
  const examples = root.examples.map(raw => { const item = object(raw); exact(item, ['id', 'title', 'prompt', 'icon', 'featured']); if (!iconNames.has(String(item.icon)) || typeof item.featured !== 'boolean') throw new TypeError('PRODUCT_UI_INVALID'); return { id: text(item.id), title: text(item.title), prompt: text(item.prompt), icon: item.icon as IconName, featured: item.featured }; });
  const models = Object.fromEntries(Object.entries(object(root.models)).map(([id, raw]) => {
    const item = object(raw); exact(item, ['displayName', 'description', 'applicability', 'inputs', 'parameterOverrides', 'confirmationSummary']);
    const inputs = Object.fromEntries(Object.entries(object(item.inputs)).map(([name, inputRaw]) => { const input = object(inputRaw); exact(input, ['label', 'description', 'formats']); if (!Array.isArray(input.formats) || input.formats.some(format => typeof format !== 'string')) throw new TypeError('PRODUCT_UI_INVALID'); return [name, { label: text(input.label), description: text(input.description), formats: input.formats as string[] }]; }));
    const parameterOverrides = Object.fromEntries(Object.entries(object(item.parameterOverrides)).map(([name, rawValue]) => [name, parameter(rawValue)]));
    if (!Array.isArray(item.confirmationSummary)) throw new TypeError('PRODUCT_UI_INVALID');
    const confirmationSummary = item.confirmationSummary.map(rawSummary => { const summary = object(rawSummary); exact(summary, ['label', 'parameters']); if (!Array.isArray(summary.parameters) || summary.parameters.some(name => typeof name !== 'string')) throw new TypeError('PRODUCT_UI_INVALID'); return { label: text(summary.label), parameters: summary.parameters as string[] }; });
    return [id, { displayName: text(item.displayName), description: text(item.description), applicability: text(item.applicability), inputs, parameterOverrides, confirmationSummary }];
  }));
  const parameterNames = Object.fromEntries(Object.entries(object(root.parameterNames)).map(([name, raw]) => [name, parameter(raw)]));
  const parameterStages = Object.fromEntries(Object.entries(object(root.parameterStages)).map(([name, raw]) => { const item = object(raw); exact(item, ['label', 'group']); return [name, { label: text(item.label), group: text(item.group) }]; }));
  const outputs = Object.fromEntries(Object.entries(object(root.outputs)).map(([name, raw]) => { const item = object(raw); exact(item, ['label', 'viewer']); if (!viewers.has(String(item.viewer))) throw new TypeError('PRODUCT_UI_INVALID'); return [name, { label: text(item.label), viewer: item.viewer as ViewerKind }]; }));
  const cases = Object.fromEntries(Object.entries(object(root.cases)).map(([id, raw]) => { const item = object(raw); exact(item, ['label', 'profiles']); return [id, { label: text(item.label), profiles: Object.fromEntries(Object.entries(object(item.profiles)).map(([name, label]) => [name, text(label)])) }]; }));
  return { schemaVersion: 'product-ui.v1', productId: text(root.productId), locale: text(root.locale), brand: { productName: text(brand.productName), workspaceName: text(brand.workspaceName), assistantName: text(brand.assistantName), welcome: text(brand.welcome), icon: { path: 'product-icon.svg', checksum: text(icon.checksum), mediaType: 'image/svg+xml' } }, examples, models, parameterNames, parameterStages, outputs, cases };
}

export async function loadProductUi(): Promise<ProductUiProfile> {
  const response = await fetch('/product-ui.json', { cache: 'no-store' });
  if (!response.ok || !response.headers.get('content-type')?.includes('json')) throw new TypeError('PRODUCT_UI_INVALID');
  return decodeProductUi(await response.json());
}
export function setProductUi(profile: ProductUiProfile) { active = profile; if (typeof document !== 'undefined') { document.documentElement.lang = profile.locale; document.title = profile.brand.workspaceName; } }
export function productUi() { if (!active) throw new Error('PRODUCT_UI_NOT_LOADED'); return active; }
export function verifyProductUi(capabilities: Capabilities) {
  const profile = productUi();
  const capabilityModels = capabilities.models.map(item => item.modelId).sort();
  const profileModels = Object.keys(profile.models).sort();
  if (capabilities.productId !== profile.productId || capabilityModels.join('\0') !== profileModels.join('\0')) throw new TypeError('PRODUCT_UI_IDENTITY_MISMATCH');
}
export function productIconUrl() { return `/${productUi().brand.icon.path}`; }
