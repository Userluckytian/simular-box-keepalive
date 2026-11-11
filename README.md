# Simular Box 保活（GitHub Actions 版）

把 [JoeNik/simular-box-keepalive](https://github.com/JoeNik/simular-box-keepalive)
原本「常开服务器 + systemd timer（每 300 秒）」的部署方式，改造为
**GitHub Actions 定时工作流**（参考 [j5tv9nfzhn-hue/qwenpaw](https://github.com/j5tv9nfzhn-hue/qwenpaw)
的 Actions 模式）。仓库本身不需要任何服务器：GitHub 定时调度 + ubuntu-latest runner
直接跑 Python 标准库脚本，为 Simular Box（box.simular.ai）持续发送 MCP 活动信号，
机器不在运行态时自动 `box_wake` 唤醒。

> ⚠️ 仅用于保活你自己名下、有权操作的 Simular Box。请勿用于其他目的。

## 为什么保活和授权都必须在 Actions 上做

实测（原仓库记录 + 本仓库复现）：Simular Box 无直连出网，唯一出口是本机
`127.0.0.1:3128` 代理，且该代理**封禁 `mcp.box.simular.ai` 控制面**（平台防自唤醒）。
所以：

- `box_status` / `box_wake` 活动信号只能从盒子之外发起 → Actions runner；
- OAuth 的 code → token 兑换同样会被盒子代理拦下（502 tunnel）→ 也放到 Actions
  （`oauth-exchange` 工作流），因此本仓库**自带授权/换绑能力**，不依赖原版脚本。

## 与原版的差异

| | 原版（JoeNik） | 本仓库 |
| --- | --- | --- |
| 运行位置 | 常开 Linux 服务器（systemd service + timer） | GitHub Actions（cron 每 15 分钟 + 手动触发） |
| refresh token 存放 | 服务器 `/etc/simular-keepalive.env`（0600） | Repository Secret，**轮换后自动写回** |
| 轮换处理 | 刷新后立刻写回 env 文件 | `gh secret set` 写回 Secret（需要 `ROTATE_TOKEN`） |
| 授权 | 脚本 authorize + localhost 回调服务器 | `authorize.py` 生成链接 + `oauth-exchange` 工作流兑换 |
| 依赖 | Python 标准库 | Python 标准库 + runner 预装的 gh CLI |

Simular 的 OAuth 是**单活跃轮换**：每次用 refresh token 换 access token 时，响应里
都会带一个新的 refresh token、旧的立即作废。原版脚本把新 token 写回服务器上的 env
文件；Actions 没有持久本地文件，所以必须写回 Secret——这就是需要 `ROTATE_TOKEN`
的原因。

## 部署步骤

1. **使用本仓库**（公开仓库 Actions 免费额度不限时）。

2. **配置 Repository Secrets**（Settings → Secrets and variables → Actions）：

   | Secret | 说明 |
   | --- | --- |
   | `SIMULAR_REFRESH_TOKEN` | Simular OAuth refresh token。首次获取见下方「授权」 |
   | `ROTATE_TOKEN` | 一个对**本仓库有管理权限**的 GitHub PAT（经典 token，勾选 `repo` scope），用于轮换后写回 Secret |
   | `SIMULAR_BOX_ID` | 可选。目标 box id（`cb-xxxxxxxx`）。留空 = 账户默认 box |

3. **授权获取 refresh token**（只需一次；换绑/续期时重跑同样三步）：

   ```bash
   # 在任意装有 gh CLI 且已登录的机器上（含保活目标盒子里也可以——
   # authorize.py 只调 api.github.com，不碰被封锁的控制面）：
   GH_REPO=<你的用户名>/simular-box-keepalive python3 authorize.py
   ```

   - 脚本会打印授权链接，并把 PKCE verifier 写入 Secret `TEMP_VERIFIER`；
   - 浏览器打开链接，登录 Simular 并授权；
   - 浏览器跳到 `localhost:51777`（**打不开是预期**），复制地址栏完整 URL；
   - 到 Actions → **oauth-exchange** → Run workflow，把 URL 粘贴进 `callback_url`
     运行。runner 上完成兑换并把 refresh token 写入 `SIMULAR_REFRESH_TOKEN`
     （全程不打印明文 token；`TEMP_VERIFIER` 用后即删）。

4. **手动触发一次验证**：Actions → simular-box-keepalive → Run workflow。
   预期日志：

   ```
   access token 获取成功（expires_in=3600s）
   refresh token 已轮换，写回 Secret SIMULAR_REFRESH_TOKEN …
   Secret 写回完成。
   MCP 已连接: …
   state=running box=cb-…
   ok（本次活动信号已发送）。
   ```

   看到以上输出即整条链路（刷新 → 轮换写回 → MCP 活动）全部打通。

## 调度说明

- cron `*/15 * * * *`：GitHub 定时任务可能有数分钟级延迟；平台大约在最后一次
  **外部** MCP 活动后 35 分钟休眠（box 内部活动不计入），15 分钟 + 延迟余量是安全的。
- 每 6 小时成功运行会提交一次 `.last-run` 时间戳（github-actions[bot]），
  防止 60 天无仓库活动导致定时任务被停用，也可作人工健康标记。
- 若要暂停：Actions 页面禁用工作流即可，无需改代码。

## 安全说明

- `SIMULAR_REFRESH_TOKEN` / `ROTATE_TOKEN` / `TEMP_VERIFIER` 只存 GitHub 加密的
  Secrets，日志中自动打码；不要写入代码或提交记录。
- `ROTATE_TOKEN` 建议专用、最小授权（经典 PAT 勾 `repo` 即可满足写 Secret 权限）；
  泄露时在 GitHub 设置中吊销并重置仓库 Secrets。
- refresh token 一旦因运行失败（如轮换写回中断）而失配，重跑「授权」三步即可恢复。
- 保活不等于备份：重要数据仍需 `box_save` / 快照独立备份。

## 致谢与来源

- 核心保活逻辑与协议实测结论：[JoeNik/simular-box-keepalive](https://github.com/JoeNik/simular-box-keepalive)（`simular_keepalive.py`）
- GitHub Actions 定时保活 + Secrets + `.last-run` 记录模式：
  [j5tv9nfzhn-hue/qwenpaw](https://github.com/j5tv9nfzhn-hue/qwenpaw)
