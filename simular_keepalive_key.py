#!/usr/bin/env python3
"""Simular Box keepalive via Personal Key (static, no rotation, no PAT)."""
import json, os, sys, time, urllib.request

MCP = "https://mcp.box.simular.ai/mcp"

def log(m):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), m, flush=True)

def call(key, tool, args, timeout=90):
    p = {"jsonrpc":"2.0","id":int(time.time()),"method":"tools/call",
         "params":{"name":tool,"arguments":args}}
    req = urllib.request.Request(MCP, data=json.dumps(p).encode(), method="POST",
        headers={"Authorization":"Bearer "+key,
                 "Content-Type":"application/json",
                 "Accept":"application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        b = line[5:].strip()
        if not b:
            continue
        try:
            o = json.loads(b)
        except Exception:
            continue
        if o.get("id") and "result" in o:
            return o["result"]
        if o.get("id") and "error" in o:
            raise RuntimeError("MCP error: " + json.dumps(o["error"])[:300])
    o = json.loads(raw)
    if "result" in o:
        return o["result"]
    raise RuntimeError("bad response: " + raw[:300])

def data(res):
    sc = res.get("structuredContent")
    if isinstance(sc, dict):
        return sc
    for it in res.get("content") or []:
        if it.get("type") == "text":
            try:
                return json.loads(it["text"])
            except Exception:
                pass
    return {}

def main():
    key = (os.environ.get("SIMULAR_KEY") or "").strip()
    box = (os.environ.get("SIMULAR_BOX") or "").strip()
    if not key:
        log("ERR missing SIMULAR_KEY")
        return 2
    a = {"box": box} if box else {}
    st = data(call(key, "box_status", a))
    state = st.get("state")
    log("box=%s state=%s" % (st.get("box_id") or box, state))
    if state != "running":
        wk = data(call(key, "box_wake", dict(a, wait=True)))
        log("wake -> state=%s" % wk.get("state"))
        if wk.get("state") != "running":
            log("ERR still not running")
            return 1
        log("OK woke")
    else:
        log("OK signal sent")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("ERR " + str(e)[:300])
        sys.exit(1)
