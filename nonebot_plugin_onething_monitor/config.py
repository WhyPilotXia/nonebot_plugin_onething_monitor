import os
from pathlib import Path

from nonebot import get_plugin_config
from pydantic import BaseModel, field_validator


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

DATA_DIR = Path(os.getcwd()) / "onething"
DATA_DIR.mkdir(parents=True, exist_ok=True)
