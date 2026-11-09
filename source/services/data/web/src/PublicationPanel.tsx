import { useState } from "react";
import { Button, Dialog, DialogTrigger, Heading, Modal } from "react-aria-components";
import { errorText, pretty } from "./api";
import type { RunAction } from "./api";
import type { OutputDraft, usePublication } from "./use-publication";

type Props = {
  controller: ReturnType<typeof usePublication>;
  run: RunAction;
  formats: string[];
};

const publicationStateLabels: Record<string, string> = {
  OPEN: "正在准备",
  PREPARING: "正在校验",
  READY: "等待确认",
  COMMITTING: "正在发布",
  PUBLISHED: "发布完成",
  FAILED: "发布失败",
  ABORTED: "已中止",
};

const policyLabels: Record<string, string> = {
  FILE_ONLY: "仅保存和下载",
  FILE_QUERYABLE: "保存并支持查询",
  FILE_AND_METRICS: "保存、查询并计算指标",
};

function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}

const progressLabels = { waiting: "等待处理", hash: "正在计算 SHA-256", upload: "正在上传", complete: "上传完成" } as const;

export function PublicationPanel({ controller, run, formats }: Props) {
  const {
    publication,
    publicationId,
    error,
    files,
    outputs,
    updateOutput,
    removeOutput,
    moveOutput,
    fileValidations,
    validationBlocked,
    uploadBytes,
    totalBytes,
    uploadProgress,
    manifest,
    busy,
    notice,
    chooseFiles,
    upload,
    cancelUpload,
    startNewPublication,
    commit,
    abort,
    deadlineStopped,
    checkNow,
  } = controller;
  const [draftStep, setDraftStep] = useState<1 | 2 | 3>(1);
  const step = publicationId
    ? (publication?.state === "PUBLISHED" ? 5 : 4)
    : draftStep;
  const update = (index: number, key: keyof OutputDraft, value: string) => {
    updateOutput(index, { ...outputs[index]!, [key]: value } as OutputDraft);
  };
  const beginAgain = () => {
    startNewPublication();
    setDraftStep(1);
  };
  return (
    <section className="panel task-panel">
      <div className="panel-heading">
        <div>
          <p className="step-kicker">任务 3</p>
          <h2>发布新数据</h2>
          <p>上传文件、补充业务含义并完成核对；只有整批校验成功后才会生成不可变版本。</p>
        </div>
        <span className="safety-badge">原子发布</span>
      </div>
      <ol className="steps" aria-label="发布步骤">
        {["选择文件", "补充信息", "核对内容", "确认发布", "查看回执"].map((label, index) => (
          <li key={label} aria-current={step === index + 1 ? "step" : undefined}>{index + 1}. {label}</li>
        ))}
      </ol>
      {step === 1 && (
        <div className="upload-start">
          <label className="file-dropzone">
            <span className="dropzone-icon" aria-hidden="true">＋</span>
            <strong>选择要发布的文件</strong>
            <span>可一次选择多个文件；同一批次将整体成功或整体失败。单文件上限 {bytes(uploadBytes)}。</span>
            <input
              aria-label="选择文件"
              type="file"
              multiple
              disabled={busy}
              onChange={event => {
                chooseFiles(Array.from(event.target.files ?? []));
                setDraftStep(2);
              }}
            />
          </label>
          <aside className="guidance-card">
            <strong>发布前请确认</strong>
            <ul><li>文件来自可信来源</li><li>字段含义、单位和空值标记已明确</li><li>资料可供当前项目成员查看</li></ul>
          </aside>
        </div>
      )}
      {step === 2 && (
        <div>
          {outputs.map((output, index) => (
            <fieldset key={`${files[index]?.name}-${index}`} className="output-card">
              <legend>{files[index]?.name}</legend>
              <div className="output-card-toolbar">
                <span>{bytes(files[index]?.size ?? 0)} · 第 {index + 1}/{outputs.length} 个</span>
                <button type="button" className="secondary" aria-label={`上移 ${files[index]?.name}`} disabled={index === 0} onClick={() => moveOutput(index, -1)}>↑</button>
                <button type="button" className="secondary" aria-label={`下移 ${files[index]?.name}`} disabled={index === outputs.length - 1} onClick={() => moveOutput(index, 1)}>↓</button>
                <button type="button" className="danger" aria-label={`移除 ${files[index]?.name}`} onClick={() => removeOutput(index)}>移除</button>
              </div>
              <div className="form-grid">
                <label>输出名称<input value={output.name} onChange={event => update(index, "name", event.target.value)} /></label>
                <label>标题<input value={output.title} onChange={event => update(index, "title", event.target.value)} /></label>
                <label>格式<select value={output.format} onChange={event => update(index, "format", event.target.value)}>{formats.map(format => <option key={format}>{format}</option>)}</select></label>
                <label>使用方式<select value={output.policy} onChange={event => update(index, "policy", event.target.value)}><option value="FILE_ONLY">仅保存和下载</option><option value="FILE_QUERYABLE">保存并支持查询</option><option value="FILE_AND_METRICS">保存、查询并计算指标</option></select></label>
                <label className="wide">业务描述<textarea placeholder="说明这份数据记录了什么、适用于什么场景，以及需要注意的限制。" value={output.description} onChange={event => update(index, "description", event.target.value)} /></label>
                <label>资料来源<input placeholder="例如：现场监测、业务系统导出" value={output.source} onChange={event => update(index, "source", event.target.value)} /></label>
                <label>标签<input placeholder="多个标签用逗号分隔" value={output.tags} onChange={event => update(index, "tags", event.target.value)} /></label>
                <label>空值标记<input placeholder="例如：NA, -9999" value={output.nullValues} onChange={event => update(index, "nullValues", event.target.value)} /></label>
              </div>
              {fileValidations[index]?.blocking.map(message => <p key={message} role="alert" className="inline-error">{message}</p>)}
              {fileValidations[index]?.warnings.map(message => <p key={message} className="validation-warning">{message}</p>)}
            </fieldset>
          ))}
          <p className="batch-size" role="status">当前批次 {outputs.length} 个文件，共 {bytes(totalBytes)}。</p>
          <button className="secondary" onClick={() => setDraftStep(1)}>返回</button>
          <button disabled={outputs.length === 0 || validationBlocked} onClick={() => setDraftStep(3)}>核对发布内容</button>
        </div>
      )}
      {step === 3 && (
        <div>
          <h3>准备核对</h3>
          <table><thead><tr><th>输出</th><th>格式</th><th>策略</th><th>说明</th></tr></thead><tbody>
            {outputs.map(output => <tr key={output.name}><td>{output.title}</td><td>{output.format.toUpperCase()}</td><td>{policyLabels[output.policy] ?? output.policy}</td><td>{output.description || "未填写"}</td></tr>)}
          </tbody></table>
          <details><summary>高级：只读 Manifest</summary><pre>{pretty(manifest)}</pre></details>
          {Object.keys(uploadProgress).length > 0 && <div className="upload-progress" aria-label="逐文件上传进度">{files.map((file, index) => {
            const progress = uploadProgress[String(index)];
            const percent = progress && progress.total > 0 ? Math.round((progress.completed / progress.total) * 100) : 0;
            return <div key={`${file.name}-${index}`}><span><strong>{file.name}</strong><small>{progress ? progressLabels[progress.phase] : "等待处理"}</small></span><progress max="100" value={percent}>{percent}%</progress><em>{percent}%</em></div>;
          })}</div>}
          <button className="secondary" onClick={() => setDraftStep(2)}>返回修改</button>
          <button disabled={busy || validationBlocked} onClick={() => void run(upload)}>上传并准备</button>
        </div>
      )}
      {busy && <button className="danger" onClick={cancelUpload}>取消上传</button>}
      {notice && <p role="status" className="notice">{notice}</p>}
      {error && <p role="alert" className="inline-error">{errorText(error)}</p>}
      {deadlineStopped && <p role="status" className="notice">自动检查已停止，操作 ID 已保留。<button onClick={() => void checkNow()}>手动检查</button></p>}
      {publication && step >= 4 && (
        <div className="publication">
          <div className="result-heading">
            <div><span className={`state-badge state-${publication.state.toLowerCase()}`}>{publicationStateLabels[publication.state] ?? publication.state}</span><h3>发布进度</h3></div>
            <details><summary>操作标识</summary><code>{publication.publicationId}</code></details>
          </div>
          <p>已接收文件：{publication.uploads.length > 0 ? `${publication.uploads.length} 个` : "尚未接收"}</p>
          {publication.operations.map(operation => (
            <p key={operation.operationId}>{publicationStateLabels[operation.state] ?? operation.state} {operation.error ? String((operation.error as { code?: unknown }).code ?? "OPERATION_FAILED") : ""}</p>
          ))}
          {publication.state === "READY" && (
            <DialogTrigger>
              <Button>确认发布</Button>
              <Modal isDismissable className="modal">
                <Dialog className="dialog">
                  {({ close }) => <>
                    <Heading slot="title">提交不可变数据版本？</Heading>
                    <p>提交后会生成不可变版本和发布回执，不能覆盖原版本。</p>
                    <Button className="secondary" onPress={close}>返回核对</Button>
                    <Button onPress={() => { close(); void run(commit); }}>确认提交</Button>
                  </>}
                </Dialog>
              </Modal>
            </DialogTrigger>
          )}
          {!TERMINAL.has(publication.state) && <button className="danger" onClick={() => void run(abort)}>中止发布</button>}
          {TERMINAL.has(publication.state) && publication.state !== "PUBLISHED" && (
            <div className="terminal-actions">
              <p className="muted">此批次已经结束。服务端审计记录会保留，可以安全开始新的发布批次。</p>
              <button className="secondary" onClick={beginAgain}>开始新发布</button>
            </div>
          )}
          {publication.receipt && (
            <div>
              <h3>发布完成</h3>
              <p>下列输出已经生成不可变版本，可回到数据目录继续核对或查询。</p>
              <ul>{publication.receipt.outputs.map(output => <li key={output.versionId}>{output.name}：<code>{output.versionId}</code></li>)}</ul>
              <button className="secondary" onClick={beginAgain}>发布另一批数据</button>
              <details><summary>高级信息</summary><pre>{pretty(publication.receipt)}</pre></details>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

const TERMINAL = new Set(["PUBLISHED", "FAILED", "ABORTED"]);
