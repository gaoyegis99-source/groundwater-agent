import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { ApiError, callApi } from './api';
import { outputName } from './card-presentation';
import { inputReferences, newRequestId } from './message-routing';
import { isResourceReference } from './resource-item';
import type {
  BusinessAssetReference, BusinessObjectReference, ResourceDetail, ResourceItem, ResourceReference,
  WorkItemDetail, WorkItemMaterialCommand,
} from './types';
import { UploadDialog } from './UploadDialog';
import { useWorkItemCommand } from './use-work-item-command';
import { linkWorkItemMaterial } from './work-item-api';
import { workItemCommandCache } from './work-item-command-cache';
import { WorkItemIssue } from './WorkItemForms';
import { workItemTime } from './work-item-presentation';
import { WorkItemOtherAssets } from './WorkItemOtherAssets';

const DatabaseWorkbench = lazy(() => import('./DatabaseWorkbench').then(module => ({ default: module.DatabaseWorkbench })));

const objectLabels = { WELL: '监测井', REGION: '区域', AQUIFER: '含水层' };
const PAGE_SIZE = 12;

type Knowledge = Extract<BusinessAssetReference, { kind: 'KNOWLEDGE' }>;
type Reference = WorkItemMaterialCommand['reference'];
type View =
  | { kind: 'data'; mode: 'asset' | 'object' | 'inspect'; reference?: ResourceReference; linkId?: string }
  | { kind: 'knowledge'; pinned?: Knowledge; linkId?: string }
  | { kind: 'run' };
type ObjectSource = { item: ResourceItem; fields: NonNullable<ResourceDetail['descriptor']['fields']> };
type Selection = { reference: Reference; label: string };

function isMaterialCommand(value: Record<string, unknown>) {
  if (Object.keys(value).length !== 4 || !value.reference || typeof value.reference !== 'object' || Array.isArray(value.reference)) return false;
  const reference = value.reference as Record<string, unknown>;
  if ('objectKind' in reference) {
    return typeof reference.objectKind === 'string' && Object.hasOwn(objectLabels, reference.objectKind)
      && typeof reference.fieldId === 'string' && typeof reference.externalId === 'string' && isResourceReference(reference.source);
  }
  switch (reference.kind) {
    case 'DATA': return isResourceReference(reference.dataRef);
    case 'KNOWLEDGE': return typeof reference.documentId === 'string' && typeof reference.versionId === 'string' && typeof reference.checksum === 'string';
    case 'WORKFLOW': return typeof reference.runId === 'string';
    case 'RESULT': return typeof reference.runId === 'string' && typeof reference.outputName === 'string';
    default: return false;
  }
}

export function MaterialIdentity({ reference }: { reference: ResourceReference }) {
  return <details className="material-identity"><summary>资料身份</summary><dl>
    <div><dt>数据集</dt><dd>{reference.datasetId}</dd></div>
    <div><dt>资料版本</dt><dd>{reference.versionId}</dd></div>
    <div><dt>语义修订</dt><dd>{reference.semanticId}</dd></div>
    <div><dt>数据对象</dt><dd>{reference.objectId}</dd></div>
    <div><dt>校验和</dt><dd>{reference.checksum}</dd></div>
  </dl></details>;
}

function ObjectForm({ source, disabled, onSelect, onClose }: {
  source: ObjectSource;
  disabled: boolean;
  onSelect: (selection: Selection) => void;
  onClose: () => void;
}) {
  const [objectKind, setKind] = useState<BusinessObjectReference['objectKind']>('WELL');
  const [fieldId, setFieldId] = useState('');
  const [key, setKey] = useState('');
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => { form.current?.focus(); }, []);
  const externalId = key.trim();
  return <form ref={form} tabIndex={-1} className="work-material-form" aria-label="填写关联业务对象"
    onSubmit={event => {
      event.preventDefault();
      if (disabled || !source.fields.some(field => field.fieldId === fieldId) || !externalId) return;
      onSelect({
        reference: { objectKind, fieldId, externalId, source: inputReferences([source.item])[0] },
        label: `${objectLabels[objectKind]} · ${externalId}`,
      });
    }}>
    <h3>填写关联业务对象</h3>
    <p>来源资料 · {source.item.name}</p>
    <MaterialIdentity reference={source.item} />
    {!source.fields.length ? <p role="status">该语义修订没有可用的来源字段，请在 Data 中完善字段定义或选择其他版本。</p> : <>
      <label>对象类型<select value={objectKind} onChange={event => setKind(event.target.value as typeof objectKind)}>
        {Object.entries(objectLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <label>来源字段<select value={fieldId} onChange={event => setFieldId(event.target.value)} required>
        <option value="">选择字段</option>
        {source.fields.map(field => <option key={field.fieldId} value={field.fieldId}>{field.name} · {field.type}</option>)}
      </select></label>
      <label>业务键<input value={key} onChange={event => setKey(event.target.value)} required maxLength={256} /></label>
      <p className="work-item-meta">在所选字段和资料版本中标识此对象。</p>
    </>}
    <div className="work-item-actions">
      <button type="submit" disabled={disabled || !fieldId || !externalId}>确认关联对象</button>
      <button type="button" onClick={onClose}>取消</button>
    </div>
  </form>;
}

export function WorkItemMaterials({ detail, scope, current, dataEnabled, knowledgeEnabled, onRefresh }: {
  detail: WorkItemDetail;
  scope: string;
  current: boolean;
  dataEnabled: boolean;
  knowledgeEnabled: boolean;
  onRefresh: () => void;
}) {
  const { workItem: item } = detail;
  const cache = workItemCommandCache<WorkItemMaterialCommand>(scope, item, 'material', isMaterialCommand);
  const [restored] = useState(cache.read);
  const [view, setView] = useState<View>();
  const [objectSource, setObjectSource] = useState<ObjectSource>();
  const [selection, setSelection] = useState<Selection>();
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [feedback, setFeedback] = useState('');
  const [file, setFile] = useState<{ value: File; limit: number }>();
  const [uploadError, setUploadError] = useState<unknown>(null);
  const [checkingUpload, setCheckingUpload] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const chooserOpener = useRef<HTMLElement | null>(null);
  const confirmation = useRef<HTMLDivElement>(null);
  const uploadCheck = useRef<AbortController | null>(null);
  useEffect(() => {
    if (selection) confirmation.current?.focus();
  }, [selection]);
  useEffect(() => {
    const invalidate = () => { setSelection(undefined); setObjectSource(undefined); };
    const visible = () => { if (document.visibilityState === 'visible') invalidate(); };
    window.addEventListener('focus', invalidate);
    document.addEventListener('visibilitychange', visible);
    return () => {
      uploadCheck.current?.abort();
      window.removeEventListener('focus', invalidate);
      document.removeEventListener('visibilitychange', visible);
    };
  }, []);
  const disabled = !current || item.status === 'ARCHIVED';
  const command = useWorkItemCommand<WorkItemMaterialCommand>(
    (value, signal) => linkWorkItemMaterial(item.workItemId, value, signal),
    receipt => {
      setSelection(undefined);
      setObjectSource(undefined);
      setFeedback(receipt.changed ? '关联已保存。' : '此引用已关联。');
      onRefresh();
    },
    { restored, remember: cache.remember, onRejected: onRefresh },
  );
  const pending = command.unsettled;
  const pendingKind = pending && ('objectKind' in pending.reference ? 'OBJECT' : pending.reference.kind);
  const replayAllowed = current && (pendingKind === 'KNOWLEDGE' ? knowledgeEnabled
    : pendingKind === 'OBJECT' || pendingKind === 'DATA' || pendingKind === 'RESULT' ? dataEnabled : true);
  const occupied = !!(view || objectSource || selection || command.unsettled || file || checkingUpload);
  const restoreOpener = () => {
    requestAnimationFrame(() => {
      const opener = chooserOpener.current;
      if (opener?.isConnected && !opener.matches(':disabled')) opener.focus({ preventScroll: true });
    });
  };
  const close = () => { setView(undefined); restoreOpener(); };
  const materials = detail.assets.filter(asset => asset.reference?.kind !== 'RESULT');
  const linkId = view && 'linkId' in view ? view.linkId : undefined;
  const readableView = !linkId || [...detail.assets, ...detail.objects]
    .some(asset => asset.linkId === linkId && asset.state === 'AVAILABLE' && !!asset.reference);

  function start(next: View) {
    if (document.activeElement instanceof HTMLElement) chooserOpener.current = document.activeElement;
    command.clearError();
    setFeedback('');
    setView(next);
  }
  function submit(pending: Selection) {
    if (disabled || command.busy) return;
    setSelection(pending);
    void command.submit({
      projectId: item.projectId, requestId: newRequestId(), expectedRevision: item.revision, reference: pending.reference,
    });
  }
  async function selectUpload(selected: File) {
    if (disabled || uploadCheck.current) return;
    const controller = new AbortController();
    uploadCheck.current = controller;
    setCheckingUpload(true);
    setUploadError(null);
    try {
      const configuration = await callApi('data_capabilities', '/data/capabilities', undefined, controller.signal);
      const limit = configuration.limits.uploadBytes;
      if (configuration.project !== item.projectId || !Number.isSafeInteger(limit) || limit <= 0) {
        throw new ApiError(502, 'API_RESPONSE_INVALID');
      }
      if (!controller.signal.aborted) setFile({ value: selected, limit });
    } catch (error) {
      if (!controller.signal.aborted) setUploadError(error);
    } finally {
      if (uploadCheck.current === controller) uploadCheck.current = null;
      if (!controller.signal.aborted) setCheckingUpload(false);
    }
  }

  return <section className="work-item-section" aria-labelledby="work-item-materials">
    <h2 id="work-item-materials">资料与业务对象</h2>
    <p className="work-item-meta">关联资料可在本工作的会话中选择为计算输入。</p>
    {item.status === 'ACTIVE' && <div className="work-item-actions work-material-tools">
      {dataEnabled && <>
        <button type="button" disabled={disabled || occupied} onClick={() => start({ kind: 'data', mode: 'asset' })}>选择数据资料</button>
        <button type="button" disabled={disabled || occupied} onClick={() => fileInput.current?.click()}>
          {checkingUpload ? '正在读取上传配置…' : '上传资料'}
        </button>
        <input type="file" ref={fileInput} hidden onChange={event => {
          const selected = event.currentTarget.files?.[0];
          event.currentTarget.value = '';
          if (selected) void selectUpload(selected);
        }} />
        <button type="button" disabled={disabled || occupied} onClick={() => start({ kind: 'data', mode: 'object' })}>关联监测井、区域或含水层</button>
      </>}
      {knowledgeEnabled && <button type="button" disabled={disabled || occupied}
        onClick={() => start({ kind: 'knowledge' })}>选择知识版本</button>}
      {!!detail.workflowRuns.length && <button type="button" disabled={disabled || occupied}
        onClick={() => start({ kind: 'run' })}>选择运行或成果</button>}
    </div>}
    <WorkItemIssue error={uploadError} />
    <WorkItemIssue error={command.error} />
    {feedback && <p role="status">{feedback}</p>}
    {pending && <div className="work-item-actions">
      <span role="status">{command.busy ? '正在关联…' : '关联回执待核对。'}</span>
      {!command.busy && <button type="button" disabled={!replayAllowed}
        onClick={() => { if (replayAllowed) void command.submit(pending); }}>重试关联</button>}
    </div>}
    {!readableView && view && <div role="status">所选引用暂不可用。<button type="button" onClick={close}>关闭查看</button></div>}
    {!detail.objects.length && !materials.length && <p>暂无关联资料与业务对象。</p>}
    {!!materials.length && <ul className="work-item-items work-material-rows" aria-label="关联资料">
      {materials.slice(0, visible).map(({ reference, linkId: id, state, linkedAt }) => <li key={id}>
        {state !== 'AVAILABLE' || !reference ? <span>关联资产暂不可用</span> :
          reference.kind === 'DATA' ? <>
            <div><strong>数据资料 · {reference.dataRef.format.toUpperCase()}</strong>
              <span>版本 {reference.dataRef.versionId}</span><MaterialIdentity reference={reference.dataRef} /></div>
            <button type="button" disabled={occupied || !dataEnabled} onClick={() => start({ kind: 'data', mode: 'inspect', reference: reference.dataRef, linkId: id })}>核对资料版本</button>
          </> : reference.kind === 'KNOWLEDGE' ? <>
            <div><strong>知识资料</strong><span>版本 {reference.versionId}</span>
              <details className="material-identity"><summary>文档身份</summary><p>{reference.documentId}</p><code>{reference.checksum}</code></details></div>
            <button type="button" disabled={occupied || !knowledgeEnabled} onClick={() => start({ kind: 'knowledge', pinned: reference, linkId: id })}>核对知识版本</button>
          </> : reference.kind === 'WORKFLOW' ? <div><strong>专业计算记录</strong><span>{reference.workflowRef.id} · {reference.workflowRef.version}</span>
            <details className="material-identity"><summary>运行身份</summary><code>{reference.runId}</code></details></div>
            : <div><strong>{outputName(reference.outputName)}</strong><MaterialIdentity reference={reference.dataRef} /></div>}
        {state === 'AVAILABLE' && reference && <time dateTime={linkedAt}>关联于 {workItemTime(linkedAt)}</time>}
      </li>)}
    </ul>}
    {!!detail.objects.length && <><h3>业务对象</h3><ul className="work-item-items work-material-rows">
      {detail.objects.slice(0, visible).map(({ reference, linkId: id, state }) => <li key={id}>
        {state !== 'AVAILABLE' || !reference ? <span>关联对象暂不可用</span> : <>
          <div><strong>{objectLabels[reference.objectKind]} · {reference.externalId}</strong>
            <span>业务键来源字段 {reference.fieldId}</span><MaterialIdentity reference={reference.source} /></div>
          <button type="button" disabled={occupied || !dataEnabled} onClick={() => start({ kind: 'data', mode: 'inspect', reference: reference.source, linkId: id })}>核对对象来源</button>
        </>}
      </li>)}
    </ul></>}
    {Math.max(detail.objects.length, materials.length) > visible &&
      <button type="button" onClick={() => setVisible(value => value + PAGE_SIZE)}>显示更多关联引用</button>}
    {readableView && view && view.kind !== 'data' && <WorkItemOtherAssets mode={view.kind} project={item.projectId} detail={detail}
      pinnedKnowledge={view.kind === 'knowledge' ? view.pinned : undefined} disabled={disabled}
      onClose={close} onSelect={reference => {
        setSelection({ reference, label: reference.kind === 'KNOWLEDGE' ? `知识版本 ${reference.versionId}`
          : reference.kind === 'RESULT' ? outputName(reference.outputName) : '专业计算记录' });
        close();
      }} />}
    {readableView && view?.kind === 'data' && <Suspense fallback={<p role="status">正在加载数据库…</p>}>
      <button className="drawer-shade" type="button" data-open="true" tabIndex={-1} aria-hidden="true" data-overlay-dismiss onClick={close} />
      <DatabaseWorkbench project={item.projectId} initialId={view.reference?.versionId} reference={view.reference}
        inspectMetadata chooseLabel={view.mode === 'object' ? '选择此对象来源' : '选择此资料版本'} onClose={close}
        onChoose={view.mode === 'inspect' || disabled ? undefined : (resource, version) => {
          if (view.mode === 'object') setObjectSource({
            item: resource,
            fields: (version.descriptor.fields ?? []).filter(field => Object.hasOwn(version.semantics.fields ?? {}, field.fieldId)),
          });
          else setSelection({ reference: { kind: 'DATA', dataRef: inputReferences([resource])[0] }, label: resource.name });
          close();
        }} />
    </Suspense>}
    {file && <UploadDialog file={file.value} maxBytes={file.limit} storageScope={scope} project={item.projectId} submitLabel="上传并核对"
      onClose={() => setFile(undefined)} onUploaded={reference => {
        setFile(undefined);
        start({ kind: 'data', mode: 'asset', reference });
      }} />}
    {objectSource && !selection && <ObjectForm source={objectSource} disabled={disabled} onSelect={submit}
      onClose={() => { setObjectSource(undefined); restoreOpener(); }} />}
    {selection && !command.unsettled && <div ref={confirmation} tabIndex={-1} className="work-material-form">
      <strong>关联 {selection.label}</strong>
      {'dataRef' in selection.reference && <MaterialIdentity reference={selection.reference.dataRef} />}
      <div className="work-item-actions">
        <button type="button" disabled={disabled || command.busy} onClick={() => submit(selection)}>
          {'objectKind' in selection.reference ? '确认关联对象' : '确认关联资料'}
        </button>
        <button type="button" onClick={() => {
          setSelection(undefined); setObjectSource(undefined); command.clearError(); restoreOpener();
        }}>取消</button>
      </div>
    </div>}
  </section>;
}
