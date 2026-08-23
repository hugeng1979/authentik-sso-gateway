# -*- coding: utf-8 -*-
"""网易企业邮箱协议转换 handler

按网易企业邮箱开放平台官方文档实现（两次 API 调用）：
① POST /api/pub/token/ssoAuthToken
   body: {appId, authCode, orgOpenId} → data.ssoAuthToken（单点登录辅助凭证）
② POST /api/sso/ssoSign
   headers: qiye-app-id / qiye-org-open-id / qiye-sso-auth-token
   body: {accountName, domain, pass_2fa} → data.sign + data.endpoint
最后 302 到 endpoint（为空时回退拼接 entryhz 免密登录地址）
"""
import logging

import requests

# handler 注册名（apps.yaml 中 handler 字段使用）
NAME = "netease_mail"

logger = logging.getLogger("sso-gateway.handler.netease_mail")

# 免密登录页回退地址模板（接口②未返回 endpoint 时使用）
SSO_LOGIN_URL = "https://entryhz.qiye.163.com/login/ssoLogin?sso_token={sign}&lang=0"

# 单点登录接口统一超时（秒），避免拖死 gunicorn worker
TIMEOUT = 10


def handle(user_info, config):
    """协议转换入口：用 Authentik userinfo 的 email 换网易免密登录 URL"""
    email = user_info.get("email")
    if not email or "@" not in email:
        logger.error("userinfo 中缺少有效 email，无法定位网易邮箱账号")
        return None

    # 按文档要求把邮箱拆为前缀与域名两部分
    account_name, domain = email.split("@", 1)

    token = _get_sso_auth_token(
        api_host=config.get("api_host", "https://api.qiye.163.com"),
        app_id=config.get("app_id", ""),
        auth_code=config.get("auth_code", ""),
        org_open_id=config.get("org_open_id", ""),
    )
    if not token:
        return None

    sign, endpoint = _get_sso_sign(
        api_host=config.get("api_host", "https://api.qiye.163.com"),
        app_id=config.get("app_id", ""),
        org_open_id=config.get("org_open_id", ""),
        sso_auth_token=token,
        account_name=account_name,
        domain=domain,
    )
    if not sign:
        return None

    # endpoint 为不带参数的基础地址，需把签名值作为查询参数拼上（兼容其自带 query 的情况）
    if endpoint:
        sep = "&" if "?" in endpoint else "?"
        return f"{endpoint}{sep}sso_token={sign}&lang=0"
    # endpoint 为空时回退拼接免密登录页
    return SSO_LOGIN_URL.format(sign=sign)


def _check_ok(data, step):
    """按文档统一判断响应是否成功（success 为 true 且 code 为 0），失败时记日志"""
    if not isinstance(data, dict) or data.get("success") is not True or data.get("code") != 0:
        logger.error("%s 调用失败, 响应: %s", step, data)
        return False
    return True


def _get_sso_auth_token(api_host, app_id, auth_code, org_open_id):
    """接口①：appId + authCode(应用授权码) + orgOpenId(企业OpenId) 换单点登录辅助凭证"""
    try:
        url = f"{api_host}/api/pub/token/ssoAuthToken"
        payload = {"appId": app_id, "authCode": auth_code, "orgOpenId": org_open_id}
        res = requests.post(url, json=payload, timeout=TIMEOUT).json()

        if not _check_ok(res, "获取单点登录辅助token"):
            return None

        token = (res.get("data") or {}).get("ssoAuthToken")
        if not token:
            logger.error("响应成功但缺少 ssoAuthToken: %s", res)
        return token

    except Exception:
        logger.exception("调用网易 ssoAuthToken 接口发生异常")
        return None


def _get_sso_sign(api_host, app_id, org_open_id, sso_auth_token, account_name, domain):
    """接口②：辅助凭证放请求头，邮箱前缀与域名放请求体，换签名值与跳转地址"""
    try:
        url = f"{api_host}/api/sso/ssoSign"
        # 单点登录接口的凭证通过 qiye-* 系列自定义请求头传递
        headers = {
            "qiye-app-id": app_id,
            "qiye-org-open-id": org_open_id,
            "qiye-sso-auth-token": sso_auth_token,
        }
        # pass_2fa=1：强制跳过二次验证，单点登录直达邮箱
        payload = {"accountName": account_name, "domain": domain, "pass_2fa": 1}
        res = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT).json()

        if not _check_ok(res, "获取单点登录签名值"):
            return None, None

        data = res.get("data") or {}
        sign = data.get("sign")
        if not sign:
            logger.error("响应成功但缺少 sign: %s", res)
            return None, None
        return sign, data.get("endpoint") or ""

    except Exception:
        logger.exception("调用网易 ssoSign 接口发生异常")
        return None, None
