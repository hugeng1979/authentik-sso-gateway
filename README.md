# Authentik SSO 中转网关（多应用版）

> 一个 Flask 服务，为**不支持 OAuth2/OIDC/SAML 标准协议**的企业应用提供 Authentik 单点登录中转：用户在 Authentik 登录一次，即可免密直达多个目标系统。

## 目录

- [1. 项目简介](#1-项目简介)
- [2. 工作原理](#2-工作原理)
- [3. 部署与启动](#3-部署与启动)
- [4. 接入目标应用](#4-接入目标应用)
- [5. 配置参考](#5-配置参考)
- [6. 生产发布](#6-生产发布)
- [7. 本地开发与调试](#7-本地开发与调试)
- [8. 扩展新目标应用](#8-扩展新目标应用)
- [9. 常见问题](#9-常见问题)
- [10. 附录](#10-附录)

---

## 1. 项目简介

### 解决什么问题

网易企业邮箱、金蝶 K3Cloud 等企业应用不支持标准单点登录协议，无法直接接入 Authentik。本网关在中间做**协议转换**：

- 对 **Authentik**：扮演标准 OIDC 客户端，用户在此完成登录授权；
- 对 **目标应用**：调用对方开放平台 API（或本地签名），生成「免密登录地址」，302 直达。

### 一个网关挂多个应用

- 每个目标应用在 Authentik 中有**独立的 OAuth2 Provider**（独立 Client ID / Client Secret）；
- 每个应用在 `apps.yaml` 中独立配置一段；
- 协议转换逻辑以**插件（handler）**形式放在 `handlers/` 目录，新增应用不必改动网关主逻辑。

### 当前已支持的应用

| 应用 | handler | 转换方式 |
|------|---------|----------|
| 网易企业邮箱 | `netease_mail` | 调用网易开放平台 API 两次，换取免密登录 Sign（联网） |
| 金蝶 K3Cloud（网页版） | `k3cloud` | 简单通行证 SimPas：本地 SHA256 签名生成免密 URL，零网络调用 |
| 金蝶 K3Cloud（Windows 客户端） | `k3cloud` | 同上，`entry: wpf` 时生成 `k3cloud://` 地址唤起本机客户端 |

> **反向场景**：如果目标是「用钉钉扫码登录 Authentik」（Authentik 作为 OIDC 客户端接入钉钉），请使用独立项目 [dingtalk-oidc-gateway](https://github.com/sqkkyzx/dingtalk-oidc-gateway)，本网关不适用。

---

## 2. 工作原理

### 三个核心概念

| 概念 | 含义 | 例子 |
|------|------|------|
| **slug** | 应用标识，决定访问路径 `/sso/<slug>` 与回调 `/sso/<slug>/callback`，仅允许字母、数字、`-`、`_` | `163mail`、`k3cloud`、`k3client` |
| **handler** | 协议转换插件：接收 Authentik 用户信息，返回目标系统免密地址 | `netease_mail`、`k3cloud` |
| **apps.yaml** | 多应用配置文件，每段 = slug → { Authentik 凭证 + handler 名 + 业务参数 } | 见 [5.2](#52-appsyaml多应用配置) |

### 认证时序

```
用户浏览器            本网关 (Flask)                Authentik                目标应用（网易/金蝶）
    │                    │                            │                          │
    │ ① 访问 /sso/<slug>  │                            │                          │
    │ ───────────────────>│ ② 302 跳转 OIDC 授权        │                          │
    │                    │ ───────────────────────────>│                          │
    │ ③ 登录并授权         │                            │                          │
    │ <───────────────────│────────────────────────────│                          │
    │ ④ 回调 /sso/<slug>/callback（带授权码）            │                          │
    │ ───────────────────>│ ⑤ 用授权码换 token 取 userinfo │                        │
    │                    │ ───────────────────────────>│                          │
    │                    │ ⑥ handler.handle() 协议转换   │                          │
    │                    │    （网易：两次 API 换免密 Sign │                          │
    │                    │     金蝶：本地签名零网络调用）   │                          │
    │ ⑦ 302 到免密登录地址  │                            │                          │
    │ <───────────────────│                            │                          │
    │ ⑧ 直接进入目标系统    │                            │                          │
```

### 关键行为

1. 网关为每个应用注册**独立的** OAuth 客户端（`authentik_<slug>`），互不影响；
2. 回调地址格式：`<BASE_URL 或按请求头推断>/sso/<slug>/callback`，**必须与 Authentik 中登记的完全一致**（协议/域名/端口/路径逐字符相同）；
3. `handle()` 返回 `http(s)` 地址时直接 302；返回自定义协议地址（如 `k3cloud://`）时渲染**落地页**唤起——Chrome/Edge 因浏览器安全策略需点击一次「启动」按钮（与腾讯会议/Zoom 网页唤起同款模式）；
4. 协议转换失败时返回 `None`，网关记录日志并提示。

---

## 3. 部署与启动

### 3.1 前置要求

- 一台可运行 Docker 的服务器（本地亦可）；
- 一个已部署好的 Authentik 实例（需管理员权限创建 Provider / Application）；
- 对外使用 9010 端口（**统一端口约定**，本地调试 / 容器内 / 宿主机映射全部一致）。

### 3.2 服务器目录结构

将整个仓库目录同步到服务器（排除 `.git`），结构如下：

```
authentik-sso-gateway/
├── docker-compose.yml          # 容器编排（端口 9010、src 与 logs 挂载）
├── Dockerfile                  # 基于 python:3.12-slim，只安装 Python 依赖
├── requirements.txt
├── .env.example                # 全局配置模板 → 复制为 .env
├── .env                        # 全局配置（含密钥，不提交）
├── src/                        # 代码目录，整目录挂载进容器 /app
│   ├── app.py                  # 网关主程序
│   ├── gunicorn.conf.py        # gunicorn 生产配置
│   ├── apps.yaml.example       # 多应用配置模板 → 复制为 apps.yaml
│   ├── apps.yaml               # 多应用配置（含密钥，不提交）
│   └── handlers/               # 协议转换插件目录
│       ├── __init__.py         # HANDLERS 注册表
│       ├── netease_mail.py
│       └── k3cloud.py
└── logs/                       # 运行日志（首次启动自动生成，不提交）
```

### 3.3 初始化配置

```bash
# 1) 全局配置：放在仓库根目录
cp .env.example .env

# 2) 多应用配置：与代码同目录（src/）
cp src/apps.yaml.example src/apps.yaml
```

### 3.4 填写 .env

| 变量 | 必填 | 说明 |
|------|------|------|
| `SECRET_KEY` | ✅ | Flask 会话密钥，**生产必须改**，用下面命令生成随机串 |
| `BASE_URL` | 视场景 | 网关对外地址（含协议、不含末尾斜杠）；反代时用于生成回调地址，留空则按请求头推断 |

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"   # 生成 SECRET_KEY
```

> 其余变量（`APPS_CONFIG_FILE` / `GUNICORN_WORKERS` / `SCRIPT_NAME` / `PORT`）均为可选项，详见 [5.1](#51-env全局配置)。

### 3.5 启动并验证

```bash
docker compose up -d --build
curl http://127.0.0.1:9010/healthz    # 返回 ok 即部署成功
```

> 首次冒烟测试时 `apps.yaml` 可保持 `apps:` 为空；接入真实应用见 [第 4 节](#4-接入目标应用)，改完 `docker compose restart` 即可生效（`src/` 整目录挂载，**无需重建镜像**）。

### 3.6 常用运维命令

| 命令 | 作用 |
|------|------|
| `docker compose logs -f` | 跟踪实时日志（stdout） |
| `tail -f logs/sso-gateway.log` | 查看日志文件（按天轮转，保留 30 天自动清理） |
| `docker compose restart` | 修改 `src/` 下代码、`apps.yaml` 或 `.env` 后重启生效，无需 rebuild |

> 容器时区已在 compose 中固定为 `TZ=Asia/Shanghai`，日志时间戳与按天轮转均按北京时间。

---

## 4. 接入目标应用

### 4.1 通用步骤：Authentik 侧（每个应用都要做一次）

1. **创建 Provider**：管理界面 → Applications → Providers → **Create** → 选择 **OAuth2/OpenID Connect Provider**
   - Name：随意（如 `netease-mail-sso`）；
   - Authorization flow：默认 `explicit-consent` 或 `implicit-consent`；
   - **Redirect URI/Origins**：填写网关回调地址，**必须完全一致**（含协议/域名/端口/路径），格式：
     - 生产：`https://<网关对外地址>/sso/<slug>/callback`
     - 本地调试：`http://127.0.0.1:9010/sso/<slug>/callback`
2. **记录凭证**：该 Provider 的 **Client ID**、**Client Secret**、**OpenID Configuration URL**
   - OpenID Configuration URL 格式：`https://<authentik域名>/application/o/<应用slug>/.well-known/openid-configuration`
3. **创建 Application**：Applications → Applications → **Create**，绑定上面创建的 Provider，并在可选策略中配置用户访问范围（默认所有用户可见）
4. **确认用户属性**（网关依赖它们定位目标系统账号）：
   - 网易企业邮箱：用户 **email** 必须已填写；
   - 金蝶 K3Cloud：用户名取 **preferred_username**，须与 K3Cloud 账号完全一致。

### 4.2 网易企业邮箱

**目标侧（网易企业邮箱管理后台）**

1. 进入【企业设置 / 自建应用管理】创建应用（或联系网易开通 API 权限）；
2. 记录以下三个值（对应 `config` 段的配置项）：
   - **应用ID** → `app_id`（请求头 qiye-app-id）
   - **应用授权码 authCode** → `auth_code`（接口① 获取辅助凭证用）
   - **企业OpenId** → `org_open_id`（请求头 qiye-org-open-id）
3. 确认 API 域名（默认 `https://api.qiye.163.com`，以网易文档为准；单点登录接口无需 IP 白名单）。

**网关侧 `apps.yaml`**（添加一段）：

```yaml
apps:
  163mail:
    authentik:
      client_id: <Authentik Provider 的 Client ID>
      client_secret: <Authentik Provider 的 Client Secret>
      metadata_url: https://<authentik域名>/application/o/163mail/.well-known/openid-configuration
    handler: netease_mail
    config:
      api_host: https://api.qiye.163.com   # 网易 API 域名
      app_id: <网易应用ID>
      auth_code: <网易应用授权码>
      org_open_id: <网易企业OpenId>
```

**免密原理（两次 API 调用）**

1. 接口① `POST {api_host}/api/pub/token/ssoAuthToken`，body `{appId, authCode, orgOpenId}` → 返回 `data.ssoAuthToken`（单点登录辅助凭证）；
2. 接口② `POST {api_host}/api/sso/ssoSign`，请求头带 `qiye-app-id` / `qiye-org-open-id` / `qiye-sso-auth-token`，body `{accountName, domain, pass_2fa: 1}` → 返回 `data.sign` + `data.endpoint`（`pass_2fa: 1` 强制跳过二次验证，直达邮箱）；
3. 302 到 `{endpoint}?sso_token={sign}&lang=0`（endpoint 为空时回退 `https://entryhz.qiye.163.com/login/ssoLogin?sso_token={sign}&lang=0`）。

### 4.3 金蝶 K3Cloud（网页版 + Windows 客户端双入口）

**目标侧（K3Cloud 管理端）**

1. Administrator 登录数据中心 →【系统管理】→【第三方系统登录授权】→ 新增应用：
   - 记录 **应用ID**（`app_id`）与**应用密钥**（`app_secret`）；
   - 授权方式建议选**允许全部用户登录**（选「指定用户登录」则只有列表内用户能 SSO）；
2. 记录**数据中心 ID**（`dbid`，K3Cloud 管理端查看）；
3. 确认 K3Cloud 对用户浏览器的访问地址 `base_url`（公网 https 形态，**末尾不带斜杠**）；
4. 确认 K3Cloud 用户名与 Authentik `preferred_username` **完全一致**（handler 依赖它定位 K3 账号）。

**网关侧 `apps.yaml`**：

```yaml
apps:
  k3cloud:
    authentik:
      client_id: <Authentik Provider 的 Client ID>
      client_secret: <Authentik Provider 的 Client Secret>
      metadata_url: https://<authentik域名>/application/o/k3cloud/.well-known/openid-configuration
    handler: k3cloud
    config:
      base_url: https://k3.example.com/K3Cloud   # K3Cloud 站点根地址，末尾不带斜杠
      dbid: <数据中心ID>
      app_id: <第三方登录授权应用ID>
      app_secret: <应用密钥>
      permitcount: 0     # 0 可重复登录（默认），1 仅一次
      lcid: 2052         # 2052 中文（默认）/ 1033 英文 / 3076 繁体
      entry: html5       # html5 网页入口（默认）/ wpf Windows 客户端入口
```

**Windows 客户端入口（可选，双入口）**

K3Cloud 管理端**零额外配置**，只需在 `apps.yaml` 复制一段（业务参数完全相同，仅加 `entry: wpf`）：

```yaml
apps:
  k3client:
    authentik:
      client_id: <新建独立 Provider 的 Client ID>
      client_secret: <Client Secret>
      metadata_url: https://<authentik域名>/application/o/k3client/.well-known/openid-configuration
    handler: k3cloud
    config:
      base_url: https://k3.example.com/K3Cloud
      dbid: <与 k3cloud 相同>
      app_id: <与 k3cloud 相同>
      app_secret: <与 k3cloud 相同>
      permitcount: 0
      lcid: 2052
      entry: wpf         # 区别仅在此：生成 k3cloud:// 地址唤起本机客户端
```

接入步骤：

1. Authentik 新建**独立** Provider（回调地址：本地 `http://127.0.0.1:9010/sso/k3client/callback` + 生产两行）与 Application（Launch URL `https://<网关对外地址>/sso/k3client`）；
2. 体验：登录后经网关**落地页**唤起（Safari/Firefox 自动唤起；Chrome/Edge 因浏览器安全策略需点一次「启动」按钮；唤起成功后页面 3 秒尝试自动关闭）→ 本机金蝶客户端自动登录（**需电脑已安装金蝶 ClickOnce 客户端**）；
3. 建议两个 Application 命名区分（如「金蝶K3（网页）」/「金蝶K3（客户端）」），图标复用同一金蝶图。

**注意**

- 签名含时间戳且 K3Cloud 服务端校验时间窗，网关宿主机时钟需 **NTP 校准**；
- wpf 地址中的 `LoginUrl` 必须为未编码裸值（金蝶客户端不认编码后的地址，已固化在 handler 实现中）。

### 4.4 配置完成后的验证

```bash
docker compose restart          # 修改 apps.yaml 后重启生效
curl http://127.0.0.1:9010/healthz
# 浏览器访问 https://<网关对外地址>/sso/<slug> → 跳 Authentik 登录 → 免密进入目标系统
```

---

## 5. 配置参考

### 5.1 .env（全局配置）

| 变量 | 必填 | 说明 |
|------|------|------|
| `SECRET_KEY` | ✅ | Flask 会话密钥，生产必须为复杂随机串（见 3.4 生成命令） |
| `BASE_URL` | 视场景 | 网关对外地址（含协议、不含末尾斜杠），反代时用于生成回调地址；留空按请求头推断 |
| `APPS_CONFIG_FILE` | - | 多应用配置文件路径，默认 `apps.yaml`，一般无需修改 |
| `GUNICORN_WORKERS` | - | gunicorn worker 数，默认 2（仅 Docker 部署生效） |
| `SCRIPT_NAME` | 视场景 | 同域子路径发布前缀，如 `/companysso`（见第 6 节）；独立域名/子域名/本地调试留空 |
| `PORT` | - | 本地调试端口，默认 9010 |

### 5.2 apps.yaml（多应用配置）

每段结构：

```yaml
apps:
  <slug>:                     # 访问路径 /sso/<slug> 与回调 /sso/<slug>/callback
    authentik:                # Authentik OAuth2 Provider 凭证（每个应用独立）
      client_id: ...
      client_secret: ...
      metadata_url: https://<authentik域名>/application/o/<slug>/.well-known/openid-configuration
    handler: <handler 名>     # handlers/ 注册表中的名字（netease_mail / k3cloud）
    config:                   # 该 handler 的业务参数，见 4.2 / 4.3
      ...
```

启动时逐项校验，**不合法只跳过该应用并告警，不影响其他应用**：

- slug 仅允许字母、数字、`-`、`_`；
- `client_id` / `client_secret` / `metadata_url` 三项必填；
- `handler` 必须在 `handlers/__init__.py` 的 `HANDLERS` 中已注册。

---

## 6. 生产发布

生产建议经 Nginx 统一反代，并透传标准 `X-Forwarded-Proto` / `X-Forwarded-Host` 头（网关已启用 ProxyFix，依赖这两个头正确识别协议与域名）。

### 形态 A：独立域名 / 子域名

```nginx
server {
    server_name sso.example.com;
    location / {
        proxy_pass http://127.0.0.1:9010;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }
}
```

网关侧：`.env` 填 `BASE_URL=https://sso.example.com`（或留空按请求头推断），`SCRIPT_NAME` 留空。

### 形态 B：同域子路径（如 `https://authgateway.company.com.cn/companysso`）

```nginx
server {
    server_name authgateway.company.com.cn;
    location /companysso {
        proxy_pass http://127.0.0.1:9010;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }
}
```

网关侧（**仅服务器 `.env`，本地调试不加**）：

1. `.env` 追加 `SCRIPT_NAME=/companysso` → `docker compose restart`；
2. Authentik 应用 Launch URL 填 `https://authgateway.company.com.cn/companysso/sso/163mail`；
3. Authentik Provider 回调地址添加 `https://authgateway.company.com.cn/companysso/sso/163mail/callback`。

> `SCRIPT_NAME` 为 WSGI 标准变量（gunicorn raw_env 注入）：Flask 自动剥前缀分发路由，`url_for` 生成的回调地址自动带前缀闭环；此时 `BASE_URL` 留空即可。

### 形态 C：WAF 透传前缀（不改写路径的场景）

同形态 B 的 `SCRIPT_NAME` 机制：WAF 匹配 `/companysso` 回源本服务即可，网关侧同样设 `SCRIPT_NAME=/companysso`。

---

## 7. 本地开发与调试

```bash
cd authentik-sso-gateway
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
cp src/apps.yaml.example src/apps.yaml
# 填写两个配置文件（apps.yaml 需先在 Authentik 建好 Provider，见 4.1）

cd src          # 必须在 src/ 内运行：app 以相对路径加载同目录的 apps.yaml 与 logs/
python app.py   # 服务运行在 http://127.0.0.1:9010
```

- 本地调试时，Authentik Provider 中需**额外添加**回调 `http://127.0.0.1:9010/sso/<slug>/callback`；
- 冒烟测试：访问 `http://127.0.0.1:9010/healthz` 返回 `ok`，`/sso/<slug>` 走完整 SSO 流程；
- VSCode 断点调试：在项目根建 `.vscode/launch.json`，把工作目录设为 `src/` 并自动加载 `.env`：

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "调试 SSO 中转网关",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/src/app.py",
      "cwd": "${workspaceFolder}/src",
      "envFile": "${workspaceFolder}/.env",
      "console": "integratedTerminal"
    }
  ]
}
```

> **端口约定**：所有环境（本地调试 / 容器内 / 宿主机映射）统一 **9010**。如需更换：本地改 `.env` 的 `PORT`；Docker 改 `docker-compose.yml` 的 `ports` 与 `src/gunicorn.conf.py` 的 `bind`；并同步修改 Authentik 回调地址。

---

## 8. 扩展新目标应用

三步接入：

1. **Authentik 侧**：新建 OAuth2/OpenID Connect Provider（独立 Client ID/Secret），回调地址填 `https://<网关对外地址>/sso/<slug>/callback`，并创建 Application 绑定；
2. **网关配置**：`apps.yaml` 的 `apps` 段追加一组配置（authentik 凭证 + handler 名 + config 业务参数）；
3. **网关代码**（仅当协议逻辑与现有 handler 不同）：在 `handlers/` 新建模块实现 `handle()`，并在 `HANDLERS` 中登记：

```python
# handlers/my_app.py
NAME = "my_app"   # apps.yaml 的 handler 字段使用

def handle(user_info: dict, config: dict) -> str | None:
    """返回 http(s) 免密地址则直接 302；自定义协议地址（如 myapp://）渲染落地页唤起；失败返回 None"""
    # user_info: Authentik 返回的 OIDC userinfo（含 email / preferred_username / sub 等）
    # config:    apps.yaml 中该应用 config 段的业务参数
    ...

# handlers/__init__.py 中登记
from handlers import my_app

HANDLERS = {
    netease_mail.NAME: netease_mail,
    k3cloud.NAME: k3cloud,
    my_app.NAME: my_app,
}
```

完成后 `docker compose restart` 即生效（apps.yaml 与代码以挂载注入，无需重建镜像）。

---

## 9. 常见问题

- **回调后报 `redirect_uri mismatch`**：Authentik 中登记的回调地址与实际请求的 `redirect_uri` 不一致，逐字符比对协议/域名/端口/路径（注意回调格式为 `/sso/<slug>/callback`）。
- **访问 `/sso/<slug>` 返回 404**：该应用未在 `apps.yaml` 中配置，或配置校验失败被跳过（查看启动日志中的 `[ERROR] 应用 [...]` 告警）。
- **报「无法获取用户邮箱」**：Authentik 用户 email 属性为空，或 Provider 的 scope 未包含 `email`（网关请求 scope 为 `openid profile email`）。
- **本地 9010 端口启动失败**：端口被占用时可更换端口（改 `.env` 的 `PORT`），并同步修改 Authentik 回调地址。
- **网易接口返回失败**：检查 `apps.yaml` 中 `app_id` / `auth_code` / `org_open_id` 是否正确、网易侧应用是否启用；接口返回的 `code` / `message` 会记录在容器日志中，可据此排查。
- **金蝶登录失败（页面提示签名或授权无效）**：依次检查 ① `dbid` / `app_id` / `app_secret` 是否与 K3Cloud【第三方系统登录授权】中一致 ② K3Cloud 用户名与 Authentik 用户名是否一致 ③ 宿主机时钟是否 NTP 准确（时间戳超时窗失效）④ 该用户是否在授权范围内（若选了「指定用户登录」）。
- **点「金蝶K3（客户端）」图标无反应或提示找不到应用**：① 电脑未安装金蝶 ClickOnce 客户端（安装后重试）② 浏览器弹出的确认框被取消/拦截，点击落地页上的「启动」按钮即可 ③ 若网关日志已有 `entry=wpf` 签名记录则网关侧正常，问题在本机客户端环境。**标签页停在「正在启动金蝶K3客户端」提示页属预期行为**（客户端启动后可自动关闭或手动关闭）。

---

## 10. 附录

### 技术栈

- Python 3.12 + Flask 3
- authlib（OIDC 客户端）
- requests（调用目标应用 API）
- PyYAML（apps.yaml 多应用配置）
- gunicorn（生产运行）+ Docker Compose 部署

### 相关链接

- Authentik 官网：https://goauthentik.io/
- 反向场景（钉钉扫码登录 Authentik）：[dingtalk-oidc-gateway](https://github.com/sqkkyzx/dingtalk-oidc-gateway)
