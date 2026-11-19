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

## 临时单管理员模式

需要暂时统一使用一个管理员时，在 `profiles/research.env` 中设置以下配置，
将主体替换为现有管理员的用户 ID，以继续访问该管理员的历史会话。

```dotenv
PLATFORM_RESEARCH_AUTH_REQUIRED=false
PLATFORM_LOCAL_IDENTITY_SUBJECT=<现有管理员用户 ID>
PLATFORM_LOCAL_IDENTITY_DISPLAY_NAME=管理员
PLATFORM_LOCAL_IDENTITY_ROLES=admin
```

重新装配并重建 Kernel 服务后，Gateway 与 Kernel 对所有请求使用该固定身份。
浏览器直接进入工作区，隐藏退出按钮。能够访问服务地址的人均拥有该管理员权限。
`/api/v12/capabilities` 的 `authenticationRequired` 为 `false`。

恢复分账户鉴权时，将 `PLATFORM_RESEARCH_AUTH_REQUIRED` 改为 `true` 并重建 Kernel 服务。
已有账号、密码、成员关系和会话归属继续用于身份校验。账户启停由账号管理接口维护。
