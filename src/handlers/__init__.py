# -*- coding: utf-8 -*-
"""目标应用协议转换插件（handler）注册表

每个目标应用一个模块，模块需提供：
    handle(user_info: dict, config: dict) -> str | None
    - user_info：Authentik 返回的 OIDC userinfo（含 email / name / sub 等）
    - config：apps.yaml 中该应用 config 段的业务参数
    - 返回：目标系统免密登录 URL（302 目标）；失败返回 None

新增目标应用步骤：
1. 在本目录新建模块并实现 handle()
2. 在下方 HANDLERS 中登记
3. apps.yaml 中为对应应用指定 handler 名
"""

# handler 注册表：名称 → 模块
from handlers import k3cloud, netease_mail

HANDLERS = {
    # 网易企业邮箱（两次 API 调用换免密 Sign）
    netease_mail.NAME: netease_mail,
    # 金蝶 K3Cloud（本地 SHA256 签名生成免密登录 URL，零网络调用）
    k3cloud.NAME: k3cloud,
}
