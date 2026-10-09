# 架构与关键取舍

~~~mermaid
flowchart LR
 B[浏览器 React / TypeScript] -->|同源 Cookie + CSRF| A[FastAPI]
 A --> D[(SQLite 业务数据)]
 A --> F[持久化私有图片]
 W[异步工作池] -->|事务领取 + 租约| D
 W --> G[LangGraph]
 G --> C[(SQLite checkpoints)]
 G --> P[ImageProvider]
 P --> O[OpenAI 三类 API]
 G -->|原子落盘| F
 G -->|状态与事件| D
 A -->|SSE + 轮询| B
~~~

## Graph

模型配置增加了 ModelService 与账户绑定凭证库。Python ModelRouter 按任务保存的连接与模型 ID 调度：OpenAI 原生图像仍走 Python SDK，OpenRouter 图像和多供应商文字处理走 Node 中的 pi-ai。Node 使用私有 JSON IPC，浏览器不直接访问供应商。

validate → enhance（可选）→ moderate → generate → persist → END。

通过 AsyncSqliteSaver 保存检查点，thread_id=job_id，durability=sync 在下一步前持久化已完成步骤。Graph 状态只含任务 ID、有效描述和文件路径，没有 Base64。用户、额度、取消与作品查询仍由普通后端管理。浏览器连接断开不会取消后台任务。

## 幂等与计费

BEGIN IMMEDIATE 事务中检查重复请求、用户 / 全站额度与参考图归属，再插入任务和事件。唯一约束 (user_id, idempotency_key)。相同 key + 参数返回原任务；改变参数返回 409。

浏览器把未确认编号存入 sessionStorage，刷新后相同提交继续使用该编号；收到任务后清除。主动修改参数属于新请求。

SDK max_retries=0；计费调用前保存 provider_started_at。图片返回后先 fsync、原子替换文件，再写检查点。重入图片节点时先检查原图，避免再次调用。网络超时与供应商 5xx 可能已计费，因此进入 uncertain，保留额度且不自动重试。

这不保证跨网络与供应商的 exactly-once。远端副作用和本地数据库无法共用事务，也没有假定供应商接受业务幂等键。

## 恢复与单实例

默认两个异步消费者；原子领取 queued 任务，设置 60 秒租约，每 10 秒续约。调用前中断可继续；调用后中断而没有图片时待确认；原图存在时恢复归档。归档恢复最多两次。

工作池文件锁拒绝同一数据目录启动第二个 API 进程。当前是单实例架构，不支持多主扩容。扩容时迁移 PostgreSQL、Postgres checkpointer、外部队列与私有对象存储；这些不属于已实现能力。

## 安全

凭证额外使用账户 / 连接 / 供应商绑定的 Fernet 加密。pi-ai 的 CredentialStore 在每个连接中序列化刷新，事件只通过私有进程通道进入加密存储；配置读取不解析密钥或发起授权刷新。模型连接撤销后不回退到共享服务器密钥。

Argon2 密码摘要；随机会话令牌与 CSRF 摘要在数据库中；HttpOnly、SameSite=Strict Cookie，公网需 Secure=true。每个资产与任务查询检查归属。

上传最大 8 MB，PNG / JPG / WebP，单边最多 4096 像素；实际解码并重新编码，移除元数据。不接受浏览器传入存储路径或外部图片 URL。普通请求体最大 32 KB，分块传输也受限。

修改操作验证来源允许列表、自定义请求头与 CSRF。生成限流和两级每日额度由服务端执行。默认不信任转发 IP 头。日志仅记录请求 / 任务 ID、阶段、异常类型，不记录密钥、密码、图片或供应商原始错误。

描述与图片仍属于私有存储数据。存储权限、备份、保留与永久擦除由部署运营方处理；软删除仅移出可见作品库。
