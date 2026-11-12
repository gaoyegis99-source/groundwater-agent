# Groundwater 实时监控

研究服务器的监控入口为 `http://192.168.167.87:28187/`。位于 `192.168.167.0/24` 网段的设备及已允许的跨网段地址可直接只读访问。日志和采集游标保存在独立的 SQLite 库，保留最近 24 小时。

## 运行和访问方式

宿主机用户服务名为 `groundwater-live-monitor.service`。使用 `systemctl --user status groundwater-live-monitor.service` 查看状态，使用 `journalctl --user -u groundwater-live-monitor.service` 检查采集器本身。采集器的 Docker 项目以启动参数中的 `--project` 列表为准。

研究服务器通过 `--http-lan --lan-read-only 192.168.167.0/24` 提供免登录只读访问。日志经局域网明文传输。可重复传入 `--lan-read-only`，添加其他私有网段或单个设备的 `/32` 地址。省略全部 `--lan-read-only` 参数可恢复管理员登录。若改用 HTTPS，启动时提供包含监听 IP 的证书和对应私钥。监控 SQLite 库只由运行服务的宿主机账号持有。

手动启动时，从 `source` 目录调用 `python -m scripts.operations.monitor --help` 查看参数。需要仓库锁定的 Python 工具链以及可读取该实例的 Docker 连接。监控只调用固定的 Docker 查询、日志跟随和内部健康探测。

## 排查卡住的请求

1. 查看「网关」和「Kernel 调度」两个状态。网关显示静态工作台入口是否可响应，Kernel 状态取内部就绪检查。
2. 查看「需求判断」的待领取数、处理中数和最长等待时间。等待达到 60 秒时显示告警。Worker 心跳按 Executor 报告的运行类型展示。
3. 在日志区按请求 ID、会话 ID 或运行 ID 查找已有的服务日志。可以加载更早的记录、暂停跟随或切换换行。每行的「复制」会复制原始日志文本，「复制当前日志」会加上时间和服务名，复制已显示的筛选结果。
4. 日志间断提示表示采集连接中断或旧 Docker 日志可能已轮转。未能从 Docker 读取的记录无法恢复。

停用时运行 `systemctl --user disable --now groundwater-live-monitor.service`。该操作关闭监控端口，科研工作台继续运行。
