import base64
import os
import random
import time
from datetime import datetime

from nonebot.log import logger
from PIL import Image

from .config import DATA_DIR


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
