# 部署与运营

## Netlify 只读预览

Netlify 使用 `netlify.toml` 构建 `dist-preview/`。该构建会启用静态演示页面：展示 Lumina 项目介绍与内置灵感示例，不发起登录、模型配置或生图 API 请求。它适合分享作品界面，不是完整可交互的线上应用。

本地构建命令为 `npm run build:preview`。将生成的 `dist-preview/` 上传到 Netlify 的手动部署入口，或将仓库连接到 Netlify 并使用仓库中的构建设置。Netlify 的页面发布后即可把站点 URL 发给别人查看。完整版继续通过 `npm run build` 输出到 `dist/`，两个目录相互独立。

若需要登录、模型配置或真实生成，需另行部署 Python 后端与持久化数据目录；纯静态站点不包含这些服务。

## 支持的形态

单实例 Python 服务、持久化磁盘、同源 HTTPS，并需要 Node.js 24 执行 pi-ai 私有桥接。一个 Uvicorn 进程，默认两个工作协程，不可使用多个 workers 指向同一个目录。单纯发布前端不能运行 Python 工作流。Dockerfile 已包含 Node 运行时和生产 npm 依赖。

部署规格需通过实际图片与并发测试确定。本版本没有宣称 QPS、P95 或内存保证。

## Docker

复制 .env.example 为 .env，在本机填密钥，再运行 docker compose up --build -d。端口只绑定 127.0.0.1:8090，公网经 HTTPS 反向代理。

命名卷 lumina-data 保存 lumina.sqlite3、checkpoints.sqlite3、WAL、outputs/、uploads/、credentials.key 和稳定设备 ID。升级前整体备份，确认迁移；当前含 schema 1 → 2 → 3 → 4。不要删除卷处理普通故障，尤其不要丢失凭证加密密钥。

APP_ORIGINS=https://你的域名，COOKIE_SECURE=true。修改配置后重启。网页连接标记仅说明密钥存在，真实模型权限与余额仍需验收。

开放注册前降低 GLOBAL_DAILY_GENERATION_LIMIT 并设置供应商消费预算。也可创建自己的账号后关闭 REGISTRATION_ENABLED，作为受控个人应用。未提供邮箱验证、密码找回、支付、管理后台或团队空间。

反向代理上传上限至少 8 MB，SSE 关闭缓冲并允许长连接。生产存储卷与备份必须限制访问。

## 人工核对

排队可取消并释放次数；开始后不取消远端计费请求。failed、cancelled 不占次数；succeeded、uncertain 保留次数。

uncertain 先人工核对供应商记录。命令不访问供应商、不产生新费用：

~~~powershell
# 已核对并取得真实原图
.\.venv\Scripts\python.exe -m server.ops 任务UUID --image "C:\verified-output.png" --note "已核对供应商输出"
# 仅在确认没有可用图片后释放次数
.\.venv\Scripts\python.exe -m server.ops 任务UUID --confirmed-no-image --note "已人工核对供应商结果"
~~~

核对结论进入事件记录。note 中不得包含密钥或敏感供应商错误。无法确认时保留 uncertain。

## 保留与恢复

停止应用后整体备份 data 目录 / 命名卷，同时包括数据库、WAL 和图片。先恢复至隔离目录验证，再启用服务。本版本无自动备份、永久擦除或存储回收工具。

移出作品库为软删除，审计记录、原图、参考图与检查点继续保留。公网运营前需选择保留周期与账号删除流程。

## 真实验收

1. 注册，检查匿名与跨账号不能读取私有作品。
2. 用有效密钥，进行一张 low 画质、关闭优化的真实生成。
3. 刷新、下载原图，检查任务与时间线。
4. 基于此图编辑一张新作品，检查各自保存。
5. 在供应商账户核对实际请求与费用。
6. 公网部署后重复验证，再将地址写入简历。

模型配置与订阅授权操作见 MODEL_CONNECTIONS.md。本地回调在远程部署中可能需要手动粘贴最终地址；原生 SDK 授权回调端口不作为公网服务暴露。Cookie 会话、授权状态与主密钥都必须按私有数据保护。

## Render 公网部署

根目录 `render.yaml` 定义单实例 Docker Web Service、`/app/data` 持久磁盘、健康检查与 HTTPS 会话配置。先把源码推送到私有 GitHub 仓库，再让 Render 连接该仓库并创建服务。Render 会为 `APP_ORIGINS` 自动注入本服务的 `RENDER_EXTERNAL_URL`；不需要把本机 `.env` 或 API Key 上传到 GitHub。访客注册后需自行添加支持生图的模型连接，才能真实生图。

该配置使用付费计算实例和持久磁盘。磁盘只能增大，不能缩小；同一磁盘只能供单实例使用。首次上线后验证 `/api/health`、注册、登录、模型连接、实际生图和图片持久化，再分享站点地址。`/app/data` 是唯一持久化路径，应按上文定期备份。
