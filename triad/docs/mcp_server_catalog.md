# MCP Server 完整清单 — Triad 精选版

> 本文档汇总了 8 个最实用的开源 MCP Server，覆盖数据库、文件系统、搜索、DevOps、网络、浏览器自动化、代码执行和数据抓取 8 大核心能力。
> 每个 Server 均包含：基本信息、功能描述、安装命令、环境变量、Triad 集成价值与安全提示。

---

## 1. SQLite Database（官方）

| 项目 | 信息 |
|------|------|
| **名称** | SQLite Database |
| **仓库** | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) |
| **Stars** | 32,000+ |
| **许可证** | MIT |

### 功能描述
基于本地 SQLite 文件的关系型数据库 MCP Server，支持 SQL 查询、数据写入、表结构管理。无需外部依赖，适合单节点数据持久化、结构化记忆存储和轻量级数据分析场景。

### 安装命令
```bash
npx -y @modelcontextprotocol/server-sqlite --db-path ./data.db
```

### 环境变量
无额外环境变量需求。

### Triad 集成价值
- **记忆持久化**：支持 AI 长期记忆的本地结构化存储
- **会话状态管理**：保存跨会话的对话上下文和用户偏好
- **轻量数据分析**：执行 SQL 查询分析用户行为数据
- **零配置**：单文件数据库，无需额外服务部署

### 安全提示
- **权限范围**：默认具备对指定 `.db` 文件的完整读写权限，可执行任意 SQL 语句
- **潜在风险**：`DROP TABLE`、`DELETE FROM` 等破坏性操作会直接生效，建议生产环境启用事务回滚
- **建议**：使用只读模式运行（`--read-only` 参数），或在受控沙箱中使用

---

## 2. Filesystem（官方）

| 项目 | 信息 |
|------|------|
| **名称** | Filesystem Access |
| **仓库** | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) |
| **Stars** | 32,000+ |
| **许可证** | MIT |

### 功能描述
文件系统 MCP Server，提供安全的文件读写、目录浏览、路径搜索和代码库操作能力。支持通过允许列表（allowlist）精确控制可访问的目录范围。

### 安装命令
```bash
npx -y @modelcontextprotocol/server-filesystem /workspace
```

### 环境变量
无额外环境变量需求。

### Triad 集成价值
- **代码库操作**：读取、修改、创建项目文件，支持代码重构和文件生成
- **配置管理**：读写 Triad 配置文件、Skill 模板文件
- **日志记录**：将执行结果和对话历史持久化到本地文件
- **文档处理**：读取和写入 Markdown、YAML、JSON 等格式文件

### 安全提示
- **权限范围**：可访问指定目录下的所有文件和子目录，包括读取、写入、删除操作
- **潜在风险**：文件覆盖、意外删除、敏感文件泄露（如 `.env`、私钥文件）
- **建议**：严格限制允许访问的目录范围，禁止访问用户主目录根路径，避免传入 `/` 或 `~`

---

## 3. Brave Search（官方）

| 项目 | 信息 |
|------|------|
| **名称** | Brave Search |
| **仓库** | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) |
| **Stars** | 32,000+ |
| **许可证** | MIT |

### 功能描述
基于 Brave Search API 的联网搜索 MCP Server，支持网页搜索、图片搜索和新闻搜索。无需 Google API，隐私友好，适合获取实时信息、验证事实和补充知识库。

### 安装命令
```bash
npx -y @modelcontextprotocol/server-brave-search
```

### 环境变量
| 变量名 | 必填 | 说明 |
|--------|------|------|
| `BRAVE_API_KEY` | 是 | Brave Search API 密钥，[在此获取](https://brave.com/search/api/) |

### Triad 集成价值
- **联网搜索**：突破训练数据时间限制，获取最新资讯和技术文档
- **事实核查**：验证 AI 生成内容的准确性
- **知识补充**：获取实时数据（股价、天气、新闻）丰富回答
- **技术研究**：搜索最新的开源项目、技术博客和文档

### 安全提示
- **权限范围**：通过 Brave API 执行公开网络搜索，不访问本地系统
- **潜在风险**：搜索结果可能包含不准确或恶意信息；API 调用消耗配额
- **建议**：设置 API 调用频率限制；对搜索结果进行可信度评估；避免将用户隐私数据作为搜索关键词

---

## 4. GitHub（官方）

| 项目 | 信息 |
|------|------|
| **名称** | GitHub Operations |
| **仓库** | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) |
| **Stars** | 32,000+ |
| **许可证** | MIT |

### 功能描述
GitHub 官方 MCP Server，支持完整的代码仓库操作：读取代码、创建/合并 PR、管理 Issue、审查代码、操作分支和提交变更。深度集成 GitHub API，适合自动化代码审查和项目管理。

### 安装命令
```bash
npx -y @modelcontextprotocol/server-github
```

### 环境变量
| 变量名 | 必填 | 说明 |
|--------|------|------|
| `GITHUB_TOKEN` | 是 | GitHub Personal Access Token，需 `repo` 和 `issues` 权限 |
| `GITHUB_OWNER` | 否 | 默认仓库所有者用户名 |
| `GITHUB_REPO` | 否 | 默认仓库名称 |

### Triad 集成价值
- **代码审查**：自动分析 PR 变更，生成审查意见
- **Issue 管理**：读取和处理 GitHub Issue，自动化工作流
- **代码同步**：将 AI 生成的代码变更直接提交到仓库
- **项目协作**：多开发者场景下协调代码合并和发布

### 安全提示
- **权限范围**：拥有 Token 权限范围内的所有 GitHub 操作能力，包括代码读写、仓库删除
- **潜在风险**：Token 泄露可能导致仓库被恶意操作；误操作可能导致代码丢失
- **建议**：使用最小权限原则配置 Token（仅授予必要的 scope）；定期轮换 Token；不在公共环境打印 Token

---

## 5. Fetch（官方）

| 项目 | 信息 |
|------|------|
| **名称** | Web Fetch |
| **仓库** | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) |
| **Stars** | 32,000+ |
| **许可证** | MIT |

### 功能描述
HTTP 请求 MCP Server，支持网页内容抓取、API 调用和 HTTP 资源获取。内置 HTML 到 Markdown 的智能转换，适合抓取网页内容作为 LLM 上下文，以及调用外部 REST API。

### 安装命令
```bash
npx -y @modelcontextprotocol/server-fetch
```

### 环境变量
无额外环境变量需求。

### Triad 集成价值
- **网页抓取**：获取网页内容作为上下文，补充实时信息
- **API 集成**：调用外部服务 API（天气、翻译、数据分析等）
- **文档获取**：抓取技术文档和博客文章
- **数据收集**：从公开网页获取结构化数据

### 安全提示
- **权限范围**：可发起任意 HTTP/HTTPS 请求，访问公开网络资源
- **潜在风险**：可能访问恶意网站、泄露内部网络信息（SSRF 风险）；大页面内容可能导致 Token 溢出
- **建议**：配置 URL 白名单限制可访问的域名；设置请求超时和响应大小限制；禁止访问内网地址段（10.x.x.x、192.168.x.x、127.x.x.x）

---

## 6. Playwright Browser（微软官方）

| 项目 | 信息 |
|------|------|
| **名称** | Playwright MCP |
| **仓库** | [microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp) |
| **Stars** | 25,000+ |
| **许可证** | Apache-2.0 |

### 功能描述
微软官方基于 Playwright 的浏览器自动化 MCP Server，支持网页截图、元素点击、表单填写、页面导航和 JavaScript 执行。提供 headless 浏览器完整控制能力，适合需要视觉交互的网页自动化场景。

### 安装命令
```bash
npx -y @microsoft/playwright-mcp
```

### 环境变量
| 变量名 | 必填 | 说明 |
|--------|------|------|
| `PLAYWRIGHT_BROWSERS_PATH` | 否 | 浏览器可执行文件路径 |
| `HEADLESS` | 否 | 是否以无头模式运行，默认 `true` |

### Triad 集成价值
- **网页截图**：生成网页预览图，可视化展示给用户
- **自动化测试**：执行前端自动化测试流程
- **动态内容抓取**：处理 JavaScript 渲染的单页应用（SPA）
- **交互式操作**：模拟用户点击、滚动、表单填写等操作

### 安全提示
- **权限范围**：拥有完整浏览器控制能力，可执行 JavaScript、下载文件、访问 Cookie
- **潜在风险**：恶意网页可能利用浏览器漏洞；可访问登录后的敏感页面；JavaScript 执行存在 XSS 风险
- **建议**：始终在隔离的沙箱环境中运行；禁止访问内部系统和敏感网页；定期更新 Playwright 和浏览器版本以修复安全漏洞

---

## 7. Pydantic AI Sandbox

| 项目 | 信息 |
|------|------|
| **名称** | Pydantic AI MCP |
| **仓库** | [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) |
| **Stars** | 18,000+ |
| **许可证** | MIT |

### 功能描述
Pydantic AI 项目提供的安全 Python 代码沙箱 MCP Server，基于 Docker 容器隔离执行 Python 代码。支持代码执行、包安装、文件操作和数据处理，适合需要动态执行代码的 AI 应用场景。

### 安装命令
```bash
# Docker 方式
docker run -p 8080:8080 pydantic/pydantic-ai-mcp
```

### 环境变量
无额外环境变量需求（通过 Docker 运行时配置）。

### Triad 集成价值
- **代码执行**：安全执行 AI 生成的 Python 代码，验证算法正确性
- **数据分析**：运行数据处理脚本，生成图表和统计结果
- **自动化脚本**：执行文件转换、批量处理等自动化任务
- **沙箱隔离**：Docker 容器确保恶意代码不会影响宿主系统

### 安全提示
- **权限范围**：容器内可执行 Python 代码、安装 PyPI 包、读写容器内文件
- **潜在风险**：Docker 逃逸风险；容器配置不当可能导致宿主机暴露；网络访问可能用于 SSRF
- **建议**：使用非 root 用户运行容器；限制容器网络访问（`--network=none` 或专用网络）；限制容器资源（CPU/内存）；定期更新基础镜像

---

## 8. Apify Web Scraping

| 项目 | 信息 |
|------|------|
| **名称** | Apify MCP Server |
| **仓库** | [apify/apify-mcp-server](https://github.com/apify/apify-mcp-server) |
| **Stars** | 8,000+ |
| **许可证** | Apache-2.0 |

### 功能描述
Apify 云平台 MCP Server，提供 3000+ 云工具（Actors）的访问能力，涵盖网页数据抓取、社交媒体监控、SEO 分析、电商数据采集等。通过统一的 MCP 接口调用 Apify 云服务的强大能力。

### 安装命令
```bash
npx -y apify-mcp-server
```

### 环境变量
| 变量名 | 必填 | 说明 |
|--------|------|------|
| `APIFY_TOKEN` | 是 | Apify API Token，[在此获取](https://console.apify.com/account/integrations) |
| `APIFY_ACTORS` | 否 | 指定可使用的 Actor 列表，逗号分隔 |

### Triad 集成价值
- **大规模抓取**：利用云端 3000+ 工具执行复杂的数据采集任务
- **社交媒体监控**：抓取 Twitter、Reddit 等平台的公开数据
- **SEO/竞品分析**：自动化网站分析和关键词监控
- **无服务器**：无需本地部署，云端弹性扩展

### 安全提示
- **权限范围**：可调用 Apify 平台上所有授权的 Actor，执行网页抓取和数据处理
- **潜在风险**：抓取行为可能违反目标网站的 ToS；API Token 泄露可能导致云服务资源被滥用（产生费用）
- **建议**：遵守目标网站的 robots.txt 和 ToS；设置 Apify 平台的消费限额；定期轮换 API Token；仅授权必要的 Actor 访问权限

---

## 快速对比表

| Server | 类别 | 需要 API Key | 权限级别 | Docker 依赖 |
|--------|------|-------------|---------|------------|
| SQLite | 数据库 | 否 | 文件读写 | 否 |
| Filesystem | 文件系统 | 否 | 文件读写 | 否 |
| Brave Search | 搜索 | BRAVE_API_KEY | 网络请求 | 否 |
| GitHub | DevOps | GITHUB_TOKEN | 仓库读写 | 否 |
| Fetch | 网络 | 否 | HTTP 请求 | 否 |
| Playwright | 浏览器 | 否 | 浏览器控制 | 否 |
| Pydantic Sandbox | 代码执行 | 否 | 代码执行 | **是** |
| Apify | 数据抓取 | APIFY_TOKEN | 云端调用 | 否 |

## 推荐的最小启动组合

对于 Triad 的初始部署，建议启用以下 4 个 Server（无需外部 API Key）：

```json
["sqlite", "filesystem", "fetch", "playwright"]
```

当需要外部服务时，按需启用：
- **联网搜索** → 启用 `brave-search`（配置 `BRAVE_API_KEY`）
- **代码仓库操作** → 启用 `github`（配置 `GITHUB_TOKEN`）
- **安全代码执行** → 启用 `pydantic-sandbox`（启动 Docker 容器）
- **大规模数据抓取** → 启用 `apify`（配置 `APIFY_TOKEN`）

---

*本文档最后更新于 2025 年 1 月。各 MCP Server 的 Stars 数和版本信息可能随时间变化，请以官方仓库为准。*
