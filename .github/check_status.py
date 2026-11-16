#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
监控 www.allcpp.cn 活动摊位申请状态（对应页面 #position-status0 等）。
用法: python check_status.py
输出:
  - SAME        状态无变化
  - CHANGED ... 状态有变化（后面跟详细对比）
  - AUTH_EXPIRED 登录态失效，需要更新 cookie.txt
"""
import json
import os
import sys
import urllib.request
import urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(BASE, "cookie.txt")
STATE_FILE = os.path.join(BASE, "last_state.json")

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


def read_cookie():
    if not os.path.exists(COOKIE_FILE):
        print("AUTH_EXPIRED 缺少 cookie.txt")
        sys.exit(0)
    with open(COOKIE_FILE, encoding="utf-8") as f:
        return f.read().strip()


def status_text(ec):
    """和页面 getPositionStatus 一致的逻辑"""
    if not ec:
        return "未申请"
    enabled = ec.get("eventEnabled", -1)
    if enabled == 1 and ec.get("isReadyPublish"):
        return "预审通过 等待签约：%s摊" % ec.get("prePositionCount", 0)
    return STATUS_MAP.get(enabled, "")


def fetch(cookie):
    url = API + "?" + urllib.parse.urlencode(PARAMS)
    req = urllib.request.Request(url, headers={**HEADERS, "Cookie": cookie})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    return json.loads(raw)


def main():
    cookie = read_cookie()
    try:
        data = fetch(cookie)
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg or "login" in msg.lower():
            print("AUTH_EXPIRED", msg)
        else:
            print("ERROR", msg)
        return

    result = data.get("result") or {}
    if not data.get("isSuccess", True) and not result:
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
            "rawEnabled": ec.get("eventEnabled"),
        }

    prev = None
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = None

    if prev is None:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        print("INIT 已建立初始快照（首次运行，不视为变化）")
        for k in sorted(current, key=int):
            print("  - %s：%s" % (current[k]["name"], current[k]["status"]))
        return

    changed = []
    for k, v in current.items():
        old = prev.get(k)
        if not old or old.get("status") != v["status"]:
            changed.append("%s：%s → %s" % (v["name"], (old or {}).get("status", "(新)"), v["status"]))

    if changed:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        print("CHANGED")
        for c in changed:
            print("  " + c)
    else:
        print("SAME")


if __name__ == "__main__":
    main()
