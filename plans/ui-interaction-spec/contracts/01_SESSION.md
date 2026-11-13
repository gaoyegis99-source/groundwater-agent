# Session UI Contract
用户心智：一个研究主题的持续交流空间。Session != Task。
必显：标题、最近活动、归档状态、主 Task 摘要（若有）、服务端状态投影、草稿/队列恢复状态。
操作：create/open/rename/archive/unarchive/search/paginate/continue-from-resource。
新会话只附加精确 ResourceReference（如用户选择），不继承旧 Task/Confirmation/执行授权；不自动发送。
普通咨询不创建 Task。本地队列不是服务端已接收消息。切换 project/session 必须隔离迟到 API/SSE 响应。
