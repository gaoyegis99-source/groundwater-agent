# 配置与密钥

源码树不读取 `.env`。当前 OS 用户在 `$XDG_CONFIG_HOME/groundwater-v6` 中拥有一个配置域：

```text
profiles/research.env
secrets/research/agent-api-key
secrets/research/intent-router-api-key
secrets/research/data-db-password
secrets/research/knowledge-db-password
secrets/research/groundwater-sustainability-api-key
secrets/research/groundwater-sustainability-ca-bundle
```

项目配置根为 `0700`；profile 和 secret 为 `0600` 或 `0400`，属于当前用户且不能是符号链接。
secret 不得出现在 Compose environment、URL、plan、receipt、Activity、归档或日志中。
第三方模型只在 Product Manifest v8 中保存 URL/host 环境变量名和 secret 引用；
`remote_http` Worker 读取挂载文件，算子、Workflow、Model 与 Agent 参数不得携带这些值。

```bash
../bin/gw config status --profile research
../bin/gw auth bootstrap-admin
../bin/gw doctor
```

浏览器使用本地密码会话与 CSRF 防护，API 使用只保存摘要的个人 Token。服务间身份由 Kernel
签发的短期内部断言传递；浏览器提供的主体或角色 header 不会直接转发。可选外部投影地址不能
包含凭据，未配置时不影响核心运行。
