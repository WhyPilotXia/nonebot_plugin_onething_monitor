import asyncio
import base64
import os
import random
import time
from typing import Dict, Optional
import aiohttp
import json
from nonebot import require, get_driver, get_bot,get_bots
from datetime import datetime, timedelta
from collections import defaultdict
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule, to_me
from nonebot import on_command, on_startswith, on_keyword, on_fullmatch, on_message
from nonebot.matcher import Matcher
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11 import GROUP_ADMIN, GROUP_OWNER, GROUP_MEMBER
from nonebot.typing import T_State
from nonebot.log import logger
from nonebot.params import ArgPlainText, CommandArg, ArgStr
from nonebot.adapters.onebot.v11 import Bot, GroupIncreaseNoticeEvent, \
    MessageSegment, Message, GroupMessageEvent, Event, escape, ActionFailed
from io import BytesIO

# -------------------------- 插件元信息（可选） --------------------------
__plugin_meta__ = PluginMetadata(
    name="网心云定时任务",
    description="自动登录网心云并执行定时请求，支持QQ验证码输入",
    usage="""
    1. 发送【网心云登录】手动触发登录
    2. 定时任务自动执行最终请求（30min）
    """,
)

try:
    scheduler = require("nonebot_plugin_apscheduler").scheduler
except Exception:
    os.system("pip install -i https://pypi.tuna.tsinghua.edu.cn/simple/ nonebot_plugin_apscheduler")
    logger.warning("请重启程序！")
    scheduler = require("nonebot_plugin_apscheduler").scheduler

logger.opt(colors=True).info(
    "已检测到软依赖<y>nonebot_plugin_apscheduler</y>, <g>开启定时任务功能</g>"
    if scheduler
    else "未检测到软依赖<y>nonebot_plugin_apscheduler</y>，<r>定时任务功能未启用</r>"
)

# -------------------------- 全局状态管理（关键） --------------------------
# 存储验证码监听状态：key=QQ号，value=asyncio.Event（用于唤醒阻塞）+ 验证码存储变量
verify_code_state: Dict[str, Dict] = {}
# 已登录的会话对象（全局复用，避免重复登录）
global_session: Optional[aiohttp.ClientSession] = None

# 目标QQ号（接收验证码的账号）
TARGET_QQ = "27****"  # 注意：OneBot事件中QQ号是字符串类型


# -------------------------- 1. 验证码监听逻辑（核心） --------------------------
def is_waiting_verify_code() -> Rule:
    """规则：是否处于“等待指定QQ输入验证码”状态"""
    async def _is_waiting(event: MessageEvent) -> bool:
        qq = str(event.user_id)
        # 仅当：1. 是目标QQ 2. 处于等待验证码状态 3. 是私聊（避免公屏消息干扰）
        return (
            qq == TARGET_QQ 
            and qq in verify_code_state 
            and isinstance(event, MessageEvent)
        )
    return Rule(_is_waiting)


# 注册验证码监听器：仅监听目标QQ的私聊消息，且处于等待状态时触发
verify_code_listener = on_message(rule=is_waiting_verify_code(), priority=1, block=True)

@verify_code_listener.handle()
async def handle_verify_code(event: MessageEvent):
    qq = str(event.user_id)
    # 获取用户发送的验证码（仅取纯文本，过滤表情/图片等）
    sms_code = event.get_plaintext().strip()
    
    if not sms_code.isdigit():  # 简单校验：验证码通常是数字
        await verify_code_listener.finish(f"请输入纯数字验证码，你输入的是：{sms_code}")
    
    # 存储验证码并唤醒阻塞的登录流程
    state = verify_code_state[qq]
    state["code"] = sms_code  # 保存验证码
    state["event"].set()       # 唤醒等待的协程
    
    await verify_code_listener.finish(f"验证码已接收：{sms_code}，正在继续登录...")


# -------------------------- 2. 网心云登录逻辑（适配NoneBot） --------------------------
async def wait_for_sms_code(qq: str) -> Optional[str]:
    """
    等待指定QQ输入验证码（阻塞协程，直到收到验证码或超时）
    :param qq: 目标QQ号
    :return: 验证码（超时返回None）
    """
    # 初始化状态：创建Event用于阻塞，存储验证码
    verify_code_state[qq] = {
        "event": asyncio.Event(),
        "code": None
    }
    
    # 发送提示消息给目标QQ（需要机器人能私聊目标QQ）
    bot = get_bot()  # 获取当前机器人实例
    await bot.send_private_msg(
        user_id=int(qq),
        message=f"请输入验证码（5分钟内有效）："
    )
    
    try:
        # 阻塞等待：最多等待5分钟（300秒），超时则返回None
        await asyncio.wait_for(verify_code_state[qq]["event"].wait(), timeout=300)
        return verify_code_state[qq]["code"]  # 返回收到的验证码
    except asyncio.TimeoutError:
        await bot.send_private_msg(
            user_id=int(qq),
            message="验证码输入超时（已超过5分钟），请重新触发登录"
        )
        return None
    finally:
        # 清理状态：无论成功/失败，都删除等待标记
        if qq in verify_code_state:
            del verify_code_state[qq]


async def onethingcloud_login() -> Optional[aiohttp.ClientSession]:
    """网心云登录（适配NoneBot，通过QQ获取验证码）"""
    global global_session
    # 若已有会话，先检查是否有效（简化逻辑：实际可加会话存活检测）
    if global_session and not global_session.closed:
        logger.info("已存在有效会话，无需重新登录")
        return global_session
    
    # 创建新会话
    session = aiohttp.ClientSession()
    try:
        # -------------------------- 步骤1：发送验证码请求 --------------------------
        logger.info("正在请求网心云发送验证码...")
        sms_url = "https://account.onethingcloud.com/v5/sms/send"
        timestamp = int(time.time() * 1000)
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
            "phone": "1****",  # 你的网心云绑定手机号
            "type": 30
        }
        sms_headers = {
            "Host": "account.onethingcloud.com",
            "Connection": "keep-alive",
            "sec-ch-ua-platform": "Windows",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0",
            "Accept": "application/json",
            "sec-ch-ua": "\"Not;A=Brand\";v=\"99\", \"Microsoft Edge\";v=\"139\", \"Chromium\";v=\"139\"",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "https://www.onethingcloud.com",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://www.onethingcloud.com/",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }

        async with session.post(
            sms_url, headers=sms_headers, json=sms_payload
        ) as resp:
            resp.raise_for_status()
            sms_result = await resp.json()
        
        if sms_result.get("errCode") != 0:
            err_msg = sms_result.get("msg", "未知错误")
            logger.error(f"发送验证码请求失败：{err_msg}")
            # 通知目标QQ失败原因
            bot = get_bot()
            await bot.send_private_msg(
                user_id=int(TARGET_QQ),
                message=f"网心云发送验证码请求失败：{err_msg}"
            )
            return None
        logger.info("验证码已发送到绑定手机，等待QQ输入...")


        # -------------------------- 步骤2：等待QQ输入验证码（核心适配） --------------------------
        sms_code = await wait_for_sms_code(TARGET_QQ)
        if not sms_code:
            logger.error("未获取到有效验证码，登录终止")
            return None


        # -------------------------- 步骤3：使用验证码完成登录 --------------------------
        logger.info(f"使用验证码 {sms_code} 登录...")
        login_url = "https://account.onethingcloud.com/v5/user/smslogin"
        login_timestamp = int(time.time() * 1000)
        tk = sms_result["data"].get("tk", "")
        if not tk:
            logger.error("无法获取登录所需的tk参数")
            return None
        
        login_payload = {
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
            "timestamp": login_timestamp,
            "tk": tk,
            "phone": "1****",
            "smsCode": sms_code,
            "extra": "{\"inviteCode\":\"2d9e1766\",\"activityId\":30006}",
            "type": 1
        }
        login_headers = sms_headers.copy()  # 复用大部分header，可根据实际调整
        login_headers["DNT"] = "1"  # 登录请求额外参数

        async with session.post(
            login_url, headers=login_headers, json=login_payload
        ) as resp:
            resp.raise_for_status()
            login_result = await resp.json()
        
        if login_result.get("errCode") != 0:
            err_msg = login_result.get("msg", "未知错误")
            logger.error(f"登录失败：{err_msg}")
            bot = get_bot()
            await bot.send_private_msg(
                user_id=int(TARGET_QQ),
                message=f"网心云登录失败：{err_msg}"
            )
            return None
        
        # 登录成功：更新全局会话
        global_session = session
        logger.success("网心云登录成功")
        # 通知目标QQ登录结果
        bot = get_bot()
        await bot.send_private_msg(
            user_id=int(TARGET_QQ),
            message="网心云登录成功，将执行定时请求"
        )
        return session

    except aiohttp.ClientError as e:
        logger.error(f"登录网络错误：{str(e)}")
        await session.close()
        return None
    except Exception as e:
        logger.error(f"登录未知错误：{str(e)}")
        await session.close()
        return None


# -------------------------- 3. 最终请求逻辑（自动处理登录状态） --------------------------
async def send_final_request(sn: str = "XRVDEDE7FCCCA04A") -> Optional[Dict]:
    """发送网心云最终请求，自动检测登录状态"""
    global global_session
    # 1. 确保会话有效（无会话则登录）
    session = global_session
    if not session or session.closed:
        session = await onethingcloud_login()
        if not session:
            logger.error("无法获取有效会话，请求终止")
            return None
    
    try:
        logger.debug(f"正在发送最终请求（设备SN：{sn}）...")
        final_url = "https://api-consolepro.onethingcloud.com/v1/device/generate_url"
        final_payload = {"sn": sn}
        final_headers = {
            "Host": "api-consolepro.onethingcloud.com",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
            "sec-ch-ua-platform": "Windows",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0",
            "Accept": "application/json, text/plain, */*",
            "sec-ch-ua": "\"Not;A=Brand\";v=\"99\", \"Microsoft Edge\";v=\"139\", \"Chromium\";v=\"139\"",
            "Content-Type": "application/json",
            "Origin": "https://consolepro.onethingcloud.com",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://consolepro.onethingcloud.com/",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }

        async with session.post(
            final_url, headers=final_headers, json=final_payload
        ) as resp:
            # 先获取响应文本，判断是否未登录
            response_text = await resp.text()
            try:
                final_result = json.loads(response_text)
                try:
                    frp_url=final_result["data"]["url"]
                    # 提取问号之前的部分（即去除查询参数）
                    base_url = frp_url.split('?')[0]
                    # 提取域名（去除http://部分）
                    domain = base_url.split('http://')[1]
                    status_api_url = f"{base_url}/v1.0/devices/status"
                    frp_headers = {
                        "Host": domain,
                        "Connection": "keep-alive",
                        "Pragma": "no-cache",
                        "Cache-Control": "no-cache",
                        "sec-ch-ua-platform": "Windows",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0",
                        "Accept": "application/json, text/plain, */*",
                        "sec-ch-ua": "\"Not;A=Brand\";v=\"99\", \"Microsoft Edge\";v=\"139\", \"Chromium\";v=\"139\"",
                        "Content-Type": "application/json",
                        "Origin": "https://consolepro.onethingcloud.com",
                        "Sec-Fetch-Site": "same-site",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Dest": "empty",
                        "Referer": frp_url,
                        "Accept-Encoding": "gzip, deflate, br, zstd",
                        "Accept-Language": "zh-CN,zh;q=0.9"
                    }
                    async with session.get(
                            status_api_url, headers=frp_headers
                    ) as resp1:
                        # 先获取响应文本，判断是否未登录
                        response1_text = await resp1.text()
                        bot = get_bot()
                        frp_result = json.loads(response1_text)
                        await bot.send_group_msg(
                            group_id=1029628356,
                            message=f"状态请求成功！\n状态码：{resp.status}\n响应：{json.dumps(frp_result, ensure_ascii=False, indent=2)}"
                    )
                except json.JSONDecodeError:
                    logger.warning(f"{frp_url},{response1_text}")
            except json.JSONDecodeError:
                final_result = {"raw_text": response_text}
            
            # 检测未登录状态（根据实际API返回调整）
            is_unauthorized = (
                resp.status in [401, 403] 
                or (isinstance(final_result, dict) and final_result.get("errCode") in [1001, 1002])
                or "未登录" in response_text
            )
            
            if is_unauthorized:
                logger.info("请求检测到未登录，重新登录...")
                # 关闭旧会话，重新登录
                await global_session.close()
                global_session = None
                new_session = await onethingcloud_login()
                if not new_session:
                    return None
                # 重新发送请求
                return await send_final_request(sn)  # 递归重试一次
            
            # 正常响应：通知目标QQ结果
            bot = get_bot()
            await bot.send_private_msg(
                user_id=int(TARGET_QQ),
                message=f"最终请求成功！\n状态码：{resp.status}\n响应：{json.dumps(final_result, ensure_ascii=False, indent=2)}"
            )
            logger.success(f"最终请求成功，响应：{final_result}")
            return final_result

    except aiohttp.ClientError as e:
        err_msg = f"请求网络错误：{str(e)}"
        logger.error(err_msg)
        bot = get_bot()
        await bot.send_private_msg(user_id=int(TARGET_QQ), message=err_msg)
        return None
    except Exception as e:
        err_msg = f"请求未知错误：{str(e)}"
        logger.error(err_msg)
        bot = get_bot()
        await bot.send_private_msg(user_id=int(TARGET_QQ), message=err_msg)
        return None


# -------------------------- 4. 定时任务与手动触发命令 --------------------------
# 手动触发登录（仅超级用户可用，避免他人滥用）
manual_login = on_command("登录", permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER, priority=5, block=True)

@manual_login.handle()
async def handle_manual_login():
    session = await onethingcloud_login()
    if session:
        logger.success("登录已成功")
        # await manual_login.finish("登录已成功")
    else:
        await manual_login.finish("登录失败，请查看日志")


# 手动触发最终请求（仅超级用户可用）
manual_request = on_command("请求", permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER, priority=5, block=True)

@manual_request.handle()
async def handle_manual_request():
    await manual_request.send("正在触发最终请求...")
    result = await send_final_request()
    if not result:
        await manual_request.finish("最终请求失败")



@scheduler.scheduled_job("cron", hour="*/1", minute="*/30", second="0", id="每30分钟", misfire_grace_time=3600)
async def daily_onethingcloud_task():
    print("定时任务触发：执行网心云最终请求")
    # 先通知目标QQ任务开始
    bot = get_bot()
    await bot.send_group_msg(
        group_id=1029628356,
        message="定时任务已触发，正在执行请求..."
    )
    await send_final_request()
