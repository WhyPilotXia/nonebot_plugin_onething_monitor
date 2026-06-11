from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import GROUP_ADMIN, GROUP_OWNER
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule

from . import state
from .api import fetch_all_devices, login_by_cookie, login_by_sms, send_final_request, send_network_request
from .config import plugin_config
from .render import save_info_to_local
from .scheduler import execute_batch_network_check
from .state import device_sn_map, global_sessions, reset_device_cache, verify_code_state

TARGET_QQ = plugin_config.onething_target_qq  # 管理员QQ，用于接收验证码请求
DEFAULT_PHONE = plugin_config.onething_default_phone  # 默认手机号


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
    if isinstance(event,GroupMessageEvent):
        state.TARGET_GROUP = event.group_id  # 临时切换至当前群。定时任务执行时会切换会默认
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
