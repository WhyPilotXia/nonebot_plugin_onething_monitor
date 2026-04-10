import aiohttp
import json
import time
import asyncio


async def login(session: aiohttp.ClientSession = None) -> aiohttp.ClientSession:
    """
    登录函数，返回已登录的会话对象
    :param session: 可选的现有会话对象，如果为None则创建新会话
    :return: 已登录的会话对象，如果登录失败则返回None
    """
    # 如果没有提供会话，则创建新会话
    session_provided = session is not None
    if not session:
        session = aiohttp.ClientSession()

    try:
        # -------------------------- 1. 发送验证码 --------------------------
        print("正在发送验证码...")
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
            "phone": "1******",
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
            "sec-ch-ua-mobile": "?0",
            "Origin": "https://www.onethingcloud.com",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://www.onethingcloud.com/",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }

        async with session.post(
                sms_url,
                headers=sms_headers,
                json=sms_payload
        ) as sms_response:
            sms_response.raise_for_status()
            sms_result = await sms_response.json()

        print(f"发送验证码响应: {json.dumps(sms_result, ensure_ascii=False, indent=2)}")

        if sms_result.get("errCode") != 0:
            print(f"发送验证码失败: {sms_result.get('msg', '未知错误')}")
            if not session_provided:
                await session.close()
            return None

        # -------------------------- 2. 获取用户输入验证码 --------------------------
        sms_code = input("请输入收到的验证码: ").strip()
        if not sms_code:
            print("验证码不能为空")
            if not session_provided:
                await session.close()
            return None

        # -------------------------- 3. 使用验证码登录 --------------------------
        print("正在使用验证码登录...")
        login_url = "https://account.onethingcloud.com/v5/user/smslogin"
        login_timestamp = int(time.time() * 1000)

        tk = sms_result['data'].get("tk", "")
        if not tk:
            print("无法获取tk，登录失败")
            if not session_provided:
                await session.close()
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
            "phone": "1******",
            "smsCode": sms_code,
            "extra": "{\"inviteCode\":\"2d9e1766\",\"activityId\":30006}",
            "type": 1
        }

        login_headers = {
            "Host": "account.onethingcloud.com",
            "Connection": "keep-alive",
            "sec-ch-ua-platform": "Windows",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0",
            "Accept": "application/json",
            "sec-ch-ua": "\"Not;A=Brand\";v=\"99\", \"Microsoft Edge\";v=\"139\", \"Chromium\";v=\"139\"",
            "Content-Type": "application/json; charset=utf-8",
            "DNT": "1",
            "sec-ch-ua-mobile": "?0",
            "Origin": "https://www.onethingcloud.com",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://www.onethingcloud.com/",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }

        async with session.post(
                login_url,
                headers=login_headers,
                json=login_payload
        ) as login_response:
            login_response.raise_for_status()
            login_result = await login_response.json()

        print(f"登录响应: {json.dumps(login_result, ensure_ascii=False, indent=2)}")

        if login_result.get("errCode") != 0:
            print(f"登录失败: {login_result.get('msg', '未知错误')}")
            if not session_provided:
                await session.close()
            return None

        print("登录成功")
        return session  # 返回已登录的会话对象

    except aiohttp.ClientError as e:
        print(f"登录过程中发生错误: {e}")
        if not session_provided:
            await session.close()
        return None
    except json.JSONDecodeError:
        print("登录过程中收到无效的JSON响应")
        if not session_provided:
            await session.close()
        return None


async def send_final_request(session: aiohttp.ClientSession, sn: str = "XRVDEDE7********") -> dict:
    """
    发送最终请求的函数
    :param session: 已登录的会话对象
    :param sn: 设备序列号
    :return: 响应结果字典，如果失败则返回None
    """
    if not session:
        print("会话对象不能为空，请先登录")
        return None

    try:
        print("正在发送最终请求...")
        final_url = "https://api-consolepro.onethingcloud.com/v1/device/generate_url"

        final_payload = {
            "sn": sn
        }

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
            "sec-ch-ua-mobile": "?0",
            "Origin": "https://consolepro.onethingcloud.com",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://consolepro.onethingcloud.com/",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }

        async with session.post(
                final_url,
                headers=final_headers,
                json=final_payload
        ) as final_response:
            final_response.raise_for_status()
            final_result = await final_response.json()

        print(f"\n最终请求状态码: {final_response.status}")
        print("最终响应内容:")
        print(json.dumps(final_result, ensure_ascii=False, indent=2))
        return final_result

    except aiohttp.ClientError as e:
        print(f"最终请求发生错误: {e}")
        return None
    except json.JSONDecodeError:
        print("最终响应内容不是有效的JSON")
        return None


# 示例调用
async def example_usage():
    # 1. 登录获取会话
    session = await login()
    if not session:
        print("登录失败，无法继续")
        return

    try:
        # 2. 可以在这里添加延迟或其他操作
        print("等待一段时间后发送最终请求...")
        await asyncio.sleep(5)  # 模拟等待一段时间

        # 3. 发送最终请求
        result = await send_final_request(session)

        # 4. 如果需要，可以多次发送最终请求
        # await asyncio.sleep(10)
        # result2 = await send_final_request(session, "另一个设备序列号")

    finally:
        # 5. 完成后关闭会话
        await session.close()


if __name__ == "__main__":
    asyncio.run(example_usage())
