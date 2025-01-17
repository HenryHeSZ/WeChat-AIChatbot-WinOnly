# encoding:utf-8

import json
import logging
import os
import random
import sqlite3
import string
from typing import Tuple

import plugins
from bridge.bridge import Bridge
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common import const
from common.log import logger
from config import conf, load_config, global_config
from plugins import *

# 定义指令集
COMMANDS = {
    "help": {
        "alias": ["help", "帮助"],
        "desc": "回复此帮助",
    },
    "helpp": {
        "alias": ["help", "帮助"],  # 与help指令共用别名，根据参数数量区分
        "args": ["插件名"],
        "desc": "回复指定插件的详细帮助",
    },
    "auth": {
        "alias": ["auth", "认证"],
        "args": ["口令"],
        "desc": "管理员认证",
    },
    "set_openai_api_key": {
        "alias": ["set_openai_api_key"],
        "args": ["api_key"],
        "desc": "设置你的OpenAI私有api_key",
    },
    "reset_openai_api_key": {
        "alias": ["reset_openai_api_key"],
        "desc": "重置为默认的api_key",
    },
    "set_gpt_model": {
        "alias": ["set_gpt_model"],
        "desc": "设置你的私有模型",
    },
    "reset_gpt_model": {
        "alias": ["reset_gpt_model"],
        "desc": "重置你的私有模型",
    },
    "gpt_model": {
        "alias": ["gpt_model"],
        "desc": "查询你使用的模型",
    },
    "id": {
        "alias": ["id", "用户"],
        "desc": "获取用户id",  # wechaty和wechatmp的用户id不会变化，可用于绑定管理员
    },
    "reset": {
        "alias": ["reset", "重置会话"],
        "desc": "重置会话",
    },
}

ADMIN_COMMANDS = {
    "resume": {
        "alias": ["resume", "恢复服务"],
        "desc": "恢复服务",
    },
    "stop": {
        "alias": ["stop", "暂停服务"],
        "desc": "暂停服务",
    },
    "reconf": {
        "alias": ["reconf", "重载配置"],
        "desc": "重载配置(不包含插件配置)",
    },
    "resetall": {
        "alias": ["resetall", "重置所有会话"],
        "desc": "重置所有会话",
    },
    "scanp": {
        "alias": ["scanp", "扫描插件"],
        "desc": "扫描插件目录是否有新插件",
    },
    "plist": {
        "alias": ["plist", "插件"],
        "desc": "打印当前插件列表",
    },
    "setpri": {
        "alias": ["setpri", "设置插件优先级"],
        "args": ["插件名", "优先级"],
        "desc": "设置指定插件的优先级，越大越优先",
    },
    "reloadp": {
        "alias": ["reloadp", "重载插件"],
        "args": ["插件名"],
        "desc": "重载指定插件配置",
    },
    "enablep": {
        "alias": ["enablep", "启用插件"],
        "args": ["插件名"],
        "desc": "启用指定插件",
    },
    "disablep": {
        "alias": ["disablep", "禁用插件"],
        "args": ["插件名"],
        "desc": "禁用指定插件",
    },
    "installp": {
        "alias": ["installp", "安装插件"],
        "args": ["仓库地址或插件名"],
        "desc": "安装指定插件",
    },
    "uninstallp": {
        "alias": ["uninstallp", "卸载插件"],
        "args": ["插件名"],
        "desc": "卸载指定插件",
    },
    "updatep": {
        "alias": ["updatep", "更新插件"],
        "args": ["插件名"],
        "desc": "更新指定插件",
    },
    "debug": {
        "alias": ["debug", "调试模式", "DEBUG"],
        "desc": "开启机器调试日志",
    }
}


@plugins.register(
    name="Godcmd",
    desire_priority=999,
    hidden=True,
    desc="为你的机器人添加指令集，有用户和管理员两种角色，加载顺序请放在首位，初次运行后插件目录会生成配置文件, 填充管理员密码后即可认证",
    version="1.0",
    author="lanvent",
)
class Godcmd(Plugin):
    def __init__(self):
        super().__init__()

        curdir = os.path.dirname(__file__)
        config_path = os.path.join(curdir, "config.json")
        rootdir = os.path.dirname(os.path.dirname(curdir))
        dbdir = os.path.join(rootdir, "db")
        if not os.path.exists(dbdir):
            os.mkdir(dbdir)
        gconf = None
        if not os.path.exists(config_path):
            gconf = {"password": "", "admin_users": []}
            with open(config_path, "w") as f:
                json.dump(gconf, f, indent=4)
        else:
            with open(config_path, "r") as f:
                gconf = json.load(f)
        if gconf["password"] == "":
            self.temp_password = "".join(random.sample(string.digits, 4))
            logger.info("[Godcmd] 因未设置口令，本次的临时口令为%s。" % self.temp_password)
        else:
            self.temp_password = None
        custom_commands = conf().get("clear_memory_commands", [])
        for custom_command in custom_commands:
            if custom_command and custom_command.startswith("#"):
                custom_command = custom_command[1:]
                if custom_command and custom_command not in COMMANDS["reset"]["alias"]:
                    COMMANDS["reset"]["alias"].append(custom_command)
        user_db = os.path.join(dbdir, "user.db")
        self.user_db = sqlite3.connect(user_db, check_same_thread=False)
        self.password = gconf["password"]
        self.admin_users = gconf["admin_users"]  # 预存的管理员账号，这些账号不需要认证。itchat的用户名每次都会变，不可用
        self.isrunning = True  # 机器人是否运行中

        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        logger.info("[Godcmd] inited")

    def on_handle_context(self, e_context: EventContext):
        context_type = e_context["context"].type
        if context_type != ContextType.TEXT:
            if not self.isrunning:
                e_context.action = EventAction.BREAK_PASS
            return

        content = e_context["context"].content
        logger.debug("[Godcmd] on_handle_context. content: %s" % content)
        if content.startswith("#"):
            if len(content) == 1:
                reply = Reply()
                reply.type = ReplyType.ERROR
                reply.content = f"空指令，输入#help查看指令列表\n"
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            # msg = e_context['context']['msg']
            channel = e_context["channel"]
            user = e_context["context"]["receiver"]
            session_id = e_context["context"]["session_id"]
            isgroup = e_context["context"].get("isgroup", False)
            bottype = Bridge().get_bot_type("chat")
            bot = Bridge().get_bot("chat")
            # 将命令和参数分割
            command_parts = content[1:].strip().split()
            cmd = command_parts[0]
            args = command_parts[1:]
            isadmin = False
            if user in self.admin_users:
                isadmin = True
            ok = False
            result = "string"
            if any(cmd in info["alias"] for info in COMMANDS.values()):
                cmd = next(c for c, info in COMMANDS.items() if cmd in info["alias"])
                if cmd == "auth":
                    ok, result = self.authenticate(user, args, isadmin, isgroup)
                elif cmd == "id":
                    ok, result = True, user
                elif cmd == "help" or cmd == "helpp":
                    if len(args) == 0:
                        ok, result = True, self.get_help_text(isadmin, isgroup)
                    else:
                        e_context.action = EventAction.BREAK_PASS
                        return
            elif any(cmd in info["alias"] for info in ADMIN_COMMANDS.values()):
                if isadmin:
                    if isgroup:
                        ok, result = False, "群聊不可执行管理员指令"
                    else:
                        cmd = next(c for c, info in ADMIN_COMMANDS.items() if cmd in info["alias"])
                        if cmd == "stop":
                            self.isrunning = False
                            ok, result = True, "服务已暂停"
                        elif cmd == "resume":
                            self.isrunning = True
                            ok, result = True, "服务已恢复"
                        elif cmd == "reconf":
                            load_config()
                            ok, result = True, "配置已重载"
                        elif cmd == "resetall":
                            if bottype in [const.OPEN_AI, const.CHATGPT, const.CHATGPTONAZURE, const.LINKAI]:
                                channel.cancel_all_session()
                                bot.sessions.clear_all_session()
                                ok, result = True, "重置所有会话成功"
                            else:
                                ok, result = False, "当前对话机器人不支持重置会话"
                        elif cmd == "debug":
                            if logger.getEffectiveLevel() == logging.DEBUG:  # 判断当前日志模式是否DEBUG
                                logger.setLevel(logging.INFO)
                                ok, result = True, "DEBUG模式已关闭"
                            else:
                                logger.setLevel(logging.DEBUG)
                                ok, result = True, "DEBUG模式已开启"
                        elif cmd == "plist":
                            plugins = PluginManager().list_plugins()
                            ok = True
                            result = "插件列表：\n"
                            for name, plugincls in plugins.items():
                                result += f"{plugincls.name}_v{plugincls.version} {plugincls.priority} - "
                                if plugincls.enabled:
                                    result += "已启用\n"
                                else:
                                    result += "未启用\n"
                        elif cmd == "scanp":
                            new_plugins = PluginManager().scan_plugins()
                            ok, result = True, "插件扫描完成"
                            PluginManager().activate_plugins()
                            if len(new_plugins) > 0:
                                result += "\n发现新插件：\n"
                                result += "\n".join([f"{p.name}_v{p.version}" for p in new_plugins])
                            else:
                                result += ", 未发现新插件"
                        elif cmd == "setpri":
                            if len(args) != 2:
                                ok, result = False, "请提供插件名和优先级"
                            else:
                                ok = PluginManager().set_plugin_priority(args[0], int(args[1]))
                                if ok:
                                    result = "插件" + args[0] + "优先级已设置为" + args[1]
                                else:
                                    result = "插件不存在"
                        elif cmd == "reloadp":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名"
                            else:
                                ok = PluginManager().reload_plugin(args[0])
                                if ok:
                                    result = "插件配置已重载"
                                else:
                                    result = "插件不存在"
                        elif cmd == "enablep":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名"
                            else:
                                ok, result = PluginManager().enable_plugin(args[0])
                        elif cmd == "disablep":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名"
                            else:
                                ok = PluginManager().disable_plugin(args[0])
                                if ok:
                                    result = "插件已禁用"
                                else:
                                    result = "插件不存在"
                        elif cmd == "installp":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名或.git结尾的仓库地址"
                            else:
                                ok, result = PluginManager().install_plugin(args[0])
                        elif cmd == "uninstallp":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名"
                            else:
                                ok, result = PluginManager().uninstall_plugin(args[0])
                        elif cmd == "updatep":
                            if len(args) != 1:
                                ok, result = False, "请提供插件名"
                            else:
                                ok, result = PluginManager().update_plugin(args[0])
                        elif cmd == "verify":
                            ok, result = self.generate_activation_code(args)
                        elif cmd == "delete":
                            ok, result = self.delete_activation_code(args)
                        logger.debug("[Godcmd] admin command: %s by %s" % (cmd, user))

                else:
                    ok, result = False, None
            else:
                trigger_prefix = conf().get("plugin_trigger_prefix", "$")
                if trigger_prefix == "#":  # 跟插件聊天指令前缀相同，继续递交
                    return
                ok, result = False, "string"

            reply = Reply()
            if ok and result:
                reply.type = ReplyType.INFO
            elif not result or result == "string":
                e_context.action = EventAction.BREAK_PASS
                return
            else:
                reply.type = ReplyType.ERROR

            reply.content = result
            e_context["reply"] = reply

            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑
        elif not self.isrunning:
            e_context.action = EventAction.BREAK_PASS

    def authenticate(self, userid, args, isadmin, isgroup) -> Tuple[bool, str]:
        if isgroup:
            return False, "请勿在群聊中认证"

        if isadmin:
            return False, "管理员账号无需认证"

        if len(args) != 1:
            return False, "请提供口令"

        password = args[0]
        if password == self.password:
            self.admin_users.append(userid)
            global_config["admin_users"].append(userid)
            return True, "认证成功"
        elif password == self.temp_password:
            self.admin_users.append(userid)
            global_config["admin_users"].append(userid)
            return True, "认证成功，请尽快设置口令"
        else:
            return False, "认证失败"

    def get_help_text(self, isadmin=False, isgroup=False, **kwargs):
        if isgroup:
            return
        if ADMIN_COMMANDS and isadmin:
            help_text = "管理员指令：\n"
            for cmd, info in ADMIN_COMMANDS.items():
                alias = ["#" + a for a in info["alias"][:1]]
                help_text += f"{','.join(alias)} "
                if "args" in info:
                    args = [a for a in info["args"]]
                    help_text += f"{' '.join(args)}"
                help_text += f": {info['desc']}\n"
            return help_text
        else:
            return

    def generate_activation_code(self, args):
        # 检查 args 的长度
        if len(args) < 1 > 2:
            return False, "命令有误，请输入有效天数和需要的数量，如 30 10！"

        try:
            # 获取并检查有效天数
            valid_days = int(args[0])
            if valid_days <= 0:
                raise ValueError

            # 获取并检查生成数量
            count = int(args[1]) if len(args) > 1 else 1
            if count <= 0:
                raise ValueError

        except ValueError:
            return False, "命令有误，请输入有效天数和需要的数量，如 30 10！"

        # 确保表存在
        cur = self.user_db.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS wechat_users (
                UserID TEXT,
                ActivationCode TEXT UNIQUE,
                Invitation code TEXT,
                ValidDays INTEGER,
                ExpiryDate DATETIME,
                LastUsedTime DATETIME
            )
        """)
        self.user_db.commit()

        # 生成激活码
        activation_codes = []
        for _ in range(count):
            # 生成一个随机的、包含大小写字母和数字的 16 位字符串作为激活码
            activation_code = ''.join(random.choices(string.ascii_letters + string.digits, k=16))

            # 将激活码和有效天数添加到数据库
            cur.execute("""
                INSERT INTO wechat_users (ActivationCode, ValidDays)
                VALUES (?, ?)
            """, (activation_code, valid_days))
            self.user_db.commit()

            # 将生成的激活码添加到列表中
            activation_codes.append(activation_code)

        # 将每个激活码添加 "激活码：" 前缀，并将所有激活码连接成一个字符串
        activation_codes_str = '\n'.join(["激活码：" + code for code in activation_codes])

        # 返回生成的激活码
        return True, activation_codes_str

    def delete_activation_code(self, args):
        # 检查 args 的长度
        if len(args) == 0:
            return False, "命令有误，请输入要删除的授权码！"

        # 获取要删除的激活码
        activation_code = args[0]

        # 在数据库中查找该激活码
        cur = self.user_db.cursor()
        cur.execute("""
            SELECT * FROM wechat_users
            WHERE ActivationCode = ?
        """, (activation_code,))

        data = cur.fetchone()

        # 如果未找到激活码，返回"授权码不存在或输入有误！"
        if data is None:
            return False, "授权码不存在或输入有误！"

        # 如果找到激活码，从数据库中删除
        cur.execute("""
            DELETE FROM wechat_users
            WHERE ActivationCode = ?
        """, (activation_code,))

        self.user_db.commit()

        return True, "授权码删除成功！"
