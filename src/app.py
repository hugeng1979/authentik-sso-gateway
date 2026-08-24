# -*- coding: utf-8 -*-
"""Authentik 单点登录中转网关（多应用版）

为一个 Flask 服务同时接入多个不支持标准协议（OAuth2/OIDC/SAML）的目标应用：
- 每个目标应用在 apps.yaml 中配置一段（含独立的 Authentik Client 凭证与业务参数）
- 每个目标应用在 handlers/ 下实现一个协议转换插件（拿到 userinfo 后换目标系统免密地址）

通用流程（以 /sso/<slug> 为例）：
1. 用户访问 /sso/<slug> → 302 到 Authentik 进行 OIDC 认证（该应用独立的 client）
2. Authentik 认证成功后回调 /sso/<slug>/callback
3. 网关用授权码换取 userinfo（email 等声明）
4. 网关调用该应用 handler 的 handle(userinfo, config) 得到目标系统免密登录 URL
5. 302 重定向到该 URL，完成单点登录
"""

import html
import json
import logging
import logging.handlers
import os
import sys

import yaml
from authlib.integrations.flask_client import OAuth
from flask import Flask, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from handlers import HANDLERS

# ================= 日志配置 =================
# 双输出：stdout（docker logs 实时可看）+ 按天轮转文件（logs/sso-gateway.log，保留 30 天自动清理）
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.TimedRotatingFileHandler(
            "logs/sso-gateway.log", when="midnight", backupCount=30, encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("sso-gateway")

app = Flask(__name__)

# ================= 全局配置（应用级配置在 apps.yaml） =================
# Flask 会话密钥：生产环境必须替换为复杂随机字符串
app.secret_key = os.getenv("SECRET_KEY", "RANDOM_SECRET_KEY_FOR_SESSION")

# 网关对外访问地址（含协议、不含末尾斜杠），反向代理场景用于生成回调地址；留空则按请求头自动推断
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

# 多应用配置文件路径（容器内通过 volume 挂载，改配置无需重建镜像）
APPS_CONFIG_FILE = os.getenv("APPS_CONFIG_FILE", "apps.yaml")
# ============================================

# 反向代理（Nginx）后部署时，依据 X-Forwarded-* 头修正回调地址的协议与主机
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


def load_apps_config():
    """加载并校验 apps.yaml，返回 {slug: 应用配置} 字典；单个应用配置不合法只跳过并告警"""
    if not os.path.exists(APPS_CONFIG_FILE):
        logger.error("配置文件 %s 不存在，网关将没有任何可用应用", APPS_CONFIG_FILE)
        return {}

    with open(APPS_CONFIG_FILE, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    apps_config = {}
    for slug, conf in (raw.get("apps") or {}).items():
        # 校验必填项：slug 规则、Authentik 凭证、handler 是否已注册
        if (
            not isinstance(slug, str)
            or not slug.replace("-", "").replace("_", "").isalnum()
        ):
            logger.error(
                "应用 slug 不合法（仅允许字母数字连字符下划线）: %r，已跳过", slug
            )
            continue
        authentik = (conf or {}).get("authentik") or {}
        handler_name = (conf or {}).get("handler") or ""
        missing = [
            k
            for k in ("client_id", "client_secret", "metadata_url")
            if not authentik.get(k)
        ]
        if missing:
            logger.error("应用 [%s] Authentik 配置缺少 %s，已跳过", slug, missing)
            continue
        if handler_name not in HANDLERS:
            logger.error(
                "应用 [%s] 的 handler %r 未注册（可用: %s），已跳过",
                slug,
                handler_name,
                list(HANDLERS),
            )
            continue
        apps_config[slug] = conf
        logger.info("应用 [%s] 配置加载成功（handler=%s）", slug, handler_name)

    return apps_config


# 启动时加载全部应用配置
APPS = load_apps_config()

# 为每个应用注册独立的 Authentik OAuth 客户端（key 为 authentik_<slug>）
oauth = OAuth(app)
for slug, conf in APPS.items():
    authentik = conf["authentik"]
    oauth.register(
        name=f"authentik_{slug}",
        client_id=authentik["client_id"],
        client_secret=authentik["client_secret"],
        server_metadata_url=authentik["metadata_url"],
        client_kwargs={"scope": "openid profile email"},
    )


@app.route("/")
def index():
    """根路径：出于安全考虑，不暴露任何信息，直接返回 404"""
    return "404 Not Found", 404


@app.route("/healthz")
def healthz():
    """健康检查端点：供 Docker healthcheck 与负载均衡探活使用"""
    return "ok", 200


def build_redirect_uri(slug):
    """生成指定应用的回调地址：优先使用 BASE_URL 环境变量，否则按请求头自动推断"""
    if BASE_URL:
        return f"{BASE_URL}/sso/{slug}/callback"
    return url_for("sso_callback", slug=slug, _external=True)


# 自定义协议唤起落地页模板：页面加载时自动尝试唤起协议处理器（如 k3cloud:// 客户端），
# 但 Chrome/Edge 安全策略要求脚本跳转协议需"瞬时用户手势"，过期则静默拦截——
# 故主 UI 为醒目大按钮兜底（腾讯会议/Zoom 网页唤起同款模式）；3 秒后尝试自动关闭标签页
LAUNCH_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>启动金蝶K3客户端</title>
<style>
  body {{ font-family: system-ui, sans-serif; text-align: center; padding-top: 70px; color: #333; background: #f5f6f8; }}
  .btn {{ display: inline-block; margin-top: 24px; padding: 14px 48px; font-size: 18px;
         color: #fff; background: #1a6fce; border: none; border-radius: 8px;
         cursor: pointer; text-decoration: none; }}
  .btn:hover {{ background: #1559a8; }}
  .tip {{ margin-top: 18px; color: #999; font-size: 14px; }}
</style>
</head>
<body>
<h2 id="title">启动金蝶K3客户端</h2>
<a class="btn" id="launch-btn" href="{launch_url}">启动金蝶K3客户端</a>
<p class="tip" id="tip">若未自动启动，请点击上方按钮（浏览器安全要求）</p>
<p class="tip">客户端已启动时，此页面可安全关闭</p>
<script>
  var launchUrl = {launch_url_json};
  // 页面加载即自动尝试唤起（Safari/Firefox 无手势限制可零点击成功；Chrome/Edge 会被静默拦截，靠按钮兜底）
  location.href = launchUrl;
  // 尝试关闭标签页（浏览器仅允许脚本关闭脚本打开的窗口，失败则停留提示页）
  function tryClose() {{ try {{ window.close(); }} catch (e) {{}} }}
  // 自动唤起路径：3 秒后尝试关闭
  setTimeout(tryClose, 3000);
  // 点击按钮路径：唤起后多次尝试关闭，并更新提示文案给出明确收尾指引
  document.getElementById("launch-btn").addEventListener("click", function () {{
    setTimeout(function () {{
      tryClose(); setTimeout(tryClose, 1000); setTimeout(tryClose, 2000);
      var tip = document.getElementById("tip");
      if (tip) {{
        tip.textContent = "客户端已启动，本页面即将关闭；若未自动关闭，可手动关闭";
      }}
    }}, 500);
  }});
</script>
</body>
</html>"""


def render_launch_page(launch_url):
    """渲染自定义协议唤起落地页：URL 经 HTML 与 JS 双重转义后嵌入，防注入"""
    return LAUNCH_PAGE.format(
        launch_url=html.escape(launch_url, quote=True),
        # JS 嵌入值：json.dumps 已处理引号/反斜杠等，再把 "<" 转为 \u003c 防 "</script>" 提前闭合；
        # script 块内浏览器按原始 JS 解析，不能做 HTML 实体转义
        launch_url_json=json.dumps(launch_url).replace("<", "<\\u003c"),
    )


@app.route("/sso/<slug>")
def sso_entry(slug):
    """单点登录入口：引导用户去 Authentik 认证（使用该应用独立的 OAuth 客户端）"""
    if slug not in APPS:
        return "未知的单点登录应用", 404
    client = oauth.create_client(f"authentik_{slug}")
    return client.authorize_redirect(build_redirect_uri(slug))


@app.route("/sso/<slug>/callback")
def sso_callback(slug):
    """认证回调：换取 userinfo 后交由该应用的 handler 生成免密登录地址并 302"""
    if slug not in APPS:
        return "未知的单点登录应用", 404
    try:
        # 1. 用该应用的 OAuth 客户端换取 Token 并解析用户信息
        client = oauth.create_client(f"authentik_{slug}")
        token = client.authorize_access_token()
        user_info = token.get("userinfo") or {}

        if not user_info:
            logger.error("应用 [%s] 无法从 Authentik 获取用户信息", slug)
            return "认证失败：无法从 Authentik 获取用户信息。", 400

        # 2. 调用该应用的 handler 做协议转换，得到目标系统免密登录地址
        conf = APPS[slug]
        handler = HANDLERS[conf["handler"]]
        login_url = handler.handle(user_info, conf.get("config") or {})

        if login_url:
            # http(s) 地址直接 302；自定义协议（如 k3cloud://）渲染落地页唤起，
            # 避免浏览器把协议地址当导航目标导致标签页停留在"加载中"
            if login_url.startswith(("http://", "https://")):
                return redirect(login_url)
            return render_launch_page(login_url)

        logger.error(
            "应用 [%s] handler=%s 协议转换失败, userinfo=%s",
            slug,
            conf["handler"],
            user_info,
        )
        return f"单点登录失败：应用 [{slug}] 协议转换失败", 500

    except Exception:
        logger.exception("处理应用 [%s] 的 Authentik 回调时发生异常", slug)
        return "系统错误，请查看网关日志", 500


if __name__ == "__main__":
    # 仅本地调试使用；生产环境请使用 gunicorn（见 Dockerfile）
    # 端口可通过 PORT 环境变量调整，默认与生产统一为 9010
    # 本地调用时__name__ == __main__才会执行，生产环境__name__=app
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 9010)), debug=True)
