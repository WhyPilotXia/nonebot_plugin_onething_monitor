import aiohttp
import json
import time
import asyncio


async def main():
    # 创建异步会话（自动管理Cookie，替代requests.Session）
    async with aiohttp.ClientSession() as session:

        # -------------------------- 1. 异步发送验证码 --------------------------
        print("正在发送验证码...")
        sms_url = "https://account.onethingcloud.com/v5/sms/send"
        # 生成当前时间戳（毫秒级，与原逻辑一致）
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
            "phone": "1****",
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

        try:
            # 异步POST请求（aiohttp.post需用await修饰）
            async with session.post(
                sms_url,
                headers=sms_headers,
                json=sms_payload  # aiohttp支持直接传json参数，无需手动dumps
            ) as sms_response:
                # 校验HTTP状态码（4xx/5xx会抛出异常）
                sms_response.raise_for_status()
                # 异步解析JSON响应
                sms_result = await sms_response.json()

            print(f"发送验证码响应: {json.dumps(sms_result, ensure_ascii=False, indent=2)}")

            # 按原逻辑校验错误码（errCode=0为成功）
            if sms_result.get("errCode") != 0:
                print(f"发送验证码失败: {sms_result.get('msg', '未知错误')}")
                return

        except aiohttp.ClientError as e:  # 捕获aiohttp的网络异常（替代requests.RequestException）
            print(f"发送验证码请求失败: {e}")
            return
        except json.JSONDecodeError:
            print("发送验证码响应不是有效的JSON")
            # 若解析失败，异步获取原始文本
            async with session.post(sms_url, headers=sms_headers, json=sms_payload) as resp:
                raw_text = await resp.text()
            print("原始响应内容:", raw_text)
            return


        # -------------------------- 2. 获取用户输入验证码 --------------------------
        sms_code = input("请输入收到的验证码: ").strip()
        if not sms_code:
            print("验证码不能为空")
            return


        # -------------------------- 3. 异步使用验证码登录 --------------------------
        print("正在使用验证码登录...")
        login_url = "https://account.onethingcloud.com/v5/user/smslogin"
        # 生成新的登录时间戳
        login_timestamp = int(time.time() * 1000)

        # 从验证码响应中获取tk（与原逻辑一致，非Cookie获取）
        tk = sms_result['data'].get("tk", "")
        if not tk:
            print("无法获取tk，登录失败")
            return

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

        try:
            # 异步登录请求
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
                return

        except aiohttp.ClientError as e:
            print(f"登录请求失败: {e}")
            return
        except json.JSONDecodeError:
            print("登录响应不是有效的JSON")
            async with session.post(login_url, headers=login_headers, json=login_payload) as resp:
                raw_text = await resp.text()
            print("原始响应内容:", raw_text)
            return


        # -------------------------- 4. 异步发送最终请求 --------------------------
        print("正在发送最终请求...")
        final_url = "https://api-consolepro.onethingcloud.com/v1/device/generate_url"

        final_payload = {
            "sn": "XRVDEDE7F*******"
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

        try:
            # 异步发送最终请求（会话自动携带登录后的Cookie）
            async with session.post(
                final_url,
                headers=final_headers,
                json=final_payload
            ) as final_response:
                final_response.raise_for_status()
                final_result = await final_response.json()

            print(f"\n最终请求状态码: {final_response.status}")  # aiohttp用status属性获取状态码
            print("最终响应内容:")
            print(json.dumps(final_result, ensure_ascii=False, indent=2))

        except aiohttp.ClientError as e:
            print(f"最终请求发生错误: {e}")
        except json.JSONDecodeError:
            print("最终响应内容不是有效的JSON")
            async with session.post(final_url, headers=final_headers, json=final_payload) as resp:
                raw_text = await resp.text()
            print("原始响应内容:", raw_text)


# -------------------------- 启动异步事件循环 --------------------------
if __name__ == "__main__":
    # 兼容Python 3.7+的异步启动方式（替代旧版asyncio.get_event_loop()）
    asyncio.run(main())