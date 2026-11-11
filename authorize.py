#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Simular OAuth 授权链接，并把 PKCE verifier 写入仓库 Secret TEMP_VERIFIER。

用法（在任何装有 gh CLI 且已登录目标仓库的机器上运行）：
    GH_REPO=<owner>/<repo> python3 authorize.py

流程：
  1. 本脚本生成 PKCE verifier / challenge 与 state，打印授权链接，
     并把 verifier 写入仓库 Secret TEMP_VERIFIER（供 oauth-exchange 工作流兑换用）；
  2. 在浏览器打开链接，登录 Simular 并授权；
  3. 浏览器会跳到 http://localhost:51777/callback?code=…——打不开是预期
     （本机没有回调服务），把地址栏完整 URL 复制下来；
  4. 到仓库 Actions → oauth-exchange → Run workflow，把 URL 粘贴进
     callback_url 输入框运行。工作流会在 GitHub runner（干净网络）上完成
     code → token 兑换，并把 refresh token 写入 SIMULAR_REFRESH_TOKEN。

为什么兑换不在本机/盒子做：Simular Box 所在平台对 box 出网代理封禁
mcp.box.simular.ai 控制面（防自唤醒），兑换必须发生在盒子之外。
"""
import base64
import hashlib
import os
import secrets
import subprocess
import urllib.parse

API_BASE = "https://mcp.box.simular.ai"
AUTHORIZE_EP = API_BASE + "/cb/oauth/authorize"
CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
RESOURCE = API_BASE + "/mcp"
REDIRECT_URI = "http://localhost:51777/callback"


def main():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)

    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "boxes",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": RESOURCE,
    })
    url = AUTHORIZE_EP + "?" + q

    repo = os.environ.get("GH_REPO", "").strip()
    env = os.environ.copy()
    if repo:
        env["GH_REPO"] = repo
    r = subprocess.run(
        ["gh", "secret", "set", "TEMP_VERIFIER", "--app", "actions", "--body", verifier],
        env=env, capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        print("OK  PKCE verifier 已写入仓库 Secret TEMP_VERIFIER（repo=%s）" % (repo or "(gh 默认)"))
    else:
        print("!!  写入 TEMP_VERIFIER 失败：%s" % ((r.stderr or "").strip()[:300]))
        print("!!  手动兜底：把下面 VERIFIER 的值配置为仓库 Secret TEMP_VERIFIER 后再触发兑换")
        print("VERIFIER=%s" % verifier)

    print()
    print("第 1 步：在浏览器打开下面链接，登录 Simular 并点击授权：")
    print(url)
    print()
    print("第 2 步：授权后浏览器跳到 localhost:51777（打不开是预期），"
          "复制地址栏完整 URL")
    print("第 3 步：仓库 Actions → oauth-exchange → Run workflow，"
          "把 URL 粘贴进 callback_url 运行；refresh token 会自动写入 Secret")


if __name__ == "__main__":
    main()
