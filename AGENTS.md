# Lumina 项目说明

- 技术栈：React + TypeScript + Vite 前端，FastAPI + LangGraph 后端，Node.js 24 运行 pi-ai 桥接。
- Windows 环境：Node.js 24、Python 3.12；本地启动使用 `scripts/setup.ps1` 和 `scripts/start.ps1`。
- 本地检查：`npm run build`、`npm run test:bridge`、`.\.venv\Scripts\python.exe -m ruff check server tests`、`.\.venv\Scripts\python.exe -m pytest --cov=server`、`npm run test:e2e`。
- 公网部署：私有 GitHub `main` 分支连接 Render Docker Web Service；业务数据使用 Supabase PostgreSQL，图片使用私有 Supabase Storage。
- 密钥只放在本地 `.env` 或部署平台环境变量中，不提交 `.env`、数据库密码、API Key、service-role key 或 Fernet 主密钥。
- Render 免费实例会休眠，公网真实生图需要用户在“模型与订阅”中配置自己的模型凭证，或由部署者配置服务器默认模型。
