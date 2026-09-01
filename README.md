# AIReviewSystem — AI 交易复盘系统

本地化部署的个人交易复盘桌面应用：导入券商交割单，自动统计分析交易数据，并结合 DeepSeek 等大模型进行 AI 阶段复盘分析。数据全部保存在本地，不上传任何服务器。

## 功能特性

- 📥 **交割单导入**：兼容同花顺 / 通达信 / 东方财富 / 文华财经的 CSV 交割单格式，增量合并，避免重复导入
- 📊 **交易统计**：盈亏、胜率、盈亏比、持仓周期、手续费等分维度统计，支持分时段（早盘/午盘/夜盘）分析
- 🤖 **AI 阶段复盘**：对接 OpenAI 兼容协议（DeepSeek / 通义千问 / Kimi / GLM 等），自动生成阶段性交易复盘报告
- 🎯 **交易计划**：盘后制定次日交易计划，支持日历视图按日期分组管理
- ⚙️ **API 设置界面化**：模型接口地址 / Key / 模型名均在界面配置并存入本地数据库，无需手工编辑配置文件
- 🖥️ **桌面应用**：PyInstaller 打包为单 exe，开箱即用，绿色免安装

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + SQLAlchemy + SQLite + uvicorn |
| 前端 | React 18 + Ant Design + Vite |
| AI 能力 | OpenAI 兼容协议（默认 DeepSeek） |
| 打包 | PyInstaller（onedir 模式） |

## 目录结构

```
AIReviewSystem/
├── backend/              # FastAPI 后端
│   ├── app/              # 应用源码（config/database/models/routers/services）
│   ├── tests/            # 测试夹具
│   ├── requirements.txt  # Python 依赖
│   └── AIReviewSystem.spec  # PyInstaller 打包配置
├── frontend/             # React 前端（Vite + Ant Design）
│   ├── src/              # 前端源码
│   └── package.json
├── .gitignore
└── README.md
```

## 本地开发运行

### 1. 启动后端（端口 8000）

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000
```

首次启动自动创建 `app.db` 数据库，默认账号 `admin` / `admin123`（登录后可改）。

### 2. 启动前端（端口 5173）

```bash
cd frontend
npm install
npm run dev
```

浏览器访问 http://localhost:5173 即可。

### 3. 配置 AI 模型

程序内右上角账号 →【API 设置】填写：
- **接口地址**：OpenAI 兼容端点（默认 `https://api.deepseek.com`）
- **API Key**：你的模型服务商 Key
- **模型名**：如 `deepseek-chat`、`qwen-plus`

配置保存到本地数据库，优先于 `backend/.env` 文件。切换服务商只需修改接口地址与模型名。

## 打包为 exe

```bash
cd backend
pyinstaller AIReviewSystem.spec --noconfirm --distpath dist_vXXX
```

打包产物在 `backend/dist_vXXX/AIReviewSystem/`。程序启动时按优先级读取配置：
1. exe 同目录 `.env`（老用户兼容）
2. `_internal/.env`（内置出厂默认值）
3. 硬编码默认值

## 数据安全

- 所有数据（交易记录、配置、上传截图）均保存在本地 `app.db` 与 `uploads/` 目录
- 不收集任何使用数据，AI 分析仅将当前会话内容发送给你配置的模型服务商
- API Key 存储于本地数据库，不写入代码或日志

## License

[MIT](LICENSE)
