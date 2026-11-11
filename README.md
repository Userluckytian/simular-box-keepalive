# Simular Box 保活（GitHub Actions 版）

把 [JoeNik/simular-box-keepalive](https://github.com/JoeNik/simular-box-keepalive)
原本「常开服务器 + systemd timer（每 300 秒）」的部署方式，改造为
**GitHub Actions 定时工作流**（参考 [j5tv9nfzhn-hue/qwenpaw](https://github.com/j5tv9nfzhn-hue/qwenpaw)
的 Actions 模式）。仓库本身不需要任何服务器：GitHub 定时调度 + ubuntu-latest runner
直接跑 Python 标准库脚本，为 Simular Box（box.simular.ai）持续发送 MCP 活动信号，
机器不在运行态时自动 `box_wake` 唤醒。

> ⚠️ 仅用于保活你自己名下、有权操作的 Simular Box。请勿用于其他目的。

## 与原版的差异

| | 原版（JoeNik） | 本仓库 |
| --- | --- | --- |
| 运行位置 | 常开 Linux 服务器（systemd service + timer） | GitHub Actions（cron 每 15 分钟 + 手动触发） |
| refresh token 存放 | 服务器 `/etc/simular-keepalive.env`（0600） | Repository Secret，**轮换后自动写回** |
| 轮换处理 | 刷新后立刻写回 env 文件 | `gh secret set` 写回 Secret（需要 `ROTATE_TOKEN`） |
| 依赖 | Python 标准库 | Python 标准库 + runner 预装的 gh CLI |

Simular 的 OAuth 是**单活跃轮换**：每次用 refresh token 换 access token 时，响应里
都会带一个新的 refresh token、旧的立即作废。原版脚本把新 token 写回服务器上的 env
文件；Actions 没有持久本地文件，所以必须写回 Secret——这就是需要 `ROTATE_TOKEN`
的原因。

## 部署步骤

1. **Fork 或使用本仓库**（公开仓库 Actions 免费额度不限时）。

2. **配置 Repository Secrets**（Settings → Secrets and variables → Actions）：

   | Secret | 说明 |
   | --- | --- |
   | `SIMULAR_REFRESH_TOKEN` | Simular OAuth refresh token。首次获取见下方「授权」 |
   | `ROTATE_TOKEN` | 一个对**本仓库有管理权限**的 GitHub PAT（经典 token，勾选 `repo` scope），用于轮换后写回 Secret |
   | `SIMULAR_BOX_ID` | 可选。目标 box id（`cb-xxxxxxxx`）。留空 = 账户默认 box |

3. **授权获取 refresh token**（只需一次）：在任意能开浏览器的机器上运行
   原版脚本的 authorize 子命令：

   ```bash
   python3 simular_keepalive.py authorize --port 51777 --box-id cb-xxxxxxxx
   # 浏览器打开打印的链接完成授权；回调落在 localhost:51777 即成功
   # 之后从 /etc/simular-keepalive.env 或 ~/simular-keepalive.env 读取
   # SIMULAR_REFRESH_TOKEN= 的值，填入仓库 Secret
   ```

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

- `SIMULAR_REFRESH_TOKEN` / `ROTATE_TOKEN` 只存 GitHub 加密的 Secrets，
  日志中自动打码；不要写入代码或提交记录。
- `ROTATE_TOKEN` 建议专用、最小授权（经典 PAT 勾 `repo` 即可满足写 Secret 权限）；
  泄露时在 GitHub 设置中吊销并重置仓库 Secrets。
- refresh token 一旦因运行失败（如轮换写回中断）而失配，重新跑一遍 authorize
  并更新 `SIMULAR_REFRESH_TOKEN` 即可恢复。
- 保活不等于备份：重要数据仍需 `box_save` / 快照独立备份。

## 致谢与来源

- 核心保活逻辑与协议实测结论：[JoeNik/simular-box-keepalive](https://github.com/JoeNik/simular-box-keepalive)（`simular_keepalive.py`）
- GitHub Actions 定时保活 + Secrets + `.last-run` 记录模式：
  [j5tv9nfzhn-hue/qwenpaw](https://github.com/j5tv9nfzhn-hue/qwenpaw)
