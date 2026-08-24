# Authentik SSO 中转网关

## 📖 项目简介

本项目是 [Authentik](https://goauthentik.io/) 单点登录的**协议中转网关（多应用版）**。部分企业应用（如网易企业邮箱、金蝶K3Cloud云星空企业版）不支持 OAuth2/OIDC/SAML 等标准协议，无法直接接入 Authentik，需要编写中转网关在两者之间做协议转换。

**单个网关服务可同时接入多个目标应用**：

- 每个目标应用在 Authentik 中有**独立的 OAuth2 Provider**（独立 Client ID / Client Secret）
- 每个目标应用在 `apps.yaml` 中独立配置，协议转换逻辑以插件形式放在 `handlers/` 目录

当前已实现：

- **网易企业邮箱**（`handlers/netease_mail.py`）：Authentik 认证后调用网易开放平台 API 两次换取免密登录 Sign
- **金蝶 K3Cloud**（`handlers/k3cloud.py`）：简单通行证（SimPas）模式，Authentik 认证后本地 SHA256 签名生成免密登录 URL（零网络调用），用户名取 Authentik 的 `preferred_username`；`entry: wpf` 时生成 `k3cloud://` 协议地址唤起 Windows 客户端（如 `k3client` 应用），默认 `html5` 为网页入口

> **反向场景**：若需让用户用钉钉扫码**登录 Authentik**（Authentik 作为 OIDC 客户端接入钉钉，即 Source 方向），使用独立部署的 [dingtalk-oidc-gateway](https://github.com/sqkkyzx/dingtalk-oidc-gateway)

## 🛠️ 技术栈

- Python 3.12 + Flask 3
- authlib（OIDC 客户端）
- requests（调用目标应用 API）
- pyyaml（apps.yaml 多应用配置）
- gunicorn（生产运行）+ Docker Compose 部署

## 🔄 认证流程（以 /sso/163mail 为例）

```
用户浏览器        本网关                     Authentik(该应用独立client)   网易企业邮箱
    │               │                              │                        │
    │ ① 访问/sso/163mail                            │                        │
    │ ─────────────>│ ② 302 跳转 OIDC 认证          │                        │
    │               │ ────────────────────────────>│                        │
    │ ③ 用户登录授权 │                              │                        │
    │ <────────────────────────────────────────────│                        │
    │ ④ 回调 /sso/163mail/callback（带授权码）        │                        │
    │ ─────────────>│ ⑤ 换 userinfo 取 email        │                        │
    │               │ ────────────────────────────>│                        │
    │               │ ⑥ POST /api/pub/token/ssoAuthToken                │
    │               │    （appId+authCode+orgOpenId → ssoAuthToken）     │
    │               │ ─────────────────────────────────────────────────────>│
    │               │ ⑦ POST /api/sso/ssoSign                           │
    │               │    （头传 qiye-* 凭证，体传 accountName+domain）    │
    │               │ ─────────────────────────────────────────────────────>│
    │ ⑧ 302 到免密登录页（endpoint+sso_token={sign}）     │                        │
    │ <─────────────│                              │                        │
    │ ⑨ 直接进入邮箱 │                              │                        │
    │ ───────────────────────────────────────────────────────────────────────>│
```

## 📁 项目结构

| 文件/目录                                    | 说明                                                                                |
| -------------------------------------------- | ----------------------------------------------------------------------------------- |
| `authentik-sso-gateway/src/app.py`           | 通用网关入口：加载 apps.yaml、动态注册多个 OAuth 客户端、按 slug 分发路由与 handler |
| `authentik-sso-gateway/src/handlers/`        | 目标应用协议转换插件目录（新增应用在此扩展）                                        |
| `authentik-sso-gateway/src/apps.yaml`        | 多应用配置（含密钥不提交），模板见 `apps.yaml.example`                              |
| `authentik-sso-gateway/requirements.txt`     | Python 依赖清单                                                                     |
| `authentik-sso-gateway/src/gunicorn.conf.py` | gunicorn 生产运行配置                                                               |
| `authentik-sso-gateway/Dockerfile`           | 镜像构建（基于 python:3.12-slim）                                                   |
| `authentik-sso-gateway/docker-compose.yml`   | 容器编排（端口 9010、src 代码目录与 logs 挂载）                                     |
| `authentik-sso-gateway/.env.example`         | 全局环境变量模板                                                                    |
| `.vscode/`                                   | VSCode 运行调试配置（工作区根）                                                     |
| `authentik-sso-gateway/logs/`                | 运行日志目录（运行时生成，不提交；`sso-gateway.log` 按天轮转保留 30 天）            |
| `docs/`                                      | 相关部署手册（钉钉 OIDC Source 网关等）                                             |
| `dingtalk-oidc-gateway/`                     | 钉钉 OIDC 网关独立仓库（Source 方向，本地对照用，不入版本控制）                     |

## ⚙️ 前置准备（以网易企业邮箱为例）

### 1. 网易企业邮箱侧

1. 登录网易企业邮箱管理后台，进入【企业设置 / 自建应用管理】创建应用（或联系网易开通 API 权限）
2. 记录以下三个值（对应 `apps.yaml` 中 `config` 段的配置项）：
   - **应用ID**（`app_id`，请求头 qiye-app-id）
   - **应用授权码 authCode**（`auth_code`，接口① 获取辅助凭证用）
   - **企业OpenId**（`org_open_id`，请求头 qiye-org-open-id）
3. 确认 API 域名（默认 `https://api.qiye.163.com`，以网易文档为准；单点登录接口无需 IP 白名单）

### 2. 金蝶 K3Cloud 侧

1. Administrator 登录数据中心，进入【系统管理】→【第三方系统登录授权】新增应用，记录（对应 `apps.yaml` 中 `config` 段）：
   - **应用ID**（`app_id`）与**应用密钥**（`app_secret`）
   - 授权方式建议选**允许全部用户登录**（指定用户登录则只有列表内用户能 SSO）
2. 记录**数据中心 ID**（`dbid`，K3Cloud 管理端查看）
3. 确认 K3Cloud 对用户浏览器的访问地址（`base_url`，公网 https 形态，末尾不带斜杠）
4. 确认 K3Cloud 用户名与 Authentik 用户名（`preferred_username`）完全一致（handler 依赖它定位 K3 账号）

> **Windows 客户端入口**（可选双入口）：K3Cloud 管理端零额外配置，提供与网页版并行的客户端唤起入口：
> 1. `apps.yaml` 复制 k3cloud 段为新 slug `k3client`：业务参数（base_url/dbid/app_id/app_secret/permitcount/lcid）完全相同，仅加 `entry: wpf`（`apps.yaml.example` 已含现成示例段）
> 2. Authentik 新建**独立** Provider（回调本地 `http://127.0.0.1:9010/sso/k3client/callback` + 生产两行）与 Application（Launch URL `https://<网关对外地址>/sso/k3client`）
> 3. 体验：登录后经网关**落地页**唤起（Safari/Firefox 自动唤起；Chrome/Edge 因浏览器安全策略需点击一次"启动"按钮——腾讯会议/Zoom 网页唤起同款；唤起成功后页面 3 秒尝试自动关闭，部分浏览器需手动关闭）→ 本机金蝶客户端自动登录（**需电脑已安装金蝶 ClickOnce 客户端**）
> 4. 建议两个 Application 命名区分（如"金蝶K3（网页）"/"金蝶K3（客户端）"），图标复用同一金蝶图
>
> 实测经验：wpf 地址中的 `LoginUrl` 必须为未编码裸值（金蝶 ClickOnce 不认编码后的地址，已固化在 handler 实现中）。

> 签名含时间戳且 K3Cloud 服务端校验时间窗，网关宿主机时钟需 NTP 校准。

### 3. Authentik 侧

1. **创建 Provider**：管理界面 → Applications → Providers → **Create** → 选择 **OAuth2/OpenID Connect Provider**
   - Name：随意（如 `netease-mail-sso`）
   - Authorization flow：默认 `explicit-consent` 或 `implicit-consent`
   - **Redirect URI/Origins**：填写网关回调地址，**必须完全一致**（含协议/域名/端口/路径），格式统一为：
     - 生产：`https://sso.example.com/sso/163mail/callback`
     - 本地调试：`http://127.0.0.1:9010/sso/163mail/callback`
2. **记录凭证**：创建完成后记下该 Provider 的 **Client ID**、**Client Secret** 以及 **OpenID Configuration URL**
   - OpenID Configuration URL 格式：`https://<authentik域名>/application/o/<slug>/.well-known/openid-configuration`
3. **创建 Application**：Applications → Applications → **Create**，绑定上面创建的 Provider，并在可选策略中配置用户访问范围（默认所有用户可见）
4. 确保 Authentik 用户的 **email** 属性已正确填写（网关依赖它定位目标系统账号）

## ➕ 新增目标应用三步走

1. **Authentik 侧**：新建 OAuth2/OpenID Connect Provider（独立 Client ID/Secret），回调地址填 `https://<网关对外地址>/sso/<slug>/callback`，创建 Application 绑定
2. **网关配置**：`apps.yaml` 的 `apps` 段追加一组配置（authentik 凭证 + handler 名 + config 业务参数）
3. **网关代码**（仅当协议逻辑与现有 handler 不同）：`handlers/` 新建模块实现 `handle(user_info, config) -> 免密URL | None`，并在 `handlers/__init__.py` 的 `HANDLERS` 登记

完成后 `docker compose restart` 即生效（apps.yaml 以挂载注入，无需重建镜像）。

## 🐳 Docker Compose 部署

镜像基于 **python:3.12-slim** 构建，自带 Python 3.12 运行环境；依赖打包进镜像，代码与配置从宿主机挂载注入——改代码/配置只需 restart，无需重建镜像。

### 0. 服务器目录结构与文件清单

在部署服务器上创建 `authentik-sso-gateway` 目录，与本地项目同名子目录结构完全一致（整目录同步即可；代码放 `src/` 子目录，apps.yaml 与代码同目录，无需单独 conf 目录；`logs/` 首次启动自动生成）：

```
authentik-sso-gateway/
├── docker-compose.yml     # 容器编排
├── Dockerfile             # 仅构建 Python 依赖环境
├── requirements.txt
├── .env                   # 全局配置（含密钥，不提交）
├── src/                   # ← 挂载到容器 /app
│   ├── app.py
│   ├── gunicorn.conf.py
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── netease_mail.py
│   │   └── k3cloud.py
│   └── apps.yaml          # 多应用配置（含密钥，不提交）
└── logs/                  # ← 挂载到容器 /app/logs（运行时生成）
```

### 1. 配置

```bash
# 全局配置（放在 authentik-sso-gateway/ 根目录）
cp .env.example .env
# 多应用配置（放在 src/ 目录，与代码同目录）
cp src/apps.yaml.example src/apps.yaml
vi .env src/apps.yaml
```

| 文件        | 内容                                                                                                                                   |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `.env`      | 全局项：`SECRET_KEY`（随机串，`python3 -c "import secrets; print(secrets.token_hex(32))"` 生成）、`BASE_URL`（反代场景填网关对外地址） |
| `apps.yaml` | 每个应用：Authentik 的 `client_id` / `client_secret` / `metadata_url` + `handler` 名 + 该 handler 的 `config` 业务参数                 |

> 容器时区已在 compose 中通过 `TZ=Asia/Shanghai` 固定（基础设施设置写 `environment:`，随环境变化的密钥配置走 `.env`），日志时间戳与按天轮转均按北京时间。

### 2. 构建并启动

```bash
docker compose up -d --build
```

### 3. 查看日志 / 更新配置

```bash
docker compose logs -f          # 跟踪实时日志（stdout）
tail -f logs/sso-gateway.log    # 查看日志文件（按天轮转，保留 30 天自动清理）
docker compose restart          # 修改 src/ 下代码、apps.yaml 或 .env 后重启即生效（无需 rebuild）
```

启动后访问 `http://<宿主机>:9010/healthz` 返回 `ok` 即部署成功；各应用单点登录入口为 `http://<宿主机>:9010/sso/<slug>`。

### 4. 生产发布（建议部署反代）

生产经 Nginx 反代统一发布在 `https://authgateway.company.com.cn`（泛域名证书）下，本网关路径为 `/companysso`，钉钉 OIDC 网关为 `/dingtalk`。完整 Nginx 配置见 [docs/nginx-gateway.conf](docs/nginx-gateway.conf)（含两个 location 与证书占位）。

本网关侧配置（**仅服务器** `.env`，本地调试不加）：

1. 服务器 `authentik-sso-gateway/.env` 追加 `SCRIPT_NAME=/companysso` → `docker compose restart`
2. Authentik 应用 Launch URL 填 `https://authgateway.company.com.cn/companysso/sso/163mail`
3. Authentik Provider 回调地址添加 `https://authgateway.company.com.cn/companysso/sso/163mail/callback`

> `SCRIPT_NAME` 为 WSGI 标准变量（gunicorn raw_env 注入）：Flask 自动剥前缀分发路由，OAuth 回调地址自动带前缀闭环；`BASE_URL` 留空即可。独立域名/子域名发布或本地 F5 调试时 `SCRIPT_NAME` 留空。

其他发布形态参考：

- **独立域名反代**：`location / { proxy_pass http://127.0.0.1:9010; }` + 透传标准 `X-Forwarded-*` 头，`BASE_URL=https://<域名>`（或留空按请求头推断），`SCRIPT_NAME` 留空
- **WAF 透传前缀**（如雷池不改写路径的场景）：同 SCRIPT_NAME 机制，WAF 匹配 `/companysso` 回源本服务即可

## 💻 本地开发与 VSCode 调试

1. 创建虚拟环境并安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. 复制 `.env.example` → `.env`、`apps.yaml.example` → `apps.yaml` 并填写；本地调试时 Authentik Provider 中需额外添加回调 `http://127.0.0.1:9010/sso/163mail/callback`

3. 用 VSCode 打开项目，安装推荐扩展（Python + Debugpy），按 **F5** 选择「调试 SSO 中转网关」即可断点调试（自动加载 `.env`，服务运行在 http://127.0.0.1:9010 ，可访问 `/sso/163mail` 测试完整流程，`/healthz` 验证服务状态）

> 端口说明：所有环境（本地调试 / 容器内 / 宿主机映射）统一使用 **9010**，避免多端口记忆负担；如需更换，改 `.env` 的 `PORT`（本地）与 `docker-compose.yml` 的 `ports` + `gunicorn.conf.py` 的 `bind`（Docker），并同步修改 Authentik 回调地址。

## ❓ 常见问题

- **回调后报 redirect_uri mismatch**：Authentik 中登记的回调地址与实际请求的 `redirect_uri` 不一致，逐字符比对协议/域名/端口/路径（注意新路由格式为 `/sso/<slug>/callback`）
- **访问 /sso/<slug> 返回 404**：该应用未在 `apps.yaml` 中配置，或配置校验失败被跳过（查看启动日志中的 `[ERROR] 应用 [...]` 告警）
- **报"无法获取用户邮箱"**：Authentik 用户 email 属性为空，或 Provider 的 scope 未包含 `email`
- **本地 9010 端口启动失败**：端口被其他程序占用时可更换端口（改 `.env` 的 `PORT`），并同步修改 Authentik 回调地址
- **网易接口返回失败**：检查 apps.yaml 中 `app_id` / `auth_code` / `org_open_id` 是否正确、网易侧应用状态是否启用；接口返回的 `code` / `message` 会记录在容器日志中，可据此排查
- **金蝶登录失败（页面提示签名或授权无效）**：依次检查 ① `dbid` / `app_id` / `app_secret` 是否与 K3Cloud【第三方系统登录授权】中一致 ② K3Cloud 用户名与 Authentik 用户名是否一致 ③ 宿主机时钟是否 NTP 准确（时间戳超时窗失效）④ 该用户是否在授权范围内（若选了"指定用户登录"）
- **点"金蝶K3（客户端）"图标无反应或提示找不到应用**：① 电脑未安装金蝶 ClickOnce 客户端（安装后重试）② 浏览器弹出的确认框被取消/拦截，点击落地页上的"点击此处重试"即可 ③ 若网关日志已有 `entry=wpf` 签名记录则网关侧正常，问题在本机客户端环境。**标签页停在"正在启动金蝶K3客户端"提示页属预期行为**（客户端启动后可自动关闭或手动关闭）
