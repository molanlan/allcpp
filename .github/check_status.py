#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
allcpp 摊位审核提醒（精简版）

规则：状态只要不是「审核中」，就推送一次提醒；同一条状态只提醒一次，不重复打扰。

环境变量:
  ALLCPP_COOKIE   allcpp 的完整 Cookie
  WECOM_WEBHOOK   企业微信群机器人 webhook（优先）
  SERVERCHAN_KEY  Server酱 SendKey（没配企业微信时用）
"""
import json
import os
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "state.json")
RESULT_FILE = os.path.join(BASE, "last_run.json")

API = "https://www.allcpp.cn/allcpp/event/getEventListApprove.do"
PARAMS = {
    "eventmainid": "0",
    "pageindex": "1",
    "pagesize": "20",
    "showapprove": "1",   # t=1 已申请
    "eventEnabled": "1",
    "cityId": "-1",
    "typeId": "-1",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://www.allcpp.cn/mng/apply.do?t=1&pageNo=1",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

STATUS_MAP = {0: "已过审", 1: "审核中", 2: "未过审", 3: "未抽中", 4: "已退摊"}
QUIET_STATUS = "审核中"   # 这个状态只做定时播报，不算"有结果"
QUIET_HOUR_END = 8        # 本地 0:00-7:59 不播报（结果提醒不受影响）
TZ_OFFSET = 8             # 北京时间


def local_hour():
    import datetime
    return datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=TZ_OFFSET))).hour


# ---------- 推送 ----------
def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")


def notify(title, desp):
    hook = os.environ.get("WECOM_WEBHOOK", "").strip()
    key = os.environ.get("SERVERCHAN_KEY", "").strip()
    if not hook and not key:
        print("[warn] 没有配置推送渠道，跳过")
        return "no_channel"
    try:
        if hook:
            body = json.dumps({"msgtype": "markdown",
                               "markdown": {"content": "**%s**\n%s" % (title, desp)}},
                              ensure_ascii=False).encode("utf-8")
            r = _post(hook, body, {"Content-Type": "application/json"})
            print("[notify] wecom:", r[:300])
            return "wecom:" + r[:300]
        if key.lower().startswith("sctp"):
            uid = ""
            for ch in key[4:]:
                if ch.isdigit():
                    uid += ch
                else:
                    break
            url = "https://%s.push.ft07.com/send/%s.send" % (uid, key)
        else:
            url = "https://sctapi.ftqq.com/%s.send" % key
        body = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
        r = _post(url, body, {"Content-Type": "application/x-www-form-urlencoded"})
        print("[notify] serverchan:", r[:300])
        return "serverchan:" + r[:300]
    except Exception as e:
        print("[notify failed]", e)
        return "failed:" + str(e)


# ---------- 数据 ----------
def get_cookie():
    c = os.environ.get("ALLCPP_COOKIE", "").strip()
    if c:
        return c
    p = os.path.join(BASE, "cookie.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def fetch(cookie):
    url = API + "?" + urllib.parse.urlencode(PARAMS)
    req = urllib.request.Request(url, headers={**HEADERS, "Cookie": cookie})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def status_text(ec):
    """和页面 getPositionStatus 一致的逻辑"""
    if not ec:
        return "未申请"
    enabled = ec.get("eventEnabled", -1)
    if enabled == 1 and ec.get("isReadyPublish"):
        return "预审通过 等待签约：%s摊" % ec.get("prePositionCount", 0)
    return STATUS_MAP.get(enabled, "未知(%s)" % enabled)


# ---------- 状态存取 ----------
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("notified", {})   # {活动id: 已提醒过的状态}
                d.setdefault("authExpired", False)
                return d
        except Exception:
            pass
    return {"notified": {}, "authExpired": False}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    print("[state] saved")


def save_result(data):
    try:
        with open(RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[result save failed]", e)


def now_str():
    import datetime
    n = datetime.datetime.now(datetime.timezone.utc)
    return n.astimezone().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 主流程 ----------
def main():
    cookie = get_cookie()
    state = load_state()
    notified = state.get("notified") or {}

    if not cookie:
        if not state.get("authExpired"):
            notify("allcpp 监控：缺少 Cookie", "请在仓库 Secrets 里配置 ALLCPP_COOKIE。")
            state["authExpired"] = True
            save_state(state)
        print("AUTH_EXPIRED 缺少 Cookie")
        return

    try:
        data = fetch(cookie)
    except Exception as e:
        msg = str(e)
        quiet = state.get("authExpired")
        if "401" in msg or "403" in msg or "login" in msg.lower():
            if not quiet:
                notify("allcpp 监控：登录已失效",
                       "Cookie 过期了，请重新抓取并更新 Secrets 里的 ALLCPP_COOKIE。")
                state["authExpired"] = True
                save_state(state)
            print("AUTH_EXPIRED", msg)
        else:
            print("ERROR", msg)
        return

    result = data.get("result") or {}
    if not data.get("isSuccess", True) and not result:
        if not state.get("authExpired"):
            notify("allcpp 监控：登录已失效", "接口返回未登录，请更新 Cookie。")
            state["authExpired"] = True
            save_state(state)
        print("AUTH_EXPIRED 接口返回未登录")
        return

    state["authExpired"] = False

    items = result.get("list") or []
    if not items:
        print("EMPTY 没有申请记录")
        return

    alerts = []      # 需要提醒的
    summary = []     # 全部状态，仅打印
    for ed in items:
        ec = ed.get("eventCircle") or {}
        em = ed.get("eventMain") or {}
        name = ed.get("totalName") or ed.get("name") or ("活动%s" % em.get("id"))
        key = str(em.get("id") or name)
        st = status_text(ec)
        summary.append("%s：%s" % (name, st))

        if st == QUIET_STATUS:
            notified.pop(key, None)          # 回到审核中，清掉记录，方便下次再提醒
        elif notified.get(key) != st:        # 不是审核中，且这条状态还没提醒过
            alerts.append((key, name, st))

    print("当前状态：")
    for s in summary:
        print("  - " + s)

    hour = local_hour()
    night = hour < QUIET_HOUR_END      # 本地 0:00-7:59 静默

    # 1) 出结果了：任何时候都立刻提醒（同一条结果只提一次）
    if alerts:
        lines = []
        for key, name, st in alerts:
            lines.append("%s：**%s**" % (name, st))
            notified[key] = st
        rec = notify("allcpp 摊位审核有结果了", "\n\n".join(lines))
        state["notified"] = notified
        save_state(state)
        save_result({"time": now_str(), "mode": "alert",
                     "notify": rec, "detail": " / ".join(lines)})
        print("ALERT")
        for ln in lines:
            print("  " + ln)
        return

    if state.get("notified") != notified:
        state["notified"] = notified
        save_state(state)

    # 2) 还在审核中：白天每 10 分钟播报一次，夜间静默
    if night:
        print("QUIET_NIGHT 夜间不播报（本地 %d 点）" % hour)
        return

    rec = notify("allcpp 摊位状态", "\n\n".join(summary))
    save_result({"time": now_str(), "mode": "heartbeat",
                 "notify": rec, "detail": " / ".join(summary)})
    print("HEARTBEAT 已播报")


if __name__ == "__main__":
    main()
