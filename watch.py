#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
王者荣耀 · 阿轲 / 司马懿 皮肤上新 & 返场监控

提醒策略（重点）：
  采用「5 天窗口」而不是「只提醒一次」——
  皮肤自上线日起 5 天内，每天检查都会提醒一次；返场/活动公告自发现起同样连续提醒 5 天。
  超过窗口自动不再提醒。这样你错过当天的消息，第二天还会再看到，不会一觉睡过就彻底错过。

数据源（均为官方公开接口，无需登录）：
  1) https://pvp.qq.com/zlkdatasys/heroskinlist.json   ← 主源（皮肤图鉴，官方维护）
     顶层两个 key：
       pflb20_3469  皮肤列表（约 830 条），每条含：
            pfidlb_3934  皮肤 ID
            pfmclb_7523  皮肤名
            yxmclb_9965  英雄名
            sxsjlb_1516  上线日期 YYYYMMDD   ← 窗口期起算点
            pfpzlb_3289  品质（勇者/史诗/传说/珍品传说/荣耀典藏/无双/各种"限定"）
            hqfs_8609    获取方式（商城直售/限时秒杀/祈愿…）
            yjhjsl_5003  一句话介绍
            fmlb_4536 / yxtxlb_8443  竖版 / 横版海报
            pcljlb_9272 / mdljlb_1924  PC / 移动端皮肤详情页
       yxlb20_2489  英雄列表（约 133 条），含 yxid_a7（英雄 ID）、yxmclb_9965（英雄名）
  2) https://pvp.qq.com/web201706/newsindex.shtml      ← 公告源（GBK）
     返场在图鉴里没有状态字段，只能靠官方公告捕捉；公告自"首次发现日"起算 5 天窗口。

  ⚠️ herolist.json（旧接口）的 skin_name 字段更新滞后：实测缺少阿轲「罗刹妄海」
     （2026-03-06 上线的传说皮肤）。基线一律以图鉴接口为准。
"""

import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.header import Header
from email.mime.text import MIMEText

# ------------------------- 配置区 -------------------------
WATCH_HEROES = ["阿轲", "司马懿"]          # 想盯的英雄，可随意增减
ALERT_WINDOW_DAYS = 5                       # 上线/返场后连续提醒几天
SKIN_INDEX_URL = "https://pvp.qq.com/zlkdatasys/heroskinlist.json"
NEWS_LIST_URL = "https://pvp.qq.com/web201706/newsindex.shtml"
NEWS_DETAIL_URL = "https://pvp.qq.com/web201706/newsdetail.shtml?tid={tid}"
STATE_FILE = "state.json"
MAX_DETAIL_FETCH = 8                        # 每次最多抓几个新公告正文，控制耗时

# 图鉴字段映射（接口字段名是混淆过的，集中放这里方便日后调整）
F_SKIN_ID = "pfidlb_3934"
F_SKIN_NAME = "pfmclb_7523"
F_HERO = "yxmclb_9965"
F_DATE = "sxsjlb_1516"
F_QUALITY = "pfpzlb_3289"
F_ACQUIRE = "hqfs_8609"
F_DESC = "yjhjsl_5003"
F_POSTER = "fmlb_4536"
F_URL_PC = "pcljlb_9272"
F_URL_MOBILE = "mdljlb_1924"

# 皮肤相关关键词：判断公告是否在讲皮肤
SKIN_KEYWORDS = [
    "返场", "限时", "上新", "新皮肤", "皮肤", "上架", "出售", "兑换",
    "祈愿", "抽奖", "典藏", "星元", "预售", "秒杀", "特惠", "礼包",
]
# 干扰项：体验服更新、处罚公示、赛事招募等与商城皮肤无关
NOISE_KEYWORDS = ["体验服", "停机维护", "处罚", "封号", "赛事", "招募", "问卷"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
CST = timezone(timedelta(hours=8))


# ------------------------- 工具函数 -------------------------
def log(*args):
    print(*args, flush=True)


def http_get(url, timeout=30, retries=3):
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": UA, "Referer": "https://pvp.qq.com/"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log(f"  ! 请求失败({attempt + 1}/{retries}) {url} -> {exc}")
            time.sleep(2 * (attempt + 1))
    raise last_err


def decode_text(raw):
    """图鉴接口是 UTF-8；官网老公告页是 GBK，统一兜底处理。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", errors="ignore")


def now_cst():
    return datetime.now(CST)


def today_cst():
    return now_cst().date()


def parse_yyyymmdd(s):
    """'20260306' -> date(2026,3,6)；无效返回 None。"""
    s = (s or "").strip()
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:]))
        except ValueError:
            return None
    return None


def fmt_date(d):
    return d.isoformat() if isinstance(d, date) else "未知"


def age_days(start):
    """从 start 到今天经过的天数（0 = 今天）。"""
    return (today_cst() - start).days


def day_label(age):
    """把天数差翻译成'第 N 天 / 还剩 M 天'，让提醒自带紧迫感。"""
    left = ALERT_WINDOW_DAYS - age - 1      # 今天之后还会再提醒几天
    if left <= 0:
        return f"第 {age + 1} 天，今天是最后一天"
    return f"第 {age + 1} 天，还会再提醒 {left} 天"


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        log(f"  ! state.json 读取失败，按空状态处理: {exc}")
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


# ------------------------- 数据抓取 -------------------------
def fetch_skin_index():
    """从皮肤图鉴接口取出关注英雄的皮肤，返回 {英雄: {皮肤名: meta}}。"""
    data = json.loads(decode_text(http_get(SKIN_INDEX_URL)))
    rows = data.get("pflb20_3469") or []
    if not rows:
        raise RuntimeError("图鉴接口结构变化：未找到 pflb20_3469 皮肤列表")
    log(f"  图鉴接口返回 {len(rows)} 条皮肤记录")

    result = {h: {} for h in WATCH_HEROES}
    for row in rows:
        hero = (row.get(F_HERO) or "").strip()
        if hero not in result:
            continue
        name = (row.get(F_SKIN_NAME) or "").strip()
        if not name:
            continue
        # 图鉴偶有脏数据（同一 ID 对应不同皮肤名），用「英雄+皮肤名」做唯一键最稳
        result[hero][name] = {
            "id": (row.get(F_SKIN_ID) or "").strip(),
            "date": (row.get(F_DATE) or "").strip(),
            "quality": (row.get(F_QUALITY) or "").strip(),
            "acquire": (row.get(F_ACQUIRE) or "").strip(),
            "desc": (row.get(F_DESC) or "").strip(),
            "poster": (row.get(F_POSTER) or "").strip(),
            "url": (row.get(F_URL_MOBILE) or row.get(F_URL_PC) or "").strip(),
        }

    for hero, skins in result.items():
        if not skins:
            log(f"  ! 图鉴中未找到英雄「{hero}」，请确认名字与官网一致")
    return result


def fetch_news_list():
    html = decode_text(http_get(NEWS_LIST_URL))
    pattern = re.compile(
        r'newsdetail\.shtml\?tid=(\d+)"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{2,80}?)\s*<',
        re.S,
    )
    items, seen = [], set()
    for tid, title in pattern.findall(html):
        title = re.sub(r"\s+", " ", title).strip()
        if not title or tid in seen:
            continue
        seen.add(tid)
        items.append({"tid": tid, "title": title})
    log(f"  公告列表解析到 {len(items)} 条")
    return items


def fetch_news_detail(tid):
    try:
        html = decode_text(http_get(NEWS_DETAIL_URL.format(tid=tid), retries=2))
    except Exception as exc:  # noqa: BLE001
        log(f"  ! 公告正文抓取失败 tid={tid}: {exc}")
        return ""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


def is_skin_related(text):
    return any(k in text for k in SKIN_KEYWORDS)


def is_noise(text):
    return any(k in text for k in NOISE_KEYWORDS)


# ------------------------- 核心检测 -------------------------
def detect(state):
    """
    返回 (candidates, new_state, first_run, index)。
    candidates 里每个元素 = 一条"当前仍在 5 天窗口内"的动态。
    """
    first_run = not state.get("initialized")
    seen_news = set(state.get("seen_news", []))
    alerts = dict(state.get("alerts", {}))
    today = today_cst()

    candidates = []

    # ---- 1. 皮肤图鉴：按「上线日期」算窗口，5 天内天天提醒 ----
    index = fetch_skin_index()
    new_skin_names = set()
    for hero in WATCH_HEROES:
        for name, meta in (index.get(hero) or {}).items():
            d = parse_yyyymmdd(meta["date"])
            if not d:
                continue
            age = age_days(d)
            if age < 0 or age >= ALERT_WINDOW_DAYS:
                continue        # 未上线 / 已过窗口，不提醒
            new_skin_names.add(name)
            log(f"  ★ 窗口内新皮肤：{hero} · {name}（{age + 1} 天）")
            candidates.append({
                "key": f"skin|{hero}|{name}",
                "kind": "新皮肤",
                "title": f"新皮肤 · {hero}「{name}」",
                "start": d,
                "age": age,
                "lines": [
                    f"- 上线日期：{fmt_date(d)}（{day_label(age)}）",
                    f"- 品质：{meta['quality'] or '未标注'}",
                    f"- 获取方式：{meta['acquire'] or '未标注'}",
                    f"- 皮肤 ID：{meta['id'] or '—'}",
                ] + ([f"- 简介：{meta['desc']}"] if meta["desc"] else [])
                  + ([f"- 详情：{meta['url']}"] if meta["url"] else []),
            })

    # ---- 2. 公告：按「首次发现日」算窗口，5 天内天天提醒 ----
    news_items = fetch_news_list()
    cur_tids = [n["tid"] for n in news_items]
    detail_checked = 0

    for item in news_items:
        tid, title = item["tid"], item["title"]
        if first_run:
            continue        # 首跑不刷历史消息，只把当前公告记为已见
        news_key = f"news|{tid}"

        known = news_key in alerts
        start = date.fromisoformat(alerts[news_key]["first_seen"]) if known else today
        age = age_days(start)
        if age < 0 or age >= ALERT_WINDOW_DAYS:
            continue        # 已过 5 天窗口
        if tid in seen_news and not known:
            continue        # 早就见过且不进提醒队列的旧公告，不再处理

        hero_hit = [h for h in WATCH_HEROES if h in title]
        if hero_hit and is_skin_related(title) and not is_noise(title):
            # 这条公告讲的就是本轮已提醒的新皮肤时，信息重复，跳过
            if any(n in title for n in new_skin_names):
                log(f"  · 公告已被新皮肤提醒覆盖：{title}")
                continue
            log(f"  ★ 窗口内相关公告：{title}")
            candidates.append({
                "key": news_key,
                "kind": "返场/活动",
                "title": f"{title}",
                "start": start,
                "age": age,
                "lines": [
                    f"- 发现日期：{fmt_date(start)}（{day_label(age)}）",
                    f"- 命中英雄：{'、'.join(hero_hit)}",
                    f"- 链接：{NEWS_DETAIL_URL.format(tid=tid)}",
                ],
            })
            continue

        # 标题没提，但新公告正文里可能提到（例如"11月5日全服不停机更新公告"）
        if tid in seen_news or detail_checked >= MAX_DETAIL_FETCH or is_noise(title):
            continue
        detail_checked += 1
        body = fetch_news_detail(tid)
        time.sleep(0.6)
        if not body or not is_skin_related(body):
            continue
        for hero in WATCH_HEROES:
            hit_window = None
            for m in re.finditer(hero, body):
                window = body[max(0, m.start() - 60): m.start() + 80]
                if is_skin_related(window):
                    hit_window = window.strip()
                    break
            if hit_window:
                log(f"  ★ 正文命中：{hero} / {title}")
                candidates.append({
                    "key": news_key,
                    "kind": "返场/活动",
                    "title": title,
                    "start": start,
                    "age": age,
                    "lines": [
                        f"- 发现日期：{fmt_date(start)}（{day_label(age)}）",
                        f"- 正文摘录：…{hit_window}…",
                        f"- 链接：{NEWS_DETAIL_URL.format(tid=tid)}",
                    ],
                })
                break

    # ---- 3. 组装新状态 ----
    merged = {k: dict(v) for k, v in (state.get("skins") or {}).items()}
    for hero, cur in index.items():
        merged.setdefault(hero, {}).update(cur)

    new_alerts = dict(alerts)
    for c in candidates:
        new_alerts.setdefault(c["key"], {"first_seen": today.isoformat()})
    # 只保留最近 60 天的提醒记录，避免文件无限膨胀
    cutoff = (today - timedelta(days=60)).isoformat()
    new_alerts = {k: v for k, v in new_alerts.items() if v.get("first_seen", "") >= cutoff}

    new_state = {
        "version": 4,
        "initialized": True,
        "skins": {h: merged.get(h, {}) for h in set(list(merged.keys()) + WATCH_HEROES)},
        "seen_news": sorted(set(seen_news) | set(cur_tids))[-400:],
        "alerts": new_alerts,
        "last_run_cst": now_cst().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return candidates, new_state, first_run, index


# ------------------------- 推送 -------------------------
def push_pushplus(title, content):
    token = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if not token:
        return False
    payload = json.dumps(
        {"token": token, "title": title, "content": content, "template": "markdown"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://www.pushplus.plus/send",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", "ignore")
        log(f"  PushPlus 返回: {body[:200]}")
        return '"code":200' in body.replace(" ", "")
    except Exception as exc:  # noqa: BLE001
        log(f"  ! PushPlus 推送失败: {exc}")
        return False


def push_mail(subject, content):
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "").strip()
    to_addr = os.getenv("MAIL_TO", "").strip() or user
    if not (host and user and password and to_addr):
        return False
    port = int(os.getenv("SMTP_PORT", "465") or 465)

    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = to_addr
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=25) as srv:
                srv.login(user, password)
                srv.sendmail(user, [a.strip() for a in to_addr.split(",")], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=25) as srv:
                srv.starttls(context=ssl.create_default_context())
                srv.login(user, password)
                srv.sendmail(user, [a.strip() for a in to_addr.split(",")], msg.as_string())
        log(f"  邮件已发送 -> {to_addr}")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"  ! 邮件发送失败: {exc}")
        return False


def has_channel():
    if os.getenv("PUSHPLUS_TOKEN", "").strip():
        return True
    return all(os.getenv(k, "").strip() for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))


def notify(subject, markdown):
    """返回 (是否配置了通道, 是否真的发出去)。"""
    log("----- 推送内容 -----")
    log(markdown)
    log("--------------------")
    if not has_channel():
        log("  ! 未配置任何推送通道，本次仅输出到运行日志")
        return False, False
    delivered = push_pushplus(subject, markdown)
    delivered = push_mail(subject, markdown) or delivered
    if not delivered:
        log("  ! 通道已配置但发送失败")
    return True, delivered


# ------------------------- 主流程 -------------------------
def main():
    log(f"== 王者荣耀皮肤监控 | {now_cst().strftime('%Y-%m-%d %H:%M:%S')} 北京时间 ==")
    log(f"   监控英雄: {', '.join(WATCH_HEROES)}｜提醒窗口: {ALERT_WINDOW_DAYS} 天")

    state = load_state()
    try:
        candidates, new_state, first_run, index = detect(state)
    except Exception as exc:  # noqa: BLE001
        log(f"!! 检测异常，保留旧状态不覆盖: {exc}")
        notify(
            "王者荣耀皮肤监控运行异常",
            f"本次检查（{now_cst().strftime('%Y-%m-%d %H:%M')}）抓取失败，状态未更新，明天会重试。\n\n"
            f"错误信息：{exc}\n\n如果连续多天报错，多半是官网接口地址调整，需要更新脚本。",
        )
        return 1

    save_state(new_state)

    if first_run:
        lines = []
        for hero in WATCH_HEROES:
            skins = index.get(hero, {})
            newest = max(
                ((n, m) for n, m in skins.items() if parse_yyyymmdd(m["date"])),
                key=lambda kv: kv[1]["date"],
                default=None,
            )
            if newest:
                lines.append(
                    f"- **{hero}**：图鉴收录 {len(skins)} 款，最新为「{newest[0]}」"
                    f"（{newest[1]['quality'] or '未标注'}）"
                )
            else:
                lines.append(f"- **{hero}**：图鉴中暂无记录")
        extra = ""
        if candidates:
            extra = "\n\n当前已在提醒窗口内的动态：\n" + "\n".join(
                f"- {c['title']}（{day_label(c['age'])}）" for c in candidates
            )
        notify(
            "王者荣耀皮肤监控已启动",
            "监控已开始运行，之后每天北京时间 08:00 检查一次。\n\n"
            f"提醒规则：皮肤上线后 / 返场公告发现后，**连续 {ALERT_WINDOW_DAYS} 天每天提醒一次**，"
            "过窗口自动停止。\n\n"
            "当前基线：\n" + "\n".join(lines) + extra,
        )
        return 0

    if not candidates:
        log("   当前没有窗口内的新皮肤或返场，静默结束。")
        return 0

    body = [
        f"检查时间：{now_cst().strftime('%Y-%m-%d %H:%M')}（北京时间）",
        f"以下动态仍在 {ALERT_WINDOW_DAYS} 天提醒窗口内：",
        "",
    ]
    for c in candidates:
        body.append(f"### {c['title']}")
        body.extend(c["lines"])
        body.append("")

    if len(candidates) == 1:
        subject = f"[{candidates[0]['kind']}] {candidates[0]['title']}（{day_label(candidates[0]['age'])}）"
    else:
        subject = f"王者荣耀：{len(candidates)} 条皮肤动态在提醒窗口内"
    notify(subject, "\n".join(body))
    log(f"   本次提醒 {len(candidates)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
