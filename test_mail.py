#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQ 邮箱发信自测工具（可选，但强烈建议先跑一次）

作用：在推到 GitHub 之前，先在本地确认「授权码 + SMTP 配置」是对的。
      免得在 Actions 里反复试错，看日志才知道失败原因。

用法（在本文件所在目录打开终端）：
    python test_mail.py

也可以直接用环境变量传参（Git Bash / macOS / Linux）：
    SMTP_USER=你的QQ邮箱 SMTP_PASS=16位授权码 python test_mail.py
"""

import getpass
import os
import smtplib
import socket
import ssl
import sys
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr


def ask(prompt, default=None, secret=False):
    # 非交互环境（IDE 内置终端、被其他程序调用等）读不到键盘输入，直接给出明确提示
    if not sys.stdin.isatty():
        if default:
            return default
        print(f"！当前环境无法键盘输入，请改用环境变量传参。缺失项：{prompt}")
        return None
    if default:
        prompt = f"{prompt}（回车使用 {default}）："
    else:
        prompt = f"{prompt}："
    if secret:
        val = getpass.getpass(prompt).strip()
    else:
        val = input(prompt).strip()
    return val or default


def main():
    print("=" * 52)
    print(" QQ 邮箱发信自测")
    print("=" * 52)

    host = os.getenv("SMTP_HOST", "").strip() or "smtp.qq.com"
    try:
        port = int(os.getenv("SMTP_PORT", "").strip() or 465)
    except ValueError:
        port = 465

    user = os.getenv("SMTP_USER", "").strip()
    if not user:
        user = ask("你的 QQ 邮箱地址（如 123456@qq.com）")
    if not user:
        print("！邮箱地址不能为空")
        return 1

    password = os.getenv("SMTP_PASS", "").strip()
    if not password:
        password = ask("16 位授权码（不是 QQ 密码，输入时不显示）", secret=True)
    if not password:
        print("！授权码不能为空")
        return 1

    # 授权码里偶尔会带空格，直接复制粘贴时容易混进去
    password = password.replace(" ", "")

    to_addr = os.getenv("MAIL_TO", "").strip() or ask("收件邮箱", default=user)

    print()
    print(f"  发信服务器 : {host}:{port}")
    print(f"  登录账号   : {user}")
    print(f"  授权码     : {password[:4]}{'*' * (len(password) - 4)}（共 {len(password)} 位）")
    print(f"  收件人     : {to_addr}")
    print()
    if len(password) != 16:
        print(f"！注意：授权码应该是 16 位，你输入的是 {len(password)} 位，先继续尝试，失败多半是这个原因。")
        print()

    body = (
        "这封邮件来自本地自测脚本，说明你的 QQ 邮箱 SMTP 配置是正确的。\n\n"
        "接下来把同样的 5 个值填进 GitHub 仓库的 Secrets，"
        "王者荣耀皮肤监控就能用这个邮箱给你发提醒了。\n\n"
        f"发信账号：{user}\n"
        "测试时间：本地手动运行\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header("皮肤监控 · 邮件通道测试", "utf-8")
    msg["From"] = formataddr((str(Header("王者荣耀皮肤监控", "utf-8")), user))
    msg["To"] = to_addr

    print("正在连接并发送 ...")
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as srv:
                srv.login(user, password)
                srv.sendmail(user, [a.strip() for a in to_addr.split(",")], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=30) as srv:
                srv.starttls(context=ssl.create_default_context())
                srv.login(user, password)
                srv.sendmail(user, [a.strip() for a in to_addr.split(",")], msg.as_string())
    except smtplib.SMTPAuthenticationError as exc:
        print("\n[失败] 账号或授权码不对（SMTPAuthenticationError）")
        print(f"  服务器返回：{exc.smtp_code} {exc.smtp_error.decode('utf-8', 'ignore') if isinstance(exc.smtp_error, bytes) else exc.smtp_error}")
        print("\n逐条排查：")
        print("  1) 密码栏必须填 16 位授权码，不是 QQ 登录密码")
        print("  2) QQ 邮箱是否已开启 IMAP/SMTP 服务（设置 → 账号与安全）")
        print("  3) 授权码是否已被重置/失效（重新生成会作废旧码）")
        print("  4) 授权码前后有没有多复制空格（本脚本已自动去空格）")
        return 1
    except (socket.timeout, TimeoutError):
        print("\n[失败] 连接超时。可能被网络/公司防火墙拦了 465 端口。")
        print("  试试改用 587：SMTP_PORT=587，并确认服务器支持 STARTTLS。")
        return 1
    except smtplib.SMTPServerDisconnected:
        print("\n[失败] 服务器还没校验密码就断开了连接，通常是下面几种原因：")
        print("  1) 邮箱地址写错了（QQ 对不存在的账号会直接断开，不会提示密码错误）")
        print("  2) 端口和加密方式不匹配：465 配 SSL，587 配 STARTTLS，别混用")
        print("  3) 短时间内重试太多次，被临时限流，等几分钟再试")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\n[失败] {type(exc).__name__}: {exc}")
        print("  把这段报错发给我，我帮你定位。")
        return 1

    print("\n[成功] 已发送！去收件箱看看（顺便看一眼垃圾邮件箱）。")
    print("\n接下来去 GitHub 仓库配置这 5 个 Secret：")
    print(f"  SMTP_HOST = {host}")
    print(f"  SMTP_PORT = {port}")
    print(f"  SMTP_USER = {user}")
    print("  SMTP_PASS = <你的 16 位授权码>")
    print(f"  MAIL_TO   = {to_addr}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(1)
