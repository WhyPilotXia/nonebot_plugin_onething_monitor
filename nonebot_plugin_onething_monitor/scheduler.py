import asyncio

from nonebot import get_bot, require
from nonebot.adapters.onebot.v11 import Bot, MessageSegment
from nonebot.log import logger

from . import state
from .api import fetch_all_devices, send_network_request
from .config import plugin_config
from .render import merge_images_vertically
from .state import device_sn_map, fail_count, fail_messages, global_sessions

TARGET_QQ = plugin_config.onething_target_qq  # 管理员QQ，用于接收验证码请求
BOT_ID = plugin_config.onething_bot_id

try:
    scheduler = require("nonebot_plugin_apscheduler").scheduler
except Exception:
    scheduler = None


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
                group_id=state.TARGET_GROUP,
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
        fail_message = (
            fail_messages.get(max_fail_count, f"连续{max_fail_count}次了，鼠鼠快醒醒吧！")
            if max_fail_count > 0
            else ""
        )



        message = (MessageSegment.at(TARGET_QQ)+"重要设备：" if 20 > max_fail_count > 2 else "* ") + f"{str(failed_devices)}似乎掉线了。"
        if fail_message:
            message += f" {fail_message}"
        await bot.send_group_msg(
            group_id=state.TARGET_GROUP,
            message=message
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
                group_id=state.TARGET_GROUP,
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
        state.TARGET_GROUP = plugin_config.onething_target_group  # 消息通知群
        try:
            bot = get_bot(self_id=BOT_ID)
            await execute_batch_network_check(bot)
        except Exception as e:
            logger.error(f"定时任务异常: {e}")
