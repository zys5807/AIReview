# AIReviewSystem — AI 交易复盘系统

本地化部署的个人交易复盘桌面应用：导入券商交割单，自动统计分析交易数据，并结合 DeepSeek 等大模型进行 AI 阶段复盘分析。数据全部保存在本地，不上传任何服务器。

> 🟢 **绿色软件 · 放心使用**：本软件为个人开发、完全绿色免安装，仅用于个人本地复盘，**不包含任何木马、后门或恶意代码**。因程序为无数字签名的 Python 打包，个别杀毒软件（如 360）可能误报，**添加信任白名单后即可正常使用**，不会影响任何系统或数据安全。

## 下载安装

- 📦 **最新版下载**：请前往 [GitHub Releases 页面](https://github.com/zys5807/AIReview/releases/latest) 下载 `AIReviewSystem-V1.007.2-清洁版.zip`
- 🚀 **快速开始**：解压后双击 `AIReviewSystem.exe` 即可运行，无需安装、无需配置环境，首次启动自动创建数据库（默认账号 `admin` / `admin123`，登录后可修改）
- 🔑 **AI 功能**：右上角账号 →【API 设置】，填入模型服务商 Key（默认 DeepSeek）即可使用 AI 复盘

## 功能特性

- 📥 **交割单导入**：兼容同花顺 / 通达信 / 东方财富 / 文华财经的 CSV 交割单格式，增量合并，避免重复导入
- 📊 **交易统计**：盈亏、胜率、盈亏比、持仓周期、手续费等分维度统计，支持分时段（早盘/午盘/夜盘）分析；**手续费计入每笔盈亏（净盈亏 = 盈亏金额 − 手续费）**，收益率/胜率/期初期末资金/回撤等全部按净额口径；**盈亏比采用平均单笔口径（平均盈利单 ÷ 平均亏损单）**，不受交易次数分布影响，未设置资金也能显示
- 💰 **资金分账管理**：按 **品种类型（A股 / 商品期货 / 数字货币）× 币种（CNY / USD）** 独立管理资金（如 A股 50万人民币、商品期货 30万人民币、数字货币 5000 USDT），未单独设置的品种自动回退通用资金；占用资金自动计算（87 个期货品种乘数表 + 东方财富每日自动同步保证金率，支持品种/合约两级参数覆盖与名称容错匹配，交易保存固化乘数/保证金率快照防历史收益率漂移），交易计划自动计算单笔/总仓位比例；数字货币占用资金 = USDT 持仓规模
- 📈 **阶段绩效指标**：按 **币种 × 业务种类** 交叉筛选计算，期初/期末资金随所选时间段动态计算（含此前交易盈亏）、按币种分别展示；平均单笔盈亏比、日平均仓位、阶段总收益率、最大回撤、最大周度回撤、卡玛比率、夏普比率（需先录入账户资金流水）
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

浏览器访问 http://localhost:5173 即可（开发者环境，客户访问端口为8000）。

### 3. 配置 AI 模型

程序内右上角账号 →【API 设置】填写：
- **接口地址**：OpenAI 兼容端点（默认 `https://api.deepseek.com`）
- **API Key**：你的模型服务商 Key
- **模型名**：如 `deepseek-chat`、`qwen-plus`

配置保存到本地数据库，优先于 `backend/.env` 文件。切换服务商只需修改接口地址与模型名。

## 数据安全

- 所有数据（交易记录、配置、上传截图）均保存在本地 `app.db` 与 `uploads/` 目录，（版本更新建议备份旧版本app.db与uploads/，再覆盖到新版本即可）
- 不收集任何使用数据，AI 分析仅将当前会话内容发送给你配置的模型服务商
- API Key 存储于本地数据库，不写入代码或日志

## 联系与反馈

如有新的意见和建议，欢迎微信联系：**zys5807**

## License

[MIT](LICENSE)
