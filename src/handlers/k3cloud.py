# -*- coding: utf-8 -*-
"""金蝶 K3Cloud 协议转换 handler（简单通行证 SimPas 模式）

按金蝶云社区官方文档实现（纯本地签名，零网络调用）：
① 六元素 [dbid, username, appid, appsecret, timestamp, permitcount]
   按字符序排序 → 无分隔符拼接 → SHA256 hex 得签名 signeddata
② 组装登录参数 JSON（origintype=SimPas）→ UTF-8 → Base64 → URL 编码 → ud 参数
③ 返回 {base_url}/html5/Index.aspx?ud=... 免密登录地址

前置条件（K3Cloud 管理端）：Administrator 登录数据中心后，
【系统管理】→【第三方系统登录授权】新增应用获得 appId / appSecret，
并记录数据中心 ID（dbid）；授权方式建议选"允许全部用户登录"
"""
import base64
import hashlib
import json
import logging
import time
from urllib.parse import quote

# handler 注册名（apps.yaml 中 handler 字段使用）
NAME = "k3cloud"

logger = logging.getLogger("sso-gateway.handler.k3cloud")


def handle(user_info, config):
    """协议转换入口：用 Authentik 用户名本地计算金蝶免密登录 URL（无网络调用）"""
    # K3Cloud 用户名 = Authentik 用户名（preferred_username 声明），须与 K3 侧完全一致
    username = user_info.get("preferred_username")
    if not username:
        logger.error("userinfo 中缺少 preferred_username，无法定位 K3Cloud 用户")
        return None

    base_url = (config.get("base_url") or "").rstrip("/")
    dbid = config.get("dbid") or ""
    app_id = config.get("app_id") or ""
    app_secret = config.get("app_secret") or ""
    if not all([base_url, dbid, app_id, app_secret]):
        logger.error(
            "k3cloud 配置不完整（需 base_url/dbid/app_id/app_secret），请检查 apps.yaml"
        )
        return None

    # 允许登录次数：0 可重复登录（默认），1 仅一次；需同时参与签名与 otherargs
    permitcount = str(config.get("permitcount", 0))

    # 签名：六元素按字符序排序后无分隔符拼接再求 SHA256（等价 .NET StringComparer.Ordinal，ASCII 内容排序结果一致）
    # 注意：拼接串含 app_secret 明文，任何日志均不得输出该串
    timestamp = str(int(time.time()))
    sortdata = "".join(sorted([dbid, username, app_id, app_secret, timestamp, permitcount]))
    signeddata = hashlib.sha256(sortdata.encode("utf-8")).hexdigest()

    # 登录参数：entryrole/formid/formtype/pkid 留空进主控首页；
    # otherargs 按金蝶官方格式用单引号包裹（json.dumps 的双引号会导致 K3 侧解析失败）
    payload = {
        "dbid": dbid,
        "username": username,
        "appid": app_id,
        "signeddata": signeddata,
        "timestamp": timestamp,
        "lcid": str(config.get("lcid", 2052)),
        "origintype": "SimPas",
        "entryrole": "",
        "formid": "",
        "formtype": "",
        "pkid": "",
        "otherargs": "|{'permitcount':'%s'}" % permitcount,
        "openmode": None,
    }

    # JSON → UTF-8 → Base64 → URL 编码（Base64 的 + / = 必须编码，否则 ud 参数解析出错）
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ud = quote(base64.b64encode(raw).decode("ascii"), safe="")

    logger.info(
        "k3cloud 签名完成（username=%s, timestamp=%s），生成免密登录地址", username, timestamp
    )
    return f"{base_url}/html5/Index.aspx?ud={ud}"
