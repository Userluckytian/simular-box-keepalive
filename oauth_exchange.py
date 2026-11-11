#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OAuth 兑换（供 .github/workflows/oauth-exchange.yml 在 GitHub runner 上调用）。

输入（环境变量）:
    RAW_CALLBACK   授权后浏览器跳转的完整 URL（含 code=…），或裸 code
    TEMP_VERIFIER  authorize.py 写入的 PKCE verifier
    ROTATE_TOKEN   写回 Secret 用的 PAT（需本仓库管理权限）
    GH_REPO        owner/repo（gh secret set 目标仓库）

行为:
    code → token 兑换（GitHub runner 网络，不经 Simular Box 出网代理）
    → 把 refresh_token 写入 Repository Secret SIMULAR_REFRESH_TOKEN
    → 删除一次性 Secret TEMP_VERIFIER

输出全部脱敏：token 值以 ::add-mask:: 注册，任何日志都不会出现明文。
退出码: 0 成功；2 配置缺失；1 兑换/写回失败。
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

TOKEN_EP = "https://mcp.box.simular.ai/oauth/token"
RESOURCE = "https://mcp.box.simular.ai/mcp"
CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
REDIRECT_URI = "http://localhost:51777/callback"
REDACT_KEYS = ("refresh_token", "access_token", "id_token")


def redact(text):
    out = text or ""
    for k in REDACT_KEYS:
        out = re.sub(r'("%s")[^,}]*' % k, r'\1: "***"', out)
        out = re.sub(r'%s=[^&\s]*' % k, '%s=***' % k, out)
    return out


def post(url, data):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")


def main():
    raw = os.environ.get("RAW_CALLBACK", "").strip()
    verifier = os.environ.get("TEMP_VERIFIER", "").strip()
    gh_token = os.environ.get("ROTATE_TOKEN", "").strip()
    gh_repo = os.environ.get("GH_REPO", "").strip()
    if not raw:
        print("缺少 RAW_CALLBACK（oauth-exchange 的 callback_url 输入）")
        return 2
    if not verifier:
        print("缺少 Secret TEMP_VERIFIER——先在任意装有 gh 的机器上运行 authorize.py")
        return 2

    if "code=" in raw:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
        code = q["code"][0]
    else:
        code = raw
    print("code 提取完成（长度 %d）" % len(code))

    st, body = post(TOKEN_EP, {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
        "resource": RESOURCE,
    })
    try:
        d = json.loads(body)
    except ValueError:
        d = {}
    rt = d.get("refresh_token", "")
    at = d.get("access_token", "")
    if st != 200 or not rt:
        print("兑换失败 HTTP %s，脱敏响应：%s" % (st, redact(body)[:500]))
        return 1
    print("::add-mask::" + rt)
    print("::add-mask::" + at)
    print("兑换成功（scope=%s expires_in=%s）"
          % (d.get("scope", "?"), d.get("expires_in", "?")))

    if not gh_token:
        print("兑换成功但缺少 Secret ROTATE_TOKEN，无法写回 SIMULAR_REFRESH_TOKEN")
        return 3
    env = os.environ.copy()
    env["GH_TOKEN"] = gh_token
    if gh_repo:
        env["GH_REPO"] = gh_repo
    r = subprocess.run(
        ["gh", "secret", "set", "SIMULAR_REFRESH_TOKEN", "--app", "actions", "--body", rt],
        env=env, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print("gh secret set 失败: %s %s"
              % ((r.stdout or "")[:200], (r.stderr or "")[:300]))
        return 1
    print("OK  SIMULAR_REFRESH_TOKEN 已写入")
    subprocess.run(["gh", "secret", "delete", "TEMP_VERIFIER", "--app", "actions"],
                   env=env, capture_output=True, text=True, timeout=60)
    print("OK  一次性 TEMP_VERIFIER 已清理")
    return 0


if __name__ == "__main__":
    sys.exit(main())
