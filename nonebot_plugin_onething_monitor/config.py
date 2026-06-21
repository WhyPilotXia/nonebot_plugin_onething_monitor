from pathlib import Path

from nonebot import get_plugin_config, require
from nonebot.compat import field_validator
from pydantic import BaseModel

require("nonebot_plugin_localstore")

import nonebot_plugin_localstore as localstore  # noqa: E402


class Config(BaseModel):
    onething_target_qq: str
    onething_target_group: int
    onething_bot_id: str
    onething_default_phone: str

    @field_validator("onething_target_qq", "onething_bot_id", "onething_default_phone", mode="before")
    @classmethod
    def stringify_numeric_config(cls, value):
        return str(value)


plugin_config = get_plugin_config(Config)

DATA_DIR: Path = localstore.get_plugin_data_dir()
