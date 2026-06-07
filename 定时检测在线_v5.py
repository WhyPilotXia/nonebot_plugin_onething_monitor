import asyncio
import base64
import os
import random
import time
from typing import Dict, Optional, List, Tuple
import aiohttp
import json
import re
from nonebot import require, get_driver, get_bot
from datetime import datetime
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment, Message, ActionFailed, GroupMessageEvent
from nonebot.adapters.onebot.v11 import GROUP_ADMIN, GROUP_OWNER
from nonebot.params import CommandArg
from nonebot.exception import ApiNotAvailable
from PIL import Image
import traceback

# -------------------------- 插件元信息 --------------------------
__plugin_meta__ = PluginMetadata(
    name="网心云多账号版",
    description="自动登录网心云并执行定时请求，支持多账号(短信/Cookie)、设备列表聚合查询",
    usage="""
    1. 发送【登录】触发默认手机号登录
    2. 发送【登录 138xxxx】触发指定手机号登录
    3. 发送【登录 userid=xxx; sessionid=xxx】使用Cookie登录
    4. 发送【列表】查看所有账号设备并获取全局编号
    5. 发送【基础】触发请求，支持【基础 1】
    6. 发送【网络】查看多拨状态，支持【网络 1】或指定SN
    相比v4多一个多账号管理功能
    """,
)

try:
    scheduler = require("nonebot_plugin_apscheduler").scheduler
except Exception:
    scheduler = None

# -------------------------- 全局配置 --------------------------
TARGET_QQ = "2xxx"  # 管理员QQ，用于接收验证码请求
TARGET_GROUP = 123456  # 消息通知群
BOT_ID = "26xxx"
DEFAULT_PHONE = "17xxxx"  # 默认手机号

DATA_DIR = os.path.join(os.getcwd(), "onething")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# -------------------------- 全局状态管理 --------------------------
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

driver = get_driver()


# -------------------------- 辅助函数 --------------------------
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


@driver.on_startup
async def _():
    await load_all_sessions()


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


def reset_device_cache() -> None:
    """重置设备相关缓存，但不清理登录态"""
    global fail_count

    # 设备映射缓存
    device_sn_map.clear()
    device_owner_map.clear()

    # 失败统计
    fail_count.clear()

    # 清空验证码等待状态
    verify_code_state.clear()

    logger.info("设备信息缓存已重置（登录态保留）")


# -------------------------- 1. 验证码监听逻辑 --------------------------
def is_waiting_verify_code() -> Rule:
    async def _is_waiting(event: MessageEvent) -> bool:
        qq = str(event.user_id)
        msg_text = event.get_plaintext().strip()

        if qq != TARGET_QQ:
            return False

        if msg_text.startswith(('/', '、', '.')):
            return False

        # 检查是否正在等待验证码 (只检查 verify_code_state 是否为空是不够的，这里简化逻辑)
        return bool(verify_code_state)

    return Rule(_is_waiting)


verify_code_listener = on_message(rule=is_waiting_verify_code(), priority=1, block=True)


@verify_code_listener.handle()
async def handle_verify_code(event: MessageEvent):
    msg_text = event.get_plaintext().strip()

    # 解析输入，可能是 "123456" 或者 "13800000000 123456" (防止多账号同时登混淆)
    # 这里简单处理：如果只有一个手机号在等待，直接匹配；如果有多个，暂未做区分（假设管理员一次只登一个）

    if not msg_text.isdigit():
        return  # 忽略非数字消息

    # 找到第一个正在等待的手机号
    target_phone = None
    for phone, state in verify_code_state.items():
        if not state["event"].is_set():
            target_phone = phone
            break

    if target_phone:
        verify_code_state[target_phone]["code"] = msg_text
        verify_code_state[target_phone]["event"].set()
        # await verify_code_listener.finish(f"收到：{msg_text}")
    else:
        # 没有等待中的任务，忽略
        pass


# -------------------------- 2. 登录逻辑 --------------------------
async def wait_for_sms_code(phone: str) -> Optional[str]:
    """等待指定手机号的验证码"""
    verify_code_state[phone] = {
        "event": asyncio.Event(),
        "code": None
    }

    try:
        bot = get_bot(self_id=BOT_ID)
        await bot.send_group_msg(
            group_id=TARGET_GROUP,
            message=MessageSegment.at(TARGET_QQ) + f" 请发码 (5分钟内):"
        )

        await asyncio.wait_for(verify_code_state[phone]["event"].wait(), timeout=300)
        return verify_code_state[phone]["code"]
    except asyncio.TimeoutError:
        logger.warning(f"手机 {phone} 验证码等待超时")
        return None
    finally:
        if phone in verify_code_state:
            del verify_code_state[phone]


async def login_by_sms(phone: str) -> bool:
    """短信登录流程"""
    session = aiohttp.ClientSession()
    try:
        logger.info(f"开始请求验证码: {phone}")
        sms_url = "https://account.onethingcloud.com/v5/sms/send"
        timestamp = int(time.time() * 1000)

        common_headers = {
            "Host": "account.onethingcloud.com",
            "Connection": "keep-alive",
            "sec-ch-ua-platform": "Windows",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0",
            "Accept": "application/json",
            "sec-ch-ua": "\"Not;A=Brand\";v=\"99\", \"Microsoft Edge\";v=\"139\", \"Chromium\";v=\"139\"",
            "Content-Type": "application/json; charset=utf-8",
            "sec-ch-ua-mobile": "?0",
            "Origin": "https://www.onethingcloud.com",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://www.onethingcloud.com/",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }

        sms_payload = {
            "appId": "22017",
            "appName": "网心云",
            "clientVer": "139.0.0.0",
            "deviceModel": "PC-model",
            "platType": "0",
            "deviceSign": "8caf1b5a1036ab38beb058bdf0ff8dc3",
            "deviceName": "Edge",
            "OSVer": "Windows10",
            "isp": "NONE",
            "netType": "OTHER",
            "timestamp": timestamp,
            "phone": phone,
            "type": 30
        }

        async with session.post(sms_url, headers=common_headers, json=sms_payload) as resp:
            sms_res = await resp.json()
            if sms_res.get("errCode") != 0:
                logger.error(f"发送验证码失败: {sms_res}")
                await session.close()
                return False

        # 2. 等待输入
        code = await wait_for_sms_code(phone)
        if not code:
            await session.close()
            return False

        # 3. 提交登录
        logger.info(f"使用验证码 {code} 登录...")
        login_url = "https://account.onethingcloud.com/v5/user/smslogin"
        tk = sms_res["data"].get("tk", "")

        # 【修复3】login_payload 也要补全 'isp' 和 'netType'
        login_payload = {
            "appId": "22017",
            "appName": "网心云",
            "clientVer": "139.0.0.0",
            "deviceModel": "PC-model",
            "platType": "0",
            "deviceSign": "8caf1b5a1036ab38beb058bdf0ff8dc3",
            "deviceName": "Edge",
            "OSVer": "Windows10",
            "isp": "NONE",  # v5 原代码缺失
            "netType": "OTHER",  # v5 原代码缺失
            "timestamp": int(time.time() * 1000),
            "tk": tk,
            "phone": phone,
            "smsCode": code,
            "extra": "{\"inviteCode\":\"2d9e1766\",\"activityId\":30006}",
            "type": 1
        }

        # 登录时需要加 DNT 头
        login_headers = common_headers.copy()
        login_headers["DNT"] = "1"

        async with session.post(login_url, headers=login_headers, json=login_payload) as resp:
            login_res = await resp.json()

        if login_res.get("errCode") != 0:
            logger.error(f"登录API失败: {login_res}")
            await session.close()
            return False

        # 4. 获取 UserID 并保存
        cookies = {c.key: c.value for c in session.cookie_jar}

        # 优先从返回数据拿userid，拿不到再从cookie拿
        user_id = str(login_res.get("data", {}).get("userid", ""))
        if not user_id:
            user_id = cookies.get("userid")

        if user_id:
            global_sessions[user_id] = session
            await save_session_to_file(user_id, session)
            logger.success(f"账号 {user_id} (手机 {phone}) 登录成功")
            return True
        else:
            logger.error("登录成功但未获取到UserID")
            await session.close()
            return False

    except Exception as e:
        logger.error(f"短信登录异常: {e}")
        await session.close()
        return False


async def login_by_cookie(cookie_str: str) -> Tuple[bool, str]:
    """Cookie 登录验证流程"""
    cookies = parse_cookie_str(cookie_str)
    userid = cookies.get("userid")

    if not userid:
        return False, "Cookie中必须包含 userid 字段"

    session = aiohttp.ClientSession(cookies=cookies)

    # 验证 Cookie 是否有效（请求设备列表接口）
    try:
        url = "https://api-consolepro.onethingcloud.com/v1/device/device_list"
        # 简化的 payload
        payload = {"page": 1, "pageSize": 20}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0"
        }

        async with session.post(url, headers=headers, json=payload) as resp:
            data = await resp.json()
            if data.get("iRet") == 0:
                # 验证成功
                global_sessions[userid] = session
                await save_session_to_file(userid, session)
                return True, f"账号 {userid} Cookie 验证成功并已保存"
            else:
                await session.close()
                return False, f"Cookie 验证失败: {data.get('sMsg')}"
    except Exception as e:
        await session.close()
        return False, f"Cookie 验证异常: {e}"


# -------------------------- 3. 业务逻辑 (列表/网络) --------------------------

async def fetch_all_devices():
    """获取所有账号的设备列表"""
    all_devices = []
    device_sn_map.clear()
    device_owner_map.clear()
    invalid_userids = []
    current_shushu_id = 1

    logger.info(f"开始遍历 {len(global_sessions)} 个账号获取设备...")

    for userid, session in global_sessions.items():
        if session.closed:
            continue

        url = "https://api-consolepro.onethingcloud.com/v1/device/device_list"

        # 【修复】：恢复 v4 版本的完整请求头，防止被反爬
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "cache-control": "no-cache",
            "content-type": "application/json",
            "pragma": "no-cache",
            "sec-ch-ua": "\"Not(A:Brand\";v=\"8\", \"Chromium\";v=\"144\", \"Microsoft Edge\";v=\"144\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "referrer": "https://consolepro.onethingcloud.com/",
            "origin": "https://consolepro.onethingcloud.com",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0"
        }

        payload = {
            "page": 1,
            "pageSize": 20,
            "deviceGroup": [], "deviceType": [], "deviceStatus": [],
            "bizId": [], "fuzzyQuery": ""
        }

        try:
            async with session.post(url, headers=headers, json=payload) as resp:
                data = await resp.json()

                if data.get("iRet") != 0 or "未登录" in data.get("sMsg", ""):
                    logger.warning(f"账号 {userid} 登录失效，标记清理")
                    invalid_userids.append(userid)
                    continue

                device_list = data.get("data", {}).get("deviceInfoList", [])
                logger.info(f"账号 {userid} 获取到 {len(device_list)} 台设备")

                for device in device_list:
                    if device.get("deviceStatus") == 0:
                        continue

                    device["shushu_id"] = current_shushu_id
                    device["userid"] = userid

                    sn = device["sn"]
                    device_sn_map[str(current_shushu_id)] = sn
                    device_owner_map[sn] = userid

                    all_devices.append(device)
                    current_shushu_id += 1

        except Exception as e:
            logger.error(f"账号 {userid} 获取列表异常: {e}")

    for uid in invalid_userids:
        await clear_session(uid)

    return all_devices


async def get_session_by_sn(sn: str) -> Optional[aiohttp.ClientSession]:
    """根据 SN 自动获取对应的 Session"""
    owner_id = device_owner_map.get(sn)
    if not owner_id:
        # 如果缓存里没有，可能是没刷新列表，或者是新设备
        # 这里为了稳妥，暂不尝试所有session盲扫，建议先刷新列表
        logger.warning(f"SN {sn} 未在缓存中找到归属账号，请尝试先执行 /列表")
        return None

    session = global_sessions.get(owner_id)
    if not session or session.closed:
        logger.warning(f"SN {sn} 归属账号 {owner_id} 会话无效")
        return None

    return session


async def send_network_request(sn: str, only_return_path: bool = False) -> Optional[str]:
    """单设备网络状态查询 (支持多账号，恢复 v4 完整 Header)"""
    bot = get_bot(self_id=BOT_ID)

    session = await get_session_by_sn(sn)
    if not session:
        if not only_return_path:
            await bot.send_group_msg(group_id=TARGET_GROUP, message=f"无法找到设备 {sn} 的有效会话，请先 /列表")
        return None

    owner_id = device_owner_map.get(sn)

    try:
        final_url = "https://api-consolepro.onethingcloud.com/v1/device/generate_url"
        final_payload = {"sn": sn}

        # 【修复】：恢复 v4 版本的完整请求头，包含 Origin 和 Host
        final_headers = {
            "Host": "api-consolepro.onethingcloud.com",
            "Connection": "keep-alive",
            "sec-ch-ua-platform": "Windows",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0",
            "Content-Type": "application/json",
            "Origin": "https://consolepro.onethingcloud.com",
            "Referer": "https://consolepro.onethingcloud.com/"
        }

        async with session.post(final_url, headers=final_headers, json=final_payload) as resp:
            response_text = await resp.text()

            if resp.status in [401, 403] or "未登录" in response_text or '"errCode":1001' in response_text.replace(" ",
                                                                                                                   ""):
                logger.warning(f"账号 {owner_id} Token失效，执行清理")
                await clear_session(owner_id)
                if not only_return_path:
                    await bot.send_group_msg(group_id=TARGET_GROUP, message=f"账号 {owner_id} 登录已失效")
                return None

            try:
                final_result = json.loads(response_text)
                if "data" in final_result and "url" in final_result["data"]:
                    frp_url = final_result["data"]["url"]
                    base_url = frp_url.split('?')[0]
                    domain = base_url.split('http://')[1]
                    pppoe_api_url = f"{base_url}/v1.0/devices/multpppoe/status"

                    # 这里的 Header 不需要太复杂，但 v4 加了 Accept
                    frp_headers = {
                        "Host": domain,
                        "Referer": frp_url,
                        "User-Agent": final_headers["User-Agent"],
                        "Connection": "keep-alive",
                        "Accept": "*/*"
                    }

                    async with session.get(pppoe_api_url, headers=frp_headers) as resp1:
                        pppoe_text = await resp1.text()
                        pppoe_result = json.loads(pppoe_text)

                        multidial_data = pppoe_result.get("multidial", [])
                        if multidial_data:
                            img_path = save_network_table_to_local(multidial_data, sn)
                        else:
                            # 没多拨数据时返回原始JSON图
                            img_path = save_info_to_local(pppoe_result)

                        if only_return_path:
                            return img_path
                        else:
                            await bot.send_group_msg(group_id=TARGET_GROUP, message=MessageSegment.image(img_path))
                            return img_path
                else:
                    if not only_return_path:
                        await bot.send_group_msg(group_id=TARGET_GROUP,
                                                 message=f"{sn} 获取FRP失败: {final_result.get('sMsg')}")
                    return None

            except Exception as e:
                logger.error(f"网络状态解析失败: {e}")
                return None

    except Exception as e:
        logger.error(f"网络请求网络层异常: {e}")
        return None


async def send_final_request(sn: str) -> Optional[Dict]:
    """
    查询设备基础信息 (对应 v4 的 send_final_request，适配多账号)
    """
    bot = get_bot(self_id=BOT_ID)

    # 自动查找对应 Session
    session = await get_session_by_sn(sn)
    if not session:
        await bot.send_group_msg(group_id=TARGET_GROUP, message=f"无法找到设备 {sn} 的有效会话，请先 /列表")
        return None

    owner_id = device_owner_map.get(sn)

    try:
        final_url = "https://api-consolepro.onethingcloud.com/v1/device/generate_url"
        final_payload = {"sn": sn}

        # 使用完整 Header
        final_headers = {
            "Host": "api-consolepro.onethingcloud.com",
            "Connection": "keep-alive",
            "sec-ch-ua-platform": "Windows",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://consolepro.onethingcloud.com",
            "Referer": "https://consolepro.onethingcloud.com/"
        }

        async with session.post(final_url, headers=final_headers, json=final_payload) as resp:
            response_text = await resp.text()

            # 失效处理
            if resp.status in [401, 403] or "未登录" in response_text or '"errCode":1001' in response_text.replace(" ",
                                                                                                                   ""):
                await clear_session(owner_id)
                await bot.send_group_msg(group_id=TARGET_GROUP, message=f"账号 {owner_id} 登录失效")
                return None

            try:
                final_result = json.loads(response_text)
                if "data" in final_result and "url" in final_result["data"]:
                    frp_url = final_result["data"]["url"]
                    base_url = frp_url.split('?')[0]
                    domain = base_url.split('http://')[1]
                    # 注意：基础信息查的是 /status，不是 /multpppoe/status
                    status_api_url = f"{base_url}/v1.0/devices/status"

                    frp_headers = final_headers.copy()
                    frp_headers["Host"] = domain
                    frp_headers["Referer"] = frp_url

                    # 基础信息 Header
                    async with session.get(status_api_url, headers=frp_headers) as resp1:
                        frp_text = await resp1.text()
                        frp_result = json.loads(frp_text)

                        img_path = save_info_to_local(frp_result)
                        await bot.send_group_msg(group_id=TARGET_GROUP, message=MessageSegment.image(img_path))
                        return frp_result
                else:
                    msg = f"未获取到FRP URL: {final_result.get('sMsg', '未知错误')}"
                    await bot.send_group_msg(group_id=TARGET_GROUP, message=msg)


            except Exception as e:
                logger.error(f"解析基础信息失败: {e}")
                logger.error(f"原始返回内容: {frp_text[:1000]}")  # 防止太长
                logger.error(f"状态码: {resp1.status}")
                logger.error(traceback.format_exc())
                return None

    except Exception as e:
        logger.error(f"基础请求异常: {e}")
        return None

# -------------------------- 绘图工具 (复用优化) --------------------------
# 保持原有的 save_network_table_to_local 和 save_info_to_local 逻辑
# 为了节省篇幅，这里假设这两个函数已存在 (基本不用改动，只需确保文件名唯一性)

def save_network_table_to_local(multidial_list: list, sn: str) -> str:
    import matplotlib.pyplot as plt
    import os
    import time

    # 1. 字体设置
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
    plt.rcParams['axes.unicode_minus'] = False

    if not multidial_list:
        return ""

    # 2. 自动识别表头
    headers = list(multidial_list[0].keys())

    # 3. 处理数据行 & 准备数据
    cell_text = []
    for item in multidial_list:
        row_data = []
        for key in headers:
            val = str(item.get(key, ""))
            if key == "username" and len(val) > 4:
                val = val[4:]
            elif key in ["ipaddr", "gateway"] and "." in val:
                parts = val.split(".")
                if len(parts) > 2:
                    val = ".".join(parts[2:])
            elif key == "ipaddr6" and ":" in val:
                parts = val.split(":")
                if len(parts) > 1:
                    val = ":".join(parts[1:])
            row_data.append(val)
        cell_text.append(row_data)

    def get_visual_length(s):
        length = 0
        for char in str(s):
            if '\u4e00' <= char <= '\u9fff':
                length += 2
            else:
                length += 1
        return length

    col_widths_raw = []
    for i in range(len(headers)):
        col_values = [headers[i]] + [row[i] for row in cell_text]
        max_len = max(get_visual_length(val) for val in col_values) + 2
        col_widths_raw.append(max_len)

    total_char_width = sum(col_widths_raw)
    col_widths_ratios = [w / total_char_width for w in col_widths_raw]

    num_rows = len(cell_text)
    fig_width = max(12, total_char_width * 0.13)
    fig_height = max(2, num_rows * 0.35 + 1.0)

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis('off')

    table = ax.table(
        cellText=cell_text,
        colLabels=headers,
        colWidths=col_widths_ratios,
        loc='center',
        cellLoc='center'
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.8)
    # 设置颜色
    # 1. 找到对应的列索引
    down_idx = -1
    up_idx = -1

    if "downspeed" in headers:
        down_idx = headers.index("downspeed")
    if "upspeed" in headers:
        up_idx = headers.index("upspeed")

    # 2. 遍历所有单元格设置颜色
    # cells 是一个字典，key是 (行, 列)，value是单元格对象
    # row=0 是表头，row>=1 是数据
    cells = table.get_celld()

    for (row, col), cell in cells.items():
        # 如果是 downspeed 列
        if col == down_idx:
            # 设置为淡绿色 (Hex: #ccffcc)
            cell.set_facecolor("#ccffcc")
        # 如果是 upspeed 列
        elif col == up_idx:
            # 设置为淡黄色 (Hex: #ffffcc)
            cell.set_facecolor("#ffffcc")
    plt.title(f"设备 [{sn}] 网络状态详情", fontsize=14, pad=20)

    # 修改：文件名加入 SN，防止批量生成时冲突
    filename = f"network_status_{sn}.png"
    file_path = os.path.join(DATA_DIR, filename)

    plt.savefig(file_path, format='png', bbox_inches='tight', pad_inches=0.1)
    plt.close(fig)
    return file_path

def save_info_to_local(data: dict) -> str:
    import matplotlib.pyplot as plt
    import json
    import os

    # 1. 字体设置 (Windows下支持中文)
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
    plt.rcParams['axes.unicode_minus'] = False

    formatted_text = json.dumps(data, indent=2, ensure_ascii=False)
    lines = formatted_text.split('\n')
    num_lines = len(lines)

    fig_height = num_lines * 0.19 + 0.3

    # 创建画布
    fig, ax = plt.subplots(figsize=(10, fig_height))
    ax.axis('off')

    ax.text(
        0.01, 0.99,
        formatted_text,
        transform=ax.transAxes,
        fontsize=11,
        verticalalignment='top'
    )


    # 3. 使用固定文件名 (覆盖写入)
    filename = "onething_device_list.png"
    file_path = os.path.join(DATA_DIR, filename)

    plt.savefig(file_path, format='png', bbox_inches='tight', pad_inches=0.1)
    plt.close(fig)
    return file_path



def merge_images_vertically(image_paths: list) -> str:
    """将多张图片按最大宽度缩放后垂直拼接"""
    if not image_paths:
        return ""

    images = []
    max_width = 0

    # 1. 读取所有图片并找到最大宽度
    for path in image_paths:
        if os.path.exists(path):
            try:
                img = Image.open(path)
                if img.width > max_width:
                    max_width = img.width
                images.append(img)
            except Exception as e:
                logger.error(f"读取图片失败 {path}: {e}")

    if not images:
        return ""

    # 2. 缩放图片并计算总高度
    resized_images = []
    total_height = 0

    for img in images:
        # 如果宽度不一致，按比例缩放
        if img.width != max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            # 使用 LANCZOS 滤镜进行高质量缩放
            img_resized = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
        else:
            img_resized = img

        resized_images.append(img_resized)
        total_height += img_resized.height

    # 3. 创建画布并拼接
    # 增加一点白色背景间距
    padding = 20
    final_height = total_height + (len(resized_images) - 1) * padding

    new_img = Image.new('RGB', (max_width, final_height), (255, 255, 255))

    current_y = 0
    for img in resized_images:
        new_img.paste(img, (0, current_y))
        current_y += img.height + padding

    # 4. 保存最终图片

    final_filename = f"network_status.png"
    final_path = os.path.join(DATA_DIR, final_filename)

    new_img.save(final_path)

    # 可选：清理临时生成的单张图片
    for path in image_paths:
        try: os.remove(path)
        except: pass

    return final_path


# -------------------------- 4. 命令注册 --------------------------

login_cmd = on_command("登录", permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER, priority=5, block=True)


@login_cmd.handle()
async def handle_login(args: Message = CommandArg()):
    arg_text = args.extract_plain_text().strip()

    # 场景1: 默认手机号
    if not arg_text:
        await login_cmd.send(f"开始登录默认手机号，请留意验证码...")
        success = await login_by_sms(DEFAULT_PHONE)
        if success:
            await login_cmd.finish("登录成功！")
        else:
            await login_cmd.finish("登录失败，请检查日志。")

    # 场景2: 指定手机号 (11位数字)
    elif arg_text.isdigit() and len(arg_text) == 11:
        await login_cmd.send(f"开始登录指定手机号，请留意验证码...")
        success = await login_by_sms(arg_text)
        if success:
            await login_cmd.finish(f"登录成功！")
        else:
            await login_cmd.finish("登录失败，请检查日志。")

    # 场景3: Cookie 登录 (包含 userid)
    elif "userid" in arg_text:
        await login_cmd.send("检测到 Cookie，正在验证...")
        success, msg = await login_by_cookie(arg_text)
        await login_cmd.finish(msg)

    else:
        await login_cmd.finish(
            "参数错误。用法：\n/登录 (默认手机)\n/登录 138xxxx (指定手机)\n/登录 userid=xxx... (Cookie)")



reset_device_cmd = on_command(
    "重置设备信息",
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    priority=5,
    block=True
)

@reset_device_cmd.handle()
async def handle_reset_device_info():
    reset_device_cache()
    await reset_device_cmd.finish(
        "已重置设备列表、设备归属、失败次数；登录态已保留。"
    )



list_cmd = on_command("列表", permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER, priority=5, block=True)


@list_cmd.handle()
async def handle_list():
    if not global_sessions:
        await list_cmd.finish("当前无任何已登录账号，请先使用 /登录")

    await list_cmd.send("正在聚合查询所有账号设备...")
    devices = await fetch_all_devices()

    if not devices:
        await list_cmd.finish("查询完成，未找到任何在线设备或Session已全部失效")

    # 生成展示数据
    display_data = {
        "count": len(devices),
        "accounts": list(global_sessions.keys()),
        "devices": devices  # 图片里展示所有数据
    }

    img = save_info_to_local(display_data)
    await list_cmd.finish(MessageSegment.image(img))


manual_request = on_command("基础", permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER, priority=5, block=True)


@manual_request.handle()
async def handle_manual_request(args: Message = CommandArg()):
    arg = args.extract_plain_text().strip()
    if not device_sn_map: await fetch_all_devices()

    target_sn = ""
    if arg in device_sn_map:
        target_sn = device_sn_map[arg]
        await manual_request.send(f"查询设备 #{arg} ({target_sn})")
    elif len(arg) > 5:
        target_sn = arg
        await manual_request.send(f"查询SN {target_sn}")
    else:
        await manual_request.finish("请输入 /列表 中的编号")

    await send_final_request(target_sn)

manual_network = on_command("网络", permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER, priority=5, block=True)


@manual_network.handle()
async def handle_network(bot: Bot, event:MessageEvent, args: Message = CommandArg()):
    global TARGET_GROUP
    if isinstance(event,GroupMessageEvent):
        TARGET_GROUP = event.group_id  # 临时切换至当前群。定时任务执行时会切换会默认
    arg_text = args.extract_plain_text().strip()

    # 如果还没有缓存映射，先尝试获取一次
    if not device_sn_map:
        await manual_network.send("本地缓存为空，正在刷新设备列表...")
        await fetch_all_devices()

    if arg_text:
        # 指定查询
        target_sn = ""
        if arg_text in device_sn_map:
            target_sn = device_sn_map[arg_text]
            await manual_network.send(f"查询设备 #{arg_text} (SN: {target_sn})...")
        elif len(arg_text) > 5:  # 假设是SN
            target_sn = arg_text
            await manual_network.send(f"查询指定SN: {target_sn}...")
        else:
            await manual_network.finish("找不到该编号设备，请先 /列表")

        await send_network_request(target_sn)

    else:
        # 批量查询
        count = len(device_sn_map)
        if count == 0:
            await manual_network.finish("无设备可查询")

        await manual_network.send(f"开始批量查询 {count} 台设备...查询，轻而易举啊")
        await execute_batch_network_check(bot)


# -------------------------- 失败统计配置 --------------------------
fail_count = {}
fail_messages = {
    1: "可能是网络波动",
    2: "好像有点问题",
    3: "坏了",
    4: "坏了坏了",
    5: "寄"
}
async def execute_batch_network_check(bot: Bot):
    """
    执行批量网络检查逻辑（多账号适配版，保留完整错误统计逻辑）
    """
    global fail_count

    # 1. 检查设备列表缓存
    # 如果本地缓存为空，说明可能重启过或者尚未获取过列表
    if not device_sn_map:
        logger.info("本地设备列表为空，正在自动获取...")
        devices = await fetch_all_devices()

        # 如果获取不到设备（可能是所有账号都过期了，或者真的没设备）
        if not devices:
            # 区分一下是没账号还是账号失效
            err_msg_suffix = "可能是没登录，快登录啊啊啊" if not global_sessions else "可能是所有账号登录均已失效"

            await bot.send_group_msg(
                group_id=TARGET_GROUP,
                message=MessageSegment.at(TARGET_QQ) + f"execute_batch_network_check:获取设备列表失败，{err_msg_suffix}"
            )
            logger.warning("execute_batch_network_check:获取设备列表失败，无法进行批量查询。")
            return

    msg_count = len(device_sn_map)
    logger.info(f"开始批量查询 {msg_count} 台设备的网络状态...")

    # 2. 遍历请求并收集图片路径
    img_paths = []

    # 这里的 device_sn_map 是 shushu_id -> sn
    # 遍历它能保证按照编号顺序请求
    for shushu_id, sn in device_sn_map.items():
        # 增加延时防止接口限频
        await asyncio.sleep(1)

        # 调用请求函数，只获取路径
        path = await send_network_request(sn, only_return_path=True)
        if path:
            # 成功则重置该 SN 的失败计数
            fail_count[sn] = 0
            img_paths.append(path)
        else:
            # 失败则累加计数
            if sn not in fail_count:
                fail_count[sn] = 1
            else:
                fail_count[sn] += 1
            logger.warning(f"设备 {sn} (编号{shushu_id}) 获取网络状态图失败")

    # 3. 失败报警逻辑 (完全保留原版)
    if any(count > 0 for count in fail_count.values()):
        # 筛选出失败次数 > 0 的设备
        failed_devices = {sn: cnt for sn, cnt in fail_count.items() if cnt > 0}
        # 筛选出关注设备
        interested_devices = ["XRVDVHL8N5KIK7S5", "XRVDEDE7FCCCA04A"]
        interested_failed_devices = {
            sn: cnt
            for sn, cnt in fail_count.items()
            if sn in interested_devices and cnt > 0
        }

        max_fail_count = max(interested_failed_devices.values()) if interested_failed_devices else 0  # 只关注重要设备
        fail_message = fail_messages.get(max_fail_count, f"连续{max_fail_count}次了，鼠鼠快醒醒吧！")



        await bot.send_group_msg(
            group_id=TARGET_GROUP,
            message=(MessageSegment.at(TARGET_QQ)+"重要设备：" if 20 > max_fail_count > 2 else "* ") + f"{str(failed_devices)}似乎掉线了。 {fail_message}"
        )

    # 如果一张图都没生成，直接返回
    if not img_paths:
        logger.warning("所有设备查询均失败或未生成图片。")
        return

    # 4. 合并图片并发送
    try:
        final_path = merge_images_vertically(img_paths)
        if final_path:
            # 发送给目标群
            await bot.send_group_msg(
                group_id=TARGET_GROUP,
                message=MessageSegment.image(final_path)
            )
            logger.success("批量网络状态图发送成功")
        else:
            logger.error("图片合并失败")
    except Exception as e:
        logger.error(f"合并或发送过程出错: {e}")

# 定时任务
if scheduler:
    @scheduler.scheduled_job("cron", hour="*/1", minute="*/30", id="onething_batch", misfire_grace_time=3600)
    async def task_entry():
        logger.info("网心云定时任务开始")
        global TARGET_GROUP
        TARGET_GROUP = 1072293499  # 消息通知群
        try:
            bot = get_bot(self_id=BOT_ID)
            await execute_batch_network_check(bot)
        except Exception as e:
            logger.error(f"定时任务异常: {e}")
