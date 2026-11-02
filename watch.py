#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
王者荣耀 · 阿轲 / 司马懿 皮肤上新 & 返场监控

提醒策略（重点）：
  新皮肤：自上线日起 ALERT_WINDOW_DAYS 天内，每天检查都会提醒一次，过窗口自动停。
  返场：  直接用公告正文里的真实起止日期，**期间内每天都提醒**，到期自动停；
          正文没写结束日期时，退回"发现日起 ALERT_WINDOW_DAYS 天"。
  这样你错过当天的消息，第二天还会再看到，不会一觉睡过就彻底错过。

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
  2) https://pvp.qq.com/web201706/newsindex.shtml      ← 公告列表（GBK）
  3) https://apps.game.qq.com/wmp/v3.1/public/searchNews.php?p0=18&source=web_pc&id={tid}
     ← 公告正文。这是官网 SPA 自己用的内容接口，只认 tid、不依赖栏目号，
       返回 JSON：msg.sContent=正文HTML、msg.sCreated=发布时间（含年月日）。
     正文里返场是结构化的，实测样本：
         返场皮肤：妲己-愿照·众生和、司空震-愿照·山河定、西施-续相思
         返场时间：10月24日0:00-10月30日23:59
         返场价格：1788点券/款
     所以返场不靠"发现日+固定窗口"估算，直接用官方的真实起止日期做倒计时。

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
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import html as _html

# ------------------------- 配置区 -------------------------
WATCH_HEROES = ["阿轲", "司马懿"]          # 想盯的英雄，可随意增减
ALERT_WINDOW_DAYS = 30                      # 上线/返场后连续提醒几天
MAX_REMINDERS_PER_SKIN = 3                 # 同款皮肤最多提醒几次后停止（避免刷屏）
SKIN_INDEX_URL = "https://pvp.qq.com/zlkdatasys/heroskinlist.json"
NEWS_LIST_URL = "https://pvp.qq.com/web201706/newsindex.shtml"
NEWS_DETAIL_URL = "https://pvp.qq.com/web201706/newsdetail.shtml?tid={tid}"
# —— 取正文：官网 SPA 自己用的内容接口，只认 tid，与栏目号无关 ——
# 返回 JSONP：var searchObj={"msg":{"sTitle":..,"sContent":<正文HTML>,"sCreated":"YYYY-MM-DD HH:MM:SS"}}
# p0=18 是《王者荣耀》的 gameID（官网 newsdetail 页 fillNews.detail({gameID:18}) 确认）。
# 不再猜测/拼接 gicp 静态页的栏目号（iDocType），单一数据源，干净可靠。
GAME_ID = 18
NEWS_API_URL = ("https://apps.game.qq.com/wmp/v3.1/public/searchNews.php"
                "?p0={gid}&source=web_pc&id={tid}")
STATE_FILE = "state.json"
MAX_DETAIL_FETCH = 12                       # 每次最多抓几个新公告正文，控制耗时

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


def md_to_html(md_text):
    """把推送用的轻量 Markdown 转成 HTML 邮件正文（图片/加粗/链接/换行）。"""
    out = []
    for line in md_text.splitlines():
        esc = _html.escape(line)
        h = re.match(r"^###\s+(.*)$", esc)
        if h:
            out.append(f"<h3 style=\"margin:14px 0 6px;\">{h.group(1)}</h3>")
            continue
        img = re.search(r"!\[([^\]]*)\]\(([^)]+)\)", esc)
        if img:
            out.append(
                f'<img src="{img.group(2)}" alt="{img.group(1)}" '
                f'style="max-width:180px;border-radius:8px;display:block;margin:6px 0;">'
            )
            continue
        esc = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', esc)
        esc = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", esc)
        esc = re.sub(r'(?<!href=")(?<!">)(https?://[^\s<]+)', r'<a href="\1">\1</a>', esc)
        if esc.startswith("- "):
            esc = "&bull; " + esc[2:]
        out.append("<br>" if not esc.strip() else esc + "<br>")
    return (
        '<div style="font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif;'
        'font-size:15px;line-height:1.8;">' + "".join(out) + "</div>"
    )


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


def _html_to_text(html):
    """HTML 片段 → 纯文本（去脚本/样式/标签、还原常见实体、压缩空白）。"""
    body = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    for a, b in (("&nbsp;", " "), ("&middot;", "·"), ("&ldquo;", ""),
                 ("&rdquo;", ""), ("&amp;", "&"), ("&quot;", '"')):
        body = body.replace(a, b)
    return re.sub(r"\s+", " ", body)


def fetch_news_body(tid):
    """
    抓公告正文：官网内容接口，只认 tid、与栏目号无关。
    返回 (纯文本, 发布日期 date|None)：
      - 抓取或解析失败返回 None（调用方据此不标记已读，下次运行自动重试）；
      - 抓到但没有正文返回 ("", date|None)（属正常，可标记已读）。
    """
    try:
        raw = http_get(NEWS_API_URL.format(gid=GAME_ID, tid=tid), retries=3)
    except Exception as exc:  # noqa: BLE001
        log(f"  ! 新闻接口取正文失败 tid={tid}: {exc}")
        return None
    s = decode_text(raw)
    i, j = s.find("{"), s.rfind("}")   # 剥掉 JSONP 外壳（var searchObj=...; / cb(...)）
    if i == -1 or j <= i:
        log(f"  ! 新闻接口返回无法解析 tid={tid}")
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:  # noqa: BLE001
        log(f"  ! 新闻接口 JSON 解析失败 tid={tid}")
        return None
    msg = obj.get("msg") or {}
    content = msg.get("sContent") or ""
    pub_date = None
    created = msg.get("sCreated") or ""
    cm = re.match(r"(\d{4})-(\d{2})-(\d{2})", created)
    if cm:
        try:
            pub_date = date(int(cm.group(1)), int(cm.group(2)), int(cm.group(3)))
        except ValueError:
            pub_date = None
    return _html_to_text(content), pub_date


# 「英雄-皮肤」文本形态，例如 妲己-愿照·众生和 / 公孙离-涂山容容
HERO_SKIN_RE = re.compile(r"([\u4e00-\u9fa5]{2,5})\s*[-－]\s*([\u4e00-\u9fa5·]{2,14})")
# 返场时间：10月24日0:00-10月30日23:59 / 8月19日 - 9月19日23:59:59
RETURN_TIME_RE = re.compile(
    r"(\d{1,2})月(\d{1,2})日[^0-9]{0,8}(\d{1,2})[:：]\s*\d{2}?"      # 起始 月日 时
    r".{0,12}?[-－~～至到].{0,12}?"
    r"(?:(\d{1,2})月)?(\d{1,2})日[^0-9]{0,8}(\d{1,2})[:：]\s*(\d{2})",  # 结束 月日 时分
)


def _match_skins(ctx, skin_index):
    """
    在图鉴已知皮肤名里找正文提及的那几款。
    用「已知皮肤名 + 英雄名同时出现」判定，比正则切「英雄-皮肤」稳得多：
    正则会把「涂山容容限时返场中」整段吞成皮肤名，已实测踩过。
    """
    hits = []
    for hero, skins in skin_index.items():
        if hero not in ctx:
            continue
        for skin in skins:
            if skin and skin in ctx:
                hits.append((hero, skin))
    return hits


def parse_return_blocks(text, pub_date, skin_index):
    """
    从公告正文里抽出「返场」条目。
    返回 [{'hero','skin','start','end','price','snippet'}]。
    只保留 WATCH_HEROES 里、且皮肤名确实收录于图鉴的记录
    （交叉校验可过滤「周年庆回城-时光定格」这类非皮肤条目和礼盒杂项）。
    """
    found = []
    positions = [m.start() for m in re.finditer(r"返场时间", text)]
    for k, i in enumerate(positions):
        # 上下文只取「上一段返场时间之后 → 本次返场时间之前」，
        # 否则同一公告里两段返场会互相串味（第二款皮肤套用了第一款的时间）
        if k > 0:
            lo = positions[k - 1] + 4
        else:
            anchors = [m.start() for m in re.finditer(r"返场", text[:i])]
            lo = max(0, (anchors[-1] - 140) if anchors else i - 220)
        ctx = text[lo:i]

        tm = RETURN_TIME_RE.search(text[i:i + 80])
        if not tm:
            continue
        sm, sd, sh, em, ed, eh, emin = tm.groups()
        year = pub_date.year if pub_date else now_cst().year
        start_m, end_m = int(sm), int(em) if em else int(sm)
        # 跨年修正：12 月发的公告讲 1 月返场
        start_y = year
        end_y = year + 1 if end_m < start_m - 6 else year
        try:
            start_d = date(start_y, start_m, int(sd))
            end_d = date(end_y, end_m, int(ed))
        except ValueError:
            continue
        if end_d < start_d:
            continue

        # 官网排版是「返场价格 ：1788点券/款」，冒号前有空格
        price = re.search(r"返场价格\s*[:：]\s*([^\s，。；]{1,30})", text[i:i + 200])
        price = price.group(1) if price else ""

        for hero, skin in _match_skins(ctx, skin_index):
            found.append({
                "hero": hero,
                "skin": skin,
                "start": start_d.isoformat(),
                "end": end_d.isoformat(),
                "price": price,
                "snippet": text[max(0, i - 60):i + 120].strip(),
            })

    # 第二遍：有些公告只写「限定返场皮肤：A-x、B-y」而不给时间（如惊喜折扣奖池）
    for hit in re.finditer(r"返场皮肤\s*[:：]|限定返场皮肤\s*[:：]", text):
        seg = text[hit.start():hit.start() + 100]
        for hero, skin in _match_skins(seg, skin_index):
            found.append({
                "hero": hero, "skin": skin,
                "start": pub_date.isoformat() if pub_date else None,
                "end": None,          # 无结束日期，退回固定窗口
                "price": "",
                "snippet": seg.strip()[:120],
            })

    # 去重：同款皮肤优先保留「有结束日期」的那条（有真实窗口就别用估算窗口）
    best = {}
    for r in found:
        k = (r["hero"], r["skin"])
        if k not in best:
            best[k] = r
            continue
        old = best[k]
        if old.get("end") is None and r.get("end") is not None:
            best[k] = r
    return list(best.values())


def is_skin_related(text):
    return any(k in text for k in SKIN_KEYWORDS)


def is_noise(text):
    return any(k in text for k in NOISE_KEYWORDS)


# ------------------------- 核心检测 -------------------------
def detect(state):
    """
    返回 (candidates, new_state, first_run, index)。
    candidates 里每个元素 = 一条"当前仍在提醒窗口内"的动态。
    """
    first_run = not state.get("initialized")
    seen_news = set(state.get("seen_news", []))
    alerts = dict(state.get("alerts", {}))
    today = today_cst()

    candidates = []

    # ---- 1. 皮肤图鉴：按「上线日期」算窗口，窗口内天天提醒 ----
    index = fetch_skin_index()
    for hero in WATCH_HEROES:
        for name, meta in (index.get(hero) or {}).items():
            d = parse_yyyymmdd(meta["date"])
            if not d:
                continue
            age = age_days(d)
            if age < 0 or age >= ALERT_WINDOW_DAYS:
                continue        # 未上线 / 已过窗口，不提醒
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
                  + ([f"  ![{name}]({meta['poster']})"] if meta.get("poster") else [])
                  + ([f"- 详情：{meta['url']}"] if meta["url"] else []),
            })

    # ---- 2. 返场：用公告正文里的真实起止日期，期间内天天提醒 ----
    returns = dict(state.get("returns") or {})     # 已登记的返场条目，持久化避免重复抓取
    news_items = fetch_news_list()
    cur_tids = [n["tid"] for n in news_items]
    detail_checked = 0

    # 2a. 从「没见过的公告」里挖新返场
    scanned_ok = set()          # 本次真正抓取成功并检查过的公告（决定是否标记已读）
    if not first_run:
        for item in news_items:
            tid, title = item["tid"], item["title"]
            if tid in seen_news or is_noise(title):
                continue
            if detail_checked >= MAX_DETAIL_FETCH:
                break
            detail_checked += 1
            got = fetch_news_body(tid)
            time.sleep(0.5)
            if got is None:
                log(f"  · 正文抓取失败，保留待下次重试 tid={tid}")
                continue                    # 不记已读：下次运行重新抓
            body, pub = got
            scanned_ok.add(tid)
            if not body or "返场" not in body:
                continue
            for r in parse_return_blocks(body, pub, index):
                key = f"ret|{r['hero']}|{r['skin']}|{r['start'] or 'na'}"
                if key in returns:
                    continue
                r.update({"tid": tid, "title": title, "first_seen": today.isoformat()})
                returns[key] = r
                log(f"  ★ 新返场：{r['hero']} · {r['skin']}"
                    f"（{r['start'] or '起止未标'} ~ {r['end'] or '未标'}）")

    # 2b. 已登记的返场，按真实起止日期判断是否在提醒期
    for key, r in sorted(returns.items()):
        start = date.fromisoformat(r["start"]) if r.get("start") else \
            date.fromisoformat(r["first_seen"])
        if r.get("end"):
            end = date.fromisoformat(r["end"])
        else:
            end = start + timedelta(days=ALERT_WINDOW_DAYS - 1)   # 无结束日期，退回固定窗口

        if today > end:
            continue                        # 已结束
        if today < start:
            lead = (start - today).days
            if lead > 3:
                continue                    # 还早，先不打扰
            status = f"还有 {lead} 天开始"
        else:
            left = (end - today).days
            status = "今天是最后一天" if left == 0 else f"还剩 {left} 天截止"

        lines = [
            f"- 返场时间：{fmt_date(start)} ~ {fmt_date(end)}（{status}）",
        ]
        if r.get("price"):
            lines.append(f"- 返场价格：{r['price']}")
        meta = (index.get(r["hero"]) or {}).get(r["skin"]) or {}
        if meta.get("quality"):
            lines.append(f"- 品质：{meta['quality']}")
        if r.get("tid"):
            lines.append(f"- 公告：《{r.get('title') or '官方公告'}》")
            lines.append(f"- 链接：{NEWS_DETAIL_URL.format(tid=r['tid'])}")
        log(f"  ★ 提醒期内返场：{r['hero']} · {r['skin']}（{status}）")
        candidates.append({
            "key": key,
            "kind": "限时返场",
            "title": f"限时返场 · {r['hero']}「{r['skin']}」",
            "start": start,
            "age": max(0, (today - start).days),
            "lines": lines,
        })

    # ---- 3. 组装新状态 ----
    merged = {k: dict(v) for k, v in (state.get("skins") or {}).items()}
    for hero, cur in index.items():
        merged.setdefault(hero, {}).update(cur)

    new_alerts = {k: dict(v) for k, v in alerts.items()}
    for c in candidates:
        key = c["key"]
        rec = new_alerts.setdefault(key, {"first_seen": today.isoformat(), "count": 0})
        c["sent"] = rec.get("count", 0)
    # 只保留最近 90 天的提醒记录，避免文件无限膨胀
    cutoff = (today - timedelta(days=90)).isoformat()
    new_alerts = {k: v for k, v in new_alerts.items() if v.get("first_seen", "") >= cutoff}

    # 返场条目：结束后再留 30 天，之后清掉
    keep_returns = {}
    for k, r in returns.items():
        end_s = r.get("end") or r.get("start") or r.get("first_seen") or ""
        if end_s >= (today - timedelta(days=30)).isoformat():
            keep_returns[k] = r

    # 首次运行：以当前公告列表为基线，全部标记已读，避免刚开始就补发一堆旧公告；
    # 之后：只标记「本次真正抓取成功并检查过」的，抓取失败的留待下次重试（不遗漏）。
    seen_after = set(cur_tids) if first_run else scanned_ok
    new_state = {
        "version": 5,
        "initialized": True,
        "skins": {h: merged.get(h, {}) for h in set(list(merged.keys()) + WATCH_HEROES)},
        "seen_news": sorted(set(seen_news) | seen_after)[-400:],
        "alerts": new_alerts,
        "returns": keep_returns,
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

    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(content, "plain", "utf-8"))
    msg.attach(MIMEText(md_to_html(content), "html", "utf-8"))
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
    log(f"   监控英雄: {', '.join(WATCH_HEROES)}｜新皮肤窗口: {ALERT_WINDOW_DAYS} 天")

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
                d = parse_yyyymmdd(newest[1]["date"])
                lines.append(
                    f"- **{hero}**：图鉴收录 {len(skins)} 款，最新为「{newest[0]}」"
                    f"（{newest[1]['quality'] or '未标注'}，{fmt_date(d)} 上线）"
                )
                if newest[1].get("poster"):
                    lines.append(f"  ![{newest[0]}]({newest[1]['poster']})")
            else:
                lines.append(f"- **{hero}**：图鉴中暂无记录")
        extra = ""
        if candidates:
            extra = "\n\n当前已在提醒窗口内的动态：\n" + "\n".join(
                f"- {c['title']}（{day_label(c['age'])}）" for c in candidates
            )
        notify(
            "王者荣耀皮肤监控已启动",
            "监控已开始运行，之后每天上午检查一次（云端定时任务偶有排队，实际在 8 点前后浮动）。\n\n"
            "【当前基线】（之后只提醒比这更新的动态）\n" + "\n".join(lines) + extra + "\n\n"
            "【提醒规则】\n"
            f"- 新皮肤：上线后 {ALERT_WINDOW_DAYS} 天内每天提醒一次，错过当天第二天还能看到，过窗口自动停\n"
            "- 限时返场：按公告里的官方起止日期，开始前预告 + 期内每天提醒，到期自动停\n"
            f"- 同一款皮肤最多提醒 {MAX_REMINDERS_PER_SKIN} 次后自动停止，避免刷屏\n"
            "- 没有新动态时一条消息都不发，不打扰",
        )
        save_state(new_state)
        return 0

    # 同一款皮肤发满上限后不再发
    to_send = [c for c in candidates if c.get("sent", 0) < MAX_REMINDERS_PER_SKIN]
    if len(to_send) < len(candidates):
        log(f"  跳过 {len(candidates) - len(to_send)} 条已达 {MAX_REMINDERS_PER_SKIN} 次上限的动态")

    if not to_send:
        log("   当前没有需要提醒的新皮肤或返场，静默结束。")
        save_state(new_state)
        return 0

    body = [
        f"检查时间：{now_cst().strftime('%Y-%m-%d %H:%M')}（北京时间）",
        f"共 {len(to_send)} 条动态仍在提醒期内：",
        "",
    ]
    for c in to_send:
        body.append(f"### {c['title']}")
        body.extend(c["lines"])
        body.append(f"- 提醒进度：第 {c['sent'] + 1} 次 / 最多 {MAX_REMINDERS_PER_SKIN} 次")
        body.append("")

    if len(to_send) == 1:
        subject = f"[{to_send[0]['kind']}] {to_send[0]['title']}"
    else:
        subject = f"王者荣耀：{len(to_send)} 条皮肤动态"
    _, delivered = notify(subject, "\n".join(body))
    if delivered:
        for c in to_send:
            rec = new_state["alerts"].setdefault(
                c["key"], {"first_seen": today_cst().isoformat(), "count": 0}
            )
            rec["count"] = rec.get("count", 0) + 1
    save_state(new_state)
    log(f"   本次提醒 {len(to_send)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
