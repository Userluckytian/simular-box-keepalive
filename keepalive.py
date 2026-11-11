#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simular Box 保活（GitHub Actions 版）
====================================
改写自 JoeNik/simular-box-keepalive 的 simular_keepalive.py。
原方案依赖一台常开服务器（systemd service + timer 每 300 秒触发）；
本版本去掉 systemd / authorize / install 子命令，适配 GitHub Actions 定时运行，
并增加「refresh token 轮换写回 Secret」环节（Actions 无法持久本地文件）。

单次运行流程
------------
  1. 从环境变量读 SIMULAR_REFRESH_TOKEN（对应 Repository Secret）；
  2. 用它向 Simular OAuth 换新 access token。注意：Simular 是单活跃轮换——
     每次刷新返回新的 refresh token，旧的立即作废；
  3. 若发生轮换，用 ROTATE_TOKEN（需 repo 管理权限的 PAT）通过 gh CLI 把新
     refresh token 写回同名 Secret SIMULAR_REFRESH_TOKEN；
  4. 通过 MCP 控制通道调 box_status 发送平台可见的活动信号；
  5. state != running 时自动调 box_wake 唤醒。

依赖：Python 3.8+ 标准库；gh CLI（ubuntu-latest runner 预装）。

环境变量
--------
  SIMULAR_REFRESH_TOKEN  必填。当前有效的 refresh token。
  ROTATE_TOKEN           必填（发生轮换时使用）。PAT，用于写回 Secret。
  SIMULAR_BOX_ID         可选。目标 box id（形如 cb-xxxxxxxx）。留空 = 账户默认 box。
  SIMULAR_MCP_URL        可选。默认 https://mcp.box.simular.ai/mcp
  GH_REPO                由 workflow 传入（owner/repo），gh secret set 用。

退出码：0 成功；2 配置缺失；3 refresh token 失效（需重新授权）；1 其他错误。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://mcp.box.simular.ai"
TOKEN_EP = API_BASE + "/oauth/token"
RESOURCE = API_BASE + "/mcp"
CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
DEFAULT_MCP_URL = API_BASE + "/mcp"


def log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- HTTP 基础

def http_req(url, data=None, headers=None, timeout=30):
    """返回 (status, headers_dict, body_str)。data 为已编码 bytes。"""
    req = urllib.request.Request(url, data=data,
                                 method="POST" if data is not None else "GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), (e.read() or b"").decode("utf-8", "replace")


def parse_mcp_body(headers, body, want_id):
    """MCP 响应可能是 application/json 或 SSE，取出 want_id 对应的那条 JSON。"""
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "")
    candidates = []
    if "text/event-stream" in ctype or body.lstrip().startswith("event:"):
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                candidates.append(line[5:].strip())
    else:
        candidates.append(body)
    for raw in candidates:
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if obj.get("id") == want_id or want_id is None:
            return obj
    return None


def mcp_post(url, token, session, payload, timeout=30):
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    st, hs, body = http_req(url, json.dumps(payload).encode(), headers, timeout)
    return st, hs, body


class McpClient(object):
    def __init__(self, mcp_url, token):
        self.url = mcp_url
        self.token = token
        self.session = None
        self.next_id = 1

    def _rpc(self, method, params=None, notify=False):
        i = self.next_id
        self.next_id += 1
        payload = {"jsonrpc": "2.0", "id": i, "method": method}
        if params is not None:
            payload["params"] = params
        st, hs, body = mcp_post(self.url, self.token, self.session, payload)
        if st == 401:
            raise RuntimeError("MCP 401 invalid_token（access token 无效/过期）")
        if st not in (200, 202):
            raise RuntimeError("MCP HTTP %s: %s" % (st, body[:200]))
        if notify:
            return None
        obj = parse_mcp_body(hs, body, i)
        if obj is None:
            raise RuntimeError("MCP 响应解析失败: %s" % body[:200])
        if "error" in obj:
            raise RuntimeError("MCP error: %s" % json.dumps(obj["error"])[:200])
        return obj["result"]

    def connect(self):
        payload = {
            "jsonrpc": "2.0", "id": self.next_id, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "simular-keepalive-actions", "version": "1.0"},
            },
        }
        i = self.next_id
        self.next_id += 1
        st, hs, body = mcp_post(self.url, self.token, None, payload)
        if st != 200:
            raise RuntimeError("initialize HTTP %s: %s" % (st, body[:200]))
        obj = parse_mcp_body(hs, body, i)
        if obj is None or "result" not in obj:
            raise RuntimeError("initialize 解析失败: %s" % body[:200])
        sid = None
        for k, v in hs.items():
            if k.lower() == "mcp-session-id":
                sid = v
                break
        if not sid:
            raise RuntimeError("响应里没有 Mcp-Session-Id")
        self.session = sid
        self._rpc("notifications/initialized", notify=True)
        return obj["result"].get("serverInfo", {})

    def call_tool(self, name, arguments=None):
        r = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if r.get("isError"):
            raise RuntimeError("tool %s 失败: %s" % (name, json.dumps(r.get("content"))[:200]))
        if isinstance(r.get("structuredContent"), dict):
            return r["structuredContent"]
        for c in r.get("content") or []:
            if c.get("type") == "text":
                try:
                    return json.loads(c["text"])
                except ValueError:
                    return {"text": c["text"]}
        return {}


# ---------------------------------------------------------------- token 管理

def refresh_access_token(refresh_token):
    """用 refresh token 换新 access token。返回 (access_token, expires_in, 新refresh_token或None)。

    注意必须带 resource=RESOURCE（RFC 8707），否则签发的 access token 无 audience，
    调 MCP 会 401 invalid_token。
    """
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
        "resource": RESOURCE,
    }).encode()
    st, _, resp = http_req(TOKEN_EP, body, {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        d = json.loads(resp)
    except ValueError:
        raise RuntimeError("token endpoint 返回异常 HTTP %s: %s" % (st, resp[:200]))
    if st != 200 or "access_token" not in d:
        raise RuntimeError("refresh 失效 HTTP %s: %s（refresh token 已轮换/作废，需重新 authorize）"
                           % (st, json.dumps(d)[:200]))
    return d["access_token"], int(d.get("expires_in", 3600)), d.get("refresh_token")


def rotate_back(new_rt):
    """把轮换出的新 refresh token 写回 Repository Secret（gh secret set）。"""
    gh_token = os.environ.get("ROTATE_TOKEN", "").strip()
    gh_repo = os.environ.get("GH_REPO", "").strip()
    if not gh_token:
        raise RuntimeError("发生 refresh token 轮换，但未配置 ROTATE_TOKEN——"
                           "本次写回失败，下一次运行必然 401。请在仓库 Secrets 配置 ROTATE_TOKEN。")
    if not gh_repo:
        raise RuntimeError("缺少 GH_REPO（workflow 应传入 github.repository）")
    env = os.environ.copy()
    env["GH_TOKEN"] = gh_token
    env["GH_REPO"] = gh_repo
    log("refresh token 已轮换，写回 Secret SIMULAR_REFRESH_TOKEN（repo=%s）…" % gh_repo)
    r = subprocess.run(
        ["gh", "secret", "set", "SIMULAR_REFRESH_TOKEN",
         "--app", "actions", "--body", new_rt],
        env=env, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("gh secret set 失败: %s %s"
                           % ((r.stdout or "")[:200], (r.stderr or "")[:300]))
    log("Secret 写回完成。")


# ---------------------------------------------------------------- 主流程

def main():
    rt = os.environ.get("SIMULAR_REFRESH_TOKEN", "").strip()
    if not rt:
        log("错误: 未配置 Secret SIMULAR_REFRESH_TOKEN。"
            "先在任意机器跑原版 authorize 获取 refresh token，再写入仓库 Secrets。")
        return 2
    box_args = {}
    if os.environ.get("SIMULAR_BOX_ID", "").strip():
        box_args = {"box": os.environ["SIMULAR_BOX_ID"].strip()}

    try:
        at, expires_in, new_rt = refresh_access_token(rt)
    except RuntimeError as e:
        msg = str(e)
        log("错误: %s" % msg)
        return 3 if "refresh" in msg.lower() or "refresh" in msg else 1
    log("access token 获取成功（expires_in=%ss）" % expires_in)

    if new_rt and new_rt != rt:
        try:
            rotate_back(new_rt)
        except RuntimeError as e:
            log("错误: %s" % e)
            return 1
    else:
        log("本次刷新未发生轮换（endpoint 未返回新 refresh token）。")

    mcp_url = os.environ.get("SIMULAR_MCP_URL", "").strip() or DEFAULT_MCP_URL
    cli = McpClient(mcp_url, at)
    try:
        info = cli.connect()
        log("MCP 已连接: %s %s" % (info.get("name"), info.get("version")))
        m = cli.call_tool("box_status", box_args)
        state = m.get("state")
        bid = m.get("box_id") or os.environ.get("SIMULAR_BOX_ID") or "(default)"
        log("state=%s box=%s" % (state, bid))
        if state != "running":
            log("机器不在运行态，调 box_wake 唤醒 …")
            w = cli.call_tool("box_wake", box_args)
            log("wake 结果: %s" % json.dumps(w, ensure_ascii=False)[:200])
            m2 = cli.call_tool("box_status", box_args)
            log("唤醒后 state=%s" % m2.get("state"))
            log("本次保活完成（含唤醒）。")
        else:
            log("ok（本次活动信号已发送）。")
        return 0
    except RuntimeError as e:
        log("错误: %s" % e)
        return 1
    except urllib.error.URLError as e:
        log("网络错误: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
