#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
监控 www.allcpp.cn 活动摊位申请状态（对应页面 #position-status0 等）。
运行在 GitHub Actions 上，状态变化时通过 Server酱 推送到微信。

环境变量:
  ALLCPP_COOKIE   allcpp 的完整 Cookie 字符串
  WECOM_WEBHOOK   企业微信群机器人 webhook 完整地址（优先使用）
  SERVERCHAN_KEY  Server酱 SendKey（没配企业微信时用这个）
"""
import json
import os
import sys
import urllib.request
import urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "state.json")

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


def get_cookie():
    c = os.environ.get("ALLCPP_COOKIE", "").strip()
    if c:
        return c
    p = os.path.join(BASE, "cookie.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _notify_wecom(hook, title, desp):
    # 企业微信群机器人 markdown 消息，content 上限 4096 字节
    content = "**%s**\n%s" % (title, desp)
    data = json.dumps({"msgtype": "markdown",
                       "markdown": {"content": content}},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        hook, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")


def _notify_serverchan(key, title, desp):
    body = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
    url = "https://sctapi.ftqq.com/%s.send" % key
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")


def notify(title, desp):
    hook = os.environ.get("WECOM_WEBHOOK", "").strip()
    key = os.environ.get("SERVERCHAN_KEY", "").strip()
    if not hook and not key:
        print("[warn] 未配置 WECOM_WEBHOOK 或 SERVERCHAN_KEY，跳过推送")
        return
    try:
        if hook:
            print("[notify] wecom:", _notify_wecom(hook, title, desp)[:300])
        else:
            print("[notify] serverchan:", _notify_serverchan(key, title, desp)[:300])
    except Exception as e:
        print("[notify failed]", e)


def status_text(ec):
    """和页面 getPositionStatus 一致的逻辑"""
    if not ec:
        return "未申请"
    enabled = ec.get("eventEnabled", -1)
    if enabled == 1 and ec.get("isReadyPublish"):
        return "预审通过 等待签约：%s摊" % ec.get("prePositionCount", 0)
    return STATUS_MAP.get(enabled, "未知(%s)" % enabled)


def fetch(cookie):
    url = API + "?" + urllib.parse.urlencode(PARAMS)
    req = urllib.request.Request(url, headers={**HEADERS, "Cookie": cookie})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    return json.loads(raw)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and "items" in d:
                return d
        except Exception:
            pass
    return {"items": {}, "authExpired": False}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    print("[state] saved")


def main():
    cookie = get_cookie()
    state = load_state()
    prev_items = state.get("items") or {}

    if not cookie:
        if not state.get("authExpired"):
            notify("allcpp 监控：缺少 Cookie", "请到仓库 Secrets 配置 ALLCPP_COOKIE。")
            state["authExpired"] = True
            save_state(state)
        print("AUTH_EXPIRED 缺少 Cookie")
        return

    try:
        data = fetch(cookie)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg or "login" in msg.lower():
            if not state.get("authExpired"):
                notify("allcpp 监控：登录已失效",
                       "Cookie 已过期，请重新抓取并更新仓库 Secrets 中的 ALLCPP_COOKIE。")
                state["authExpired"] = True
                save_state(state)
            print("AUTH_EXPIRED", msg)
        else:
            print("ERROR", msg)
        return

    result = data.get("result") or {}
    if not data.get("isSuccess", True) and not result:
        if not state.get("authExpired"):
            notify("allcpp 监控：登录已失效",
                   "接口返回未登录/无权限，请更新 Cookie。")
            state["authExpired"] = True
            save_state(state)
        print("AUTH_EXPIRED 接口返回未登录/无权限")
        return

    items = result.get("list") or []
    current = {}
    for idx, ed in enumerate(items):
        ec = ed.get("eventCircle") or {}
        em = ed.get("eventMain") or {}
        name = ed.get("totalName") or ed.get("name") or ("活动%s" % em.get("id"))
        current[str(idx)] = {
            "name": name,
            "eventId": em.get("id"),
            "status": status_text(ec),
        }

    # 之前是登录失效状态，现在恢复了
    recovered = bool(state.get("authExpired"))
    state["authExpired"] = False

    if not prev_items:
        state["items"] = current
        save_state(state)
        print("INIT 已建立初始快照")
        for k in sorted(current, key=int):
            print("  - %s：%s" % (current[k]["name"], current[k]["status"]))
        if recovered:
            notify("allcpp 监控：已恢复", "Cookie 有效，监控正常运行。")
        return

    changed = []
    for k, v in current.items():
        old = prev_items.get(k)
        if not old:
            changed.append("%s：新增（当前 %s）" % (v["name"], v["status"]))
        elif old.get("status") != v["status"]:
            changed.append("%s：%s → %s" % (v["name"], old.get("status"), v["status"]))
    for k, old in prev_items.items():
        if k not in current:
            changed.append("%s：已从列表移除" % old.get("name"))

    if changed:
        state["items"] = current
        save_state(state)
        print("CHANGED")
        for c in changed:
            print("  " + c)
        notify("allcpp 摊位状态有变化", "\n\n".join(changed))
    else:
        if recovered:
            save_state(state)
            notify("allcpp 监控：已恢复", "Cookie 有效，监控正常运行。")
        print("SAME")


if __name__ == "__main__":
    main()
