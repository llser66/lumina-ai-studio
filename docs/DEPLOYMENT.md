# 部署与运营

当前公网体验服务已部署到 Render：[https://lumina-ai-studio-cfgg.onrender.com](https://lumina-ai-studio-cfgg.onrender.com)。部署使用私有 GitHub 仓库 `llser66/lumina-ai-studio` 的 `main` 分支、Docker Web Service、Supabase PostgreSQL 和私有 Storage。服务健康检查路径为 `/api/health`。

## Netlify 只读预览

Netlify 使用 `netlify.toml` 构建 `dist-preview/`。该构建会启用静态演示页面：展示 Lumina 项目介绍与内置灵感示例，不发起登录、模型配置或生图 API 请求。它适合分享作品界面，不是完整可交互的线上应用。

本地构建命令为 `npm run build:preview`。将生成的 `dist-preview/` 上传到 Netlify 的手动部署入口，或将仓库连接到 Netlify 并使用仓库中的构建设置。Netlify 的页面发布后即可把站点 URL 发给别人查看。完整版继续通过 `npm run build` 输出到 `dist/`，两个目录相互独立。

若需要登录、模型配置或真实生成，需另行部署 Python 后端与持久化数据目录；纯静态站点不包含这些服务。

## 支持的形态

单实例 Python 服务、同源 HTTPS，并需要 Node.js 24 执行 pi-ai 私有桥接。一个 Uvicorn 进程，默认两个工作协程，不可使用多个 workers 指向同一个目录。单纯发布前端不能运行 Python 工作流。Dockerfile 已包含 Node 运行时和生产 npm 依赖。

部署规格需通过实际图片与并发测试确定。本版本没有宣称 QPS、P95 或内存保证。

## Docker

复制 .env.example 为 .env，在本机填密钥，再运行 docker compose up --build -d。端口只绑定 127.0.0.1:8090，公网经 HTTPS 反向代理。没设置 DATABASE_URL 时，本机默认使用 SQLite 与本机图片目录。

命名卷 lumina-data 保存 SQLite、检查点、outputs/、uploads/、credentials.key 和稳定设备 ID。升级前整体备份，确认迁移；SQLite 当前含 schema 1 → 2 → 3 → 4。不要删除卷处理普通故障，尤其不要丢失凭证加密密钥。

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

## 零月费公网体验

这个体验方案把 Render 免费 Docker Web Service 作为应用，把 Supabase 免费 PostgreSQL 保存账号、任务与 LangGraph 检查点，并把图片放进**私有** Supabase Storage bucket。应用只通过后端访问图片，浏览器不能使用 service-role key 直接读取对象。

1. 在 Supabase 创建免费项目，区域选择靠近 Render 的 Singapore。创建名为 `lumina-assets` 的 bucket，并确认 **Public bucket 关闭**。
2. 在 Supabase 项目设置中复制 Project URL、`service_role` 密钥，以及数据库连接信息里的 **Session pooler** PostgreSQL URI。数据库密码须保存在密码管理器中；切勿发到聊天或提交到 GitHub。Render 免费实例没有持久磁盘，不要把连接串或图片写进 `/app/data`。
3. 将代码推送到私有 GitHub 仓库，在 Render 用 Blueprint 连接仓库并创建服务。`render.yaml` 选择免费计划，并自动生成稳定的 `CREDENTIAL_ENCRYPTION_KEY`。
4. 在 Render 的环境变量设置中填 `DATABASE_URL`、`SUPABASE_URL`、`SUPABASE_SERVICE_ROLE_KEY`。数据库连接用 Supabase **Session pooler** 连接串；bucket 名称保持 `lumina-assets`。不要将任何密钥放入 Git 仓库或前端变量。
5. 部署后检查 `/api/health`，注册一个新账号，添加自己的图片模型 API Key，完成一次实际生图；之后重启或重新部署，再确认登录、历史任务和图片仍存在。新云数据库是空的，需要在公开站点重新注册账号。

免费版的重要限制：Render 免费 Web Service 连续 15 分钟无访问后会休眠，恢复通常约一分钟；本机文件会在休眠、重启和部署时丢失，所以本配置已将业务数据放到云服务。Supabase 免费层当前提供 500 MB 数据库和 1 GB 文件存储；闲置一周可能暂停项目。额度、闲置策略和服务条款可能调整，体验中应限制分享范围并自行检查用量。该配置适合演示，不保证连续在线、备份或生产服务等级。

访客使用自己的 API Key 时，生成费由对应模型供应商按其账号规则结算；Supabase / Render 的免费资源由站点所有者账户提供。若后续访问量或图片存储超出免费额度，服务可能暂停或需要付费升级。
