# 模型与订阅连接

实现参考 [pi-ai README](https://github.com/earendil-works/pi/blob/main/packages/ai/README.md)，实际依赖固定为 @earendil-works/pi-ai 1.0.4。采用该版本的 provider factories、Models、CredentialStore、AuthInteraction 和 generateImages。

## 网页操作

1. 注册或登录 Lumina，打开“模型与订阅”。
2. 在模型配置弹窗选择“订阅登录”“API Key”或“自定义接口”。订阅账号通过供应商授权页面登录，不向 Lumina 提供供应商密码；授权码或完整回调地址只提交给当前授权会话。
3. 可选择“验证凭证”，通过只读接口检查连接，不触发生图。
4. 分别选择并保存默认生图模型和文本模型，然后回到工作台创作。

一个账号可保留同一供应商的多个连接。例如 OpenAI API Key 负责生图，另一个 ChatGPT 订阅连接负责提示词优化。默认模型变更仅影响新任务；已有任务记录连接 ID、版本与模型 ID。

## 后端网络与系统代理

模型请求从 Lumina 后端发出。Node 24 模型进程会读取已有的 HTTP_PROXY / HTTPS_PROXY / NO_PROXY 设置；没有显式代理环境变量时，会使用 Python 检测到的系统代理（包括 Windows 系统代理）。通过 NODE_USE_ENV_PROXY 启用 Node 原生代理支持，显式设置为 0 可禁用。代理设置改变后需重启后端。不要将 API Key 填入代理设置。

OpenAI Codex 文本请求使用 SSE，让请求经过相同的 HTTP 代理与错误分类逻辑。自定义接口仍使用固定 IP 的专用传输，保留 DNS 校验、TLS 校验和禁止跳转的限制。连接超时与网络失败会指出发生在描述优化还是内容检查阶段。

## 当前能力

| 连接 | 认证 | 在 Lumina 中的用途 |
| --- | --- | --- |
| OpenAI | API Key | 原生生图 / 编辑、文本处理 |
| OpenAI / ChatGPT | pi-ai API OAuth | 支持的文本模型；实际 scope 由 OpenAI 授权决定 |
| OpenAI Codex | pi-ai 订阅授权 | ChatGPT Plus / Pro 订阅可用的文本与视觉模型 |
| OpenRouter | API Key 或账号授权 | pi-ai 生图 / 编辑与文本模型 |
| Cloudflare Workers AI | Account ID + API Token | FLUX.1 schnell 文字生图；免费套餐有每日额度，不支持参考图编辑 |
| Anthropic | API Key 或 pi-ai 账号授权 | 支持的 Claude 文本模型 |
| Google Gemini | API Key | 文本模型 |
| GitHub Copilot | API Key 或订阅授权 | 支持的文本模型，界面默认订阅授权 |
| DeepSeek | API Key | DeepSeek 官方文本与视觉模型 |
| 通义千问 / 百炼 | API Key | Qwen 文本与视觉模型；可填写对应工作空间 Base URL |
| 自定义接口 | API Key | 用户自有的 OpenAI 兼容文本 / 视觉接口；不提供生图 |

能力来自实际 SDK 目录和本地适配，不代表账号拥有目录中所有模型。真实权限、地区可用性与订阅限制以供应商响应为准。没有代购、续费或修改供应商套餐的功能。

pi-ai 1.0.4 的图像接口目前使用 OpenRouter；本项目保留了独立的 OpenAI Images 适配，也通过 Cloudflare 官方 Workers AI REST API 接入 FLUX.1 schnell。其他订阅不自动获得生图能力。Cloudflare 连接只负责生图，提示词与内容检查须另选文本连接，例如 OpenAI Codex。验证 Cloudflare 连接只检查 Token 有效性，不消耗生图额度；Account ID 和模型权限需要首次实际生成验证。免费额度和适用模型以 [Cloudflare 官方定价](https://developers.cloudflare.com/workers-ai/platform/pricing/) 为准。

OpenAI 支持 low / medium / high 和请求尺寸；OpenRouter 通过 image_config 请求比例，实际画质与分辨率由模型决定，界面显示“由模型控制”。参考图支持依据模型 input 能力验证。

文字优化使用所选文本连接。OpenAI 生图使用其原生 Moderations 接口；OpenRouter 使用所选或默认文本模型执行内容判断，再交由图片供应商处理。文字处理同样可能消耗供应商额度；不提供货币钱包或费用上限保证。

## 授权流程

pi-ai 在服务端驱动供应商 OAuth，通知与提示经 Lumina 私有会话转发到网页。支持授权链接、设备代码、普通输入、选择和手动回调输入。回调状态与 PKCE 由 SDK 校验；Lumina 将授权会话绑定用户与启动时的登录会话。退出登录会取消尚未完成的授权。

SDK 的某些流程使用服务器本机回调监听。桌面部署可以自动完成；远程部署或 Docker 中，浏览器最终回调无法自动到达服务端时，按页面提示粘贴完整最终回调地址。GitHub 当前允许 github.com；没有开放任意企业主机输入。

浏览器显示“Authentication successful”只证明本机收到了授权回调，不代表供应商之后的令牌交换已经成功。Lumina 分别展示登录、令牌交换、连接保存阶段，并把供应商 HTTP 状态映射成安全提示；原始响应、授权码和令牌不会返回浏览器。OpenAI 与 OpenAI Codex 订阅登录使用不同 provider；授权失败后需从 Lumina 重新开始，旧回调不能复用。OpenAI 和 Codex 共用本机 1455 回调端口，同一时刻只允许一个流程。

如果 OpenAI 令牌交换返回 `unsupported_country_region_territory`，这是授权服务器对当前网络出口地区的拒绝，不是登录回调损坏。请按 OpenAI 支持地区要求检查网络位置；若你实际位于支持地区，请向 OpenAI 支持反馈出口 IP 的地区误判，或使用所在地可用的其他模型 API Key。

连接完成后，pi-ai 在使用模型时刷新过期授权。刷新在同一连接的序列化 CredentialStore 中进行，更新通过私有 IPC 写入加密库；不会悄悄改用环境变量里的 API Key。

## 凭证与任务边界

- 每个连接都有用户归属；匿名和其他账号不能读取、验证、选择或移除。
- 公共配置 API 仅返回名称、状态、部分密钥提示和模型能力，不返回密钥或令牌。
- 凭证使用 Fernet 认证加密，载荷绑定用户、连接与供应商，不能在连接间直接移植。
- 主密钥保存于 data/credentials.key，Linux 创建权限为 0600；部署方应限制数据目录和备份的访问。
- 主密钥丢失时拒绝静默生成替代密钥，需恢复备份。未实现 KMS 或在线密钥轮换。
- 断开连接清空应用中的凭证并阻止后续步骤，不在供应商端自动撤销全部账号授权。
- 已发出的供应商请求可能仍处理，结果不明时沿用原有保守策略，不自动重试。

Node 子进程仅通过 stdin/stdout 私有 JSON 帧通信，不提供额外 HTTP 模型代理入口；不继承服务端模型环境密钥，不读取用户的 pi / Codex auth.json。浏览器只请求 FastAPI。

## 接口

| 接口 | 用途 |
| --- | --- |
| GET /api/models/config | 当前账号配置与 SDK 能力 |
| POST /api/models/connections | 保存 API Key |
| POST /api/models/connections/{id}/check | 明确发起只读验证 |
| DELETE /api/models/connections/{id} | 断开连接 |
| PUT /api/models/preferences | 保存默认模型 |
| POST /api/models/oauth | 开始供应商授权 |
| GET /api/models/oauth/{id} | 当前账号授权进度 |
| POST /api/models/oauth/{id}/answer | 提交授权步骤输入 |
| POST /api/models/oauth/{id}/callback | 将授权码或回调地址提交到该会话当前等待的手动步骤 |
| POST /api/models/oauth/{id}/cancel | 取消授权 |
| POST /api/models/oauth/reset | 清理当前账号残留的授权会话 |

自定义地址与自定义 Qwen 工作空间地址只接受公开 HTTPS 主机；服务端每次调用前重新解析 DNS，将请求固定到已验证的公网 IP，保持原主机名用于 TLS 和 Host，并拒绝重定向。修改接口继续要求登录、X-Lumina-Client 和 CSRF。

新版阿里云百炼在北京、新加坡等区域使用带 Workspace ID 的 Base URL，且 API Key 必须与区域匹配。Qwen 的 Base URL 可选填写；新建连接时建议复制百炼工作空间展示的兼容接口地址。留空时 Lumina 使用兼容的北京默认地址。[阿里云兼容接口与区域说明](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope)。

真实供应商授权尚未由代理代为执行；你需要本人在页面完成授权。自动测试使用隔离替身，SDK 图像协议使用模拟 HTTP 响应，不能替代实际账号权限与真实图像效果验收。
