import os
from pathlib import Path
from typing import Optional, Union

from nonebot import get_plugin_config
from pydantic import BaseModel


class Config(BaseModel):
    onething_target_qq: Optional[Union[str, int]] = None
    onething_target_group: Optional[Union[str, int]] = None
    onething_bot_id: Optional[Union[str, int]] = None
    onething_default_phone: Optional[Union[str, int]] = None


def _as_str(value: Optional[Union[str, int]]) -> str:
    return "" if value in (None, "") else str(value)


def _as_int(value: Optional[Union[str, int]]) -> int:
    return 0 if value in (None, "") else int(value)


plugin_config = get_plugin_config(Config)
plugin_config.onething_target_qq = _as_str(plugin_config.onething_target_qq)
plugin_config.onething_target_group = _as_int(plugin_config.onething_target_group)
plugin_config.onething_bot_id = _as_str(plugin_config.onething_bot_id)
plugin_config.onething_default_phone = _as_str(plugin_config.onething_default_phone)

DATA_DIR = Path(os.getcwd()) / "onething"
DATA_DIR.mkdir(parents=True, exist_ok=True)
