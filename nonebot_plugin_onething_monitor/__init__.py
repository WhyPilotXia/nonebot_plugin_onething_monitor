from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="网心云多账号版",
    description="自动登录网心云并执行定时请求，支持多账号(短信/Cookie)、设备列表聚合查询",
    usage="""
    1. 发送【登录】触发默认手机号登录
    2. 发送【登录 138xxxx】触发指定手机号登录
    3. 发送【登录 userid=xxx; sessionid=xxx】使用Cookie登录
    4. 发送【列表】查看所有账号设备并获取全局编号
    5. 发送【基础】触发请求，支持【基础 1】
    6. 发送【网络】查看多拨状态，支持【网络 1】或指定SN
    相比v4多一个多账号管理功能
    相比v5升级成正式nonebot2插件，配置外置
    """,
    type="application",
    homepage="https://github.com/WhyPilotXia/nonebot_plugin_onething_monitor",
    supported_adapters={"~onebot.v11"},
    config=Config,
)

from . import commands as commands
from . import scheduler as scheduler
from . import session as session
