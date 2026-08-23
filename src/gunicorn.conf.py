# -*- coding: utf-8 -*-
"""gunicorn 生产级 WSGI 配置"""
import os

# 监听地址：统一使用 9010 端口（宿主机/容器/本地调试一致）
bind = "0.0.0.0:9010"

# 进程模型：2 worker × 4 线程，足够应对单点登录的并发量
workers = int(os.getenv("GUNICORN_WORKERS", 2))
threads = 4

# 请求超时（网易 API 偶尔较慢，留足余量）
timeout = 60

# 同域路径发布前缀（如雷池 WAF 将 https://域名/jandarsso 透传到本服务时设 /jandarsso）：
# 非空时 Flask 依据 WSGI 标准自动剥前缀分发路由，url_for 生成的回调地址自动带上前缀
_script_name = os.getenv("SCRIPT_NAME", "")
raw_env = [f"SCRIPT_NAME={_script_name}"] if _script_name else []

# 日志输出到标准输出，便于 docker logs 查看
accesslog = "-"
errorlog = "-"
