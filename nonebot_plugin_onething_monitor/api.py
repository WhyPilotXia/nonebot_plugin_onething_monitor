import asyncio
import json
import os
import re
import time
import traceback
from typing import Dict, Optional, Tuple

import aiohttp
from nonebot import get_bot
from nonebot.adapters.onebot.v11 import Bot, MessageSegment
from nonebot.exception import ApiNotAvailable
from nonebot.log import logger

from . import state
from .config import plugin_config
from .render import image_segment_from_path, save_info_to_local, save_network_table_to_local
from .session import clear_session, parse_cookie_str, save_session_to_file
from .state import device_owner_map, device_sn_map, global_sessions, verify_code_state

TARGET_QQ = plugin_config.onething_target_qq  # 管理员QQ，用于接收验证码请求
BOT_ID = plugin_config.onething_bot_id


async def wait_for_sms_code(phone: str) -> Optional[str]:
    """等待指定手机号的验证码"""
    verify_code_state[phone] = {
        "event": asyncio.Event(),
        "code": None
    }

    try:
        bot = get_bot(self_id=BOT_ID)
        await bot.send_group_msg(
            group_id=state.TARGET_GROUP,
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
            await bot.send_group_msg(group_id=state.TARGET_GROUP, message=f"无法找到设备 {sn} 的有效会话，请先 /列表")
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
                    await bot.send_group_msg(group_id=state.TARGET_GROUP, message=f"账号 {owner_id} 登录已失效")
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
                            await bot.send_group_msg(
                                group_id=state.TARGET_GROUP,
                                message=image_segment_from_path(img_path),
                            )
                            return img_path
                else:
                    if not only_return_path:
                        await bot.send_group_msg(group_id=state.TARGET_GROUP,
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
        await bot.send_group_msg(group_id=state.TARGET_GROUP, message=f"无法找到设备 {sn} 的有效会话，请先 /列表")
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
                await bot.send_group_msg(group_id=state.TARGET_GROUP, message=f"账号 {owner_id} 登录失效")
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
                        await bot.send_group_msg(
                            group_id=state.TARGET_GROUP,
                            message=image_segment_from_path(img_path),
                        )
                        return frp_result
                else:
                    msg = f"未获取到FRP URL: {final_result.get('sMsg', '未知错误')}"
                    await bot.send_group_msg(group_id=state.TARGET_GROUP, message=msg)


            except Exception as e:
                logger.error(f"解析基础信息失败: {e}")
                logger.error(f"原始返回内容: {frp_text[:1000]}")  # 防止太长
                logger.error(f"状态码: {resp1.status}")
                logger.error(traceback.format_exc())
                return None

    except Exception as e:
        logger.error(f"基础请求异常: {e}")
        return None
