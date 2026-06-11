import json
import os
from typing import Dict

import aiohttp
from nonebot import get_driver
from nonebot.log import logger

from .config import DATA_DIR
from .state import global_sessions


def get_session_file(userid: str) -> str:
    return os.path.join(DATA_DIR, f"onethingsession_{userid}.json")


async def load_all_sessions():
    """启动时加载目录下所有会话文件"""
    logger.info("正在加载本地会话...")
    count = 0
    for filename in os.listdir(DATA_DIR):
        if filename.startswith("onethingsession_") and filename.endswith(".json"):
            userid = filename.replace("onethingsession_", "").replace(".json", "")
            filepath = os.path.join(DATA_DIR, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    cookies = json.load(f)

                # 创建session
                session = aiohttp.ClientSession(cookies=cookies)
                global_sessions[userid] = session
                count += 1
            except Exception as e:
                logger.error(f"加载会话文件 {filename} 失败: {e}")
    logger.success(f"成功加载 {count} 个历史会话")


async def save_session_to_file(userid: str, session: aiohttp.ClientSession):
    """保存会话到文件"""
    try:
        cookies = {cookie.key: cookie.value for cookie in session.cookie_jar}
        # 确保userid存在
        if not userid:
            # 尝试从cookie获取
            userid = cookies.get("userid")

        if userid:
            filepath = get_session_file(userid)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(cookies, f)
            logger.info(f"账号 {userid} 会话已保存")
        else:
            logger.warning("保存会话失败：无法获取userid")
    except Exception as e:
        logger.error(f"保存会话异常: {e}")


async def clear_session(userid: str):
    """清理无效会话"""
    logger.warning(f"正在清理账号 {userid} 的会话...")

    # 1. 内存清理
    if userid in global_sessions:
        try:
            await global_sessions[userid].close()
        except:
            pass
        del global_sessions[userid]

    # 2. 文件清理
    filepath = get_session_file(userid)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            logger.info(f"已删除会话文件: {filepath}")
        except Exception as e:
            logger.error(f"删除文件失败: {e}")


def parse_cookie_str(cookie_str: str) -> Dict[str, str]:
    """解析 Cookie 字符串或 JSON"""
    cookie_str = cookie_str.strip()
    cookies = {}

    # 尝试 JSON 解析
    if cookie_str.startswith("{"):
        try:
            return json.loads(cookie_str)
        except:
            pass

    # 尝试 key=value; 解析
    parts = cookie_str.split(';')
    for part in parts:
        if '=' in part:
            k, v = part.split('=', 1)
            cookies[k.strip()] = v.strip()
    return cookies


driver = get_driver()


@driver.on_startup
async def _():
    await load_all_sessions()
