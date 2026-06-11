from typing import Dict

import aiohttp

from .config import plugin_config

# 存储所有活跃会话：userid -> session
global_sessions: Dict[str, aiohttp.ClientSession] = {}

# 验证码状态管理：phone -> {event, code}
# 改为用 phone 作为 key，支持多手机号并发登录
verify_code_state: Dict[str, Dict] = {}

# 映射缓存
# 1. 编号 -> SN (全局唯一编号)
device_sn_map: Dict[str, str] = {}
# 2. SN -> UserID (用于查找该设备属于哪个账号)
device_owner_map: Dict[str, str] = {}

TARGET_GROUP = plugin_config.onething_target_group  # 消息通知群

# -------------------------- 失败统计配置 --------------------------
fail_count = {}
fail_messages = {
    1: "可能是网络波动",
    2: "好像有点问题",
    3: "坏了",
    4: "坏了坏了",
    5: "寄",
}


def reset_device_cache() -> None:
    """重置设备相关缓存，但不清理登录态"""

    # 设备映射缓存
    device_sn_map.clear()
    device_owner_map.clear()

    # 失败统计
    fail_count.clear()

    # 清空验证码等待状态
    verify_code_state.clear()

    from nonebot.log import logger

    logger.info("设备信息缓存已重置（登录态保留）")
