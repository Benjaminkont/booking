# 四楼A区自动预约

在能访问学校内网的 macOS/Linux 电脑上运行。每次只预约一个座位，按 `config.py` 中的顺序选择，成功后停止。

## 日常使用入口

完整操作说明见 [USAGE.md](USAGE.md)，覆盖配置、定时演练、真实预约、停止、日志和常见问题。jige 上执行：

```bash
booking-config help  # 使用 Rich 美化打印使用说明；不读配置、不登录、不预约
booking-config       # 保留原用法：用 nano 编辑 config.py
```

`booking-config -h`、`booking-config --help` 同样打印完整说明。命令入口更新后，现有终端执行 `source ~/.bashrc` 或重新登录。也可绕过别名直接运行：

```bash
~/services/booking/.venv/bin/python -B \
  ~/services/booking/booking_config.py help
```

重新部署时，在已安装项目依赖的前提下，将下面的别名放入 `.bashrc`（替换旧的 booking-config 别名，不要指向 nano）：

```bash
alias booking-config='/home/karatani/services/booking/.venv/bin/python -B /home/karatani/services/booking/booking_config.py'
```

## 首次安装

需要 Python 3.9+ 和已安装的 Google Chrome。

```bash
cd /Users/quzhong/GitHub/Booking
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

如果没有 Chrome，在 `config.py` 中设置 `BROWSER_CHANNEL = ""`，再安装浏览器：

```bash
python -m playwright install chromium
```

### Ubuntu / jige

服务器部署目录是 `/home/karatani/services/booking`。虚拟环境需在服务器上创建，不能复制 macOS 的 `.venv`。已安装 Chrome 时可继续使用 `BROWSER_CHANNEL = "chrome"`，不需要额外安装 Chromium。

部署时可先从不含凭据的 `config.example.py` 初始化配置；填写 `USERNAME`、`PASSWORD`、`ACCOUNT_NAME`（网页显示姓名）、日期和时间。初始示例使用 `SUBMIT = False`，确认演练结果后才改为 `True`。含密码的本机配置不会未经明确授权上传到服务器。

服务器没有图形桌面时，运行时加 `--headless`（或在服务器的 `config.py` 中设置 `HEADLESS = True`）：

```bash
cd ~/services/booking
.venv/bin/python reserve.py --headless --dry-run
```

确认日期、时间等配置后，真实预约命令是：

```bash
.venv/bin/python reserve.py --headless
```

`config.py` 含账号密码，应保持权限 `600`。`DATE = "today"` 对应北京时间当天；如果开始时间已经过去，需要先修改配置，脚本不会自动改日期。直接执行 `reserve.py` 是一次运行后退出；下方的系统服务提供单次定时触发。独立观察模式尚未实现。

## 单次预约系统服务（jige）

系统服务开机启动并保持待命。`booking-run` 不再立即运行预约，而是锁定配置、注册一个任务，等待指定启动时间。完成或失败后都回到待命，不自动重复。重新连接服务器，或执行 `source ~/.bashrc` 加载新命令。

用 `booking-config` 编辑配置，区分座位使用时间和流程启动时间（下面仅是示例，不是已经确认的预约开放规则）：

```python
DATE = "2026-10-04"
START_TIME = "19:00"
END_TIME = "22:00"
RUN_BEFORE_HOURS = 6  # 默认提前 6 小时，可手动调整，允许 5.5 等正数
RUN_AT = ""          # 留空自动计算，本例为 2026-10-04 13:00:00
```

`RUN_AT` 留空时，启动时间为 `DATE + START_TIME - RUN_BEFORE_HOURS`（北京时间）。旧配置没有提前量参数时也默认 6 小时；允许正的有限数字，也可跨日计算。六小时只是可调整的默认假设，尚未实测确认网站开放规则。

想指定一个准确启动时间时，填写 `RUN_AT`，它优先于提前量，严格包含日期和秒：

```python
RUN_AT = "2026-10-04 14:00:00"  # 手动指定，忽略 RUN_BEFORE_HOURS
```

无论自动计算还是手动指定，启动时间都必须在未来且早于座位使用开始时间。如果自动计算的时间已经过去，会提示修改配置，不立即补跑，也不自动改约到明天。填写无效的 `RUN_AT` 会报错，不退回自动计算。这里的启动时间决定何时开始登录和预约流程，不保证此刻已点击提交；原有加载等待和操作间隔会继续生效。直接执行 `reserve.py` 仍是立即运行，不读取这两个调度参数。

```bash
booking-config         # 编辑服务器的 config.py
booking-run            # 注册一次任务；SUBMIT=True 时到点真实预约
booking-run --dry-run  # 注册一次演练任务，到点执行但不提交
booking-status         # 查看服务、任务和最近结果
booking-stop           # 撤销等待任务，或请求停止正在运行的任务
```

- `booking-run` 保存已求值的参数快照，包括固定的预约日期。`DATE = "today"` 指注册任务当天，不会在重启或跨日后变成另一天。修改配置后，先 `booking-stop`，再 `booking-run` 才会改变任务。
- 单次任务结束后必须再次手动 `booking-run` 才会运行下一次。不论成功、失败、提交结果不确定，均不自动重试。
- `booking-stop` 的待命状态会持久保存；重启不会重新激活。停止期间已成功提交的预约不会被取消；在运行途中停止时，请自行核查个人中心。
- 重启时只恢复尚未到点的等待任务。已错过时间或此前正在运行的任务回到待命，不补跑、不重放；中断结果需核查。
- 运行中的服务每秒检查一次。正常触发允许最多 5 秒调度延迟；超过此限视为错过，不补跑。这不构成精确到秒的提交承诺。
- 私有状态目录 `.booking-service/` 权限为 `700`，状态、日志、任务快照权限为 `600`。任务结束或撤销后清理对应快照，用户 `config.py` 保留不变。
- 最近运行日志位于 `/home/karatani/services/booking/.booking-service/last-run.log`，截图仍在 `output/playwright/`；这些文件只在本机保留。

运维命令（通常无需使用）：

```bash
systemctl --user status booking.service
journalctl --user -u booking.service -n 30 --no-pager
```

开机常驻使用用户级 systemd 和 `loginctl enable-linger karatani`；系统服务配置位于 `~/.config/systemd/user/booking.service`。普通退出 SSH 不影响等待任务。停用整个后台服务与 `booking-stop` 不同；后者让服务继续常驻但不预约。

2026-10-20 已将服务器项目整体迁至 `~/services/booking`，服务工作目录、启动命令和 `.bashrc` 中的 `booking-*` 别名均指向新目录。旧路径 `~/GitHub/booking` 仅保留指向新目录的兼容符号链接，保证旧终端别名及虚拟环境中的绝对路径入口继续有效；不要将该链接当作另一份项目。配置、状态、日志和虚拟环境随项目迁移，没有更改预约参数。迁移前的服务单元和 `.bashrc` 私有备份保存在新目录的 `.booking-service/before-services-move.Mzjx8s/`。

## 配置与运行

编辑 `config.py`，填写 `START_TIME`、`END_TIME` 和账号密码。例如：

```python
DATE = "today"
START_TIME = "19:00"
END_TIME = "22:00"
```

`DATE = "today"` 使用北京时间当天；也可以填写具体日期。未来日期是否开放，以学校实际表单为准。密码可以填在 `PASSWORD` 中；不填则读取 `BOOKING_PASSWORD` 环境变量，再不填会在运行时询问。不要将填好密码的配置提交到 Git；`config.py` 已被忽略。

首次建议演练，脚本会填到提交前、保存截图，然后取消申请窗口：

```bash
python reserve.py --dry-run
```

确定参数并设置 `SUBMIT = True` 后直接运行（立即真实提交预约，不经过定时服务）：

```bash
python reserve.py
```

也可设置 `SUBMIT = False` 让普通运行保持演练模式。`HEADLESS = True` 可后台运行。

### 操作速度

在 `config.py` 中修改操作间隔，单位为秒，默认 2 秒：

```python
ACTION_DELAY_SECONDS = 2.0
```

数值越大越慢。例如改为 `3.0`，每次点击、填写、键盘操作或切换页面前额外等待 3 秒。允许 0–60 的有限数字；`0` 关闭额外间隔，不建议首次运行时使用。旧配置未填写该项时也默认使用 2 秒。

原有的页面加载、元素可操作性、表单值和提交后记录检查仍然保留。`TIMEOUT_MS` 是一次响应或元素等待的最长时间，不是操作间隔。若看见“操作过于频繁”等提示，脚本停止，不自动重试或尝试下一个座位；若提示发生在提交附近，请先查看个人中心确认结果，再决定是否稍后重跑。操作间隔不能保证网站不限流，也不控制网页内部自行发出的请求。

仅检查参数：

```bash
python reserve.py --check-config
```

## 行为与限制

- 默认优先顺序是最左侧竖排从上到下：114 → 113 → 112 → 111。不会替换成列表之外的座位。
- 先查个人中心中的未开始/已开始预约。同一目标预约已经存在时直接结束；时间重叠的其他预约会使脚本停止。
- 指定时段内全程空闲的座位才会进入申请；半空闲和使用中的座位跳过。
- 时间必须满足 10 分钟步长、60–300 分钟时长，并完全位于单个开放时段内。
- 不自动修正已过去的时间，也不默许网页改变起止时间。表单值与配置不一致时停止。
- 提交后查询记录确认结果。响应超时不自动重发；结果无法确认时退出，需查看个人中心。
- 同一目录只允许一个脚本进程运行。其他电脑或手工同时预约仍可能产生竞争。
- 不自动签到、退座或取消已有预约。校方规定开始后 15 分钟未签到将取消并计违约，使用结束需退座。
- 不保存登录会话，每次新开独立浏览器并正常登录。登录失败、额外认证、规则变化或网页结构变化时停止。
- 截图位于 `output/playwright/`，含账号和预约信息，应仅在本机保留。

实现依据：[Playwright Python](https://playwright.dev/python/docs/library)、[定位元素](https://playwright.dev/python/docs/locators)。

## 已验证范围

2026-10-02：20 项自动化测试通过；在真实学校网页分别以 19:00–22:00、19:10–21:10 演练，均成功登录、查询预约记录、进入四楼A区、选择 `4F-A114` 并核对申请表单，随后取消申请。

用户填写配置后，真实提交并核实 `4F-A114` 的 2026-10-02 20:00–22:00 预约，个人中心状态为“审核通过”。根据实际运行修正了分页箭头定位及该状态的识别。再次以演练模式运行，脚本识别目标预约已存在并正常退出，没有再次提交。记录截图：`output/playwright/20261002-190817-902961/existing.png`。其他三个候选座位的真实提交、未来日期预约及座位冲突响应尚未现场验证。

操作速度改动：33 项自动化测试通过，语法和配置检查通过。在阻断网络的独立 Chrome 中使用本地模拟页面，默认 2 秒配置下填写操作耗时 2.05 秒、点击操作耗时 2.13 秒；模拟“操作过于频繁”提示后，下一次操作被阻止。本次未访问学校网站或提交新预约，真实网站的限流阈值仍未知。

Ubuntu 部署环境验证（jige）：Python 3.12 独立环境、Playwright 1.60.0 和已有 Chrome；依赖检查、语法检查及 33 项模拟测试通过。先部署无凭据示例配置；用户明确授权后，已将本机填写的 `config.py` 复制到 `/home/karatani/GitHub/booking/config.py`，权限为 `600`。无界面浏览器能打开学校登录页（HTTP 200），并验证账号登录成功，随后关闭，未选座或提交预约。实际配置的当天 20:00–22:00 在验证时已经过去，配置检查按预期阻止运行；下次预约前需修改日期或时间。服务器运行时加 `--headless`，没有启动定时任务或常驻服务。

2026-10-03 单次系统服务更新：本地与 jige 均通过 56 项测试（含真实 Python 子进程运行临时假预约脚本），语法和 systemd 单元检查通过。`booking.service` 已启用开机启动并运行，`Linger=yes`，当前为待命，`RUN_AT` 留空。配置、私有状态权限为 `600`，状态目录为 `700`。未注册生产预约任务，未真实预约，也未重启服务器；重启恢复行为以隔离状态测试验证。原配置备份位于 `.booking-service/config-before-service.py`，原 shell 配置备份为 `/home/karatani/.bashrc.booking-service-backup`。

2026-10-03 默认提前量更新：本地与 jige 均通过 66 项离线测试，覆盖默认提前 6 小时、修改提前量、小数小时、手动时间优先、跨日、配置冻结及过期拒绝。Python 语法检查通过；服务器配置除调度字段外与备份逐行一致，凭据和其他参数未改动。`booking.service` 仍为 enabled/active，`Linger=yes`，当前待命，没有注册生产任务或真实预约，也未重启服务或服务器。原配置备份为 `.booking-service/config-before-six-hour.py`（权限 `600`）。启动时间在每次 `booking-run` 注册时计算，后台服务仍只消费冻结后的绝对时间，无需重启生效。六小时默认值尚不代表网站开放规则已验证。

2026-10-20 目录迁移验证：在 `~/services/booking` 中通过 66 项离线测试、依赖检查和 Playwright 命令检查，代码摘要与迁移前一致，实际配置摘要未改变。服务已按新路径重启，工作目录和启动命令均为新目录；`active/running`、`enabled`、`Linger=yes`，任务仍为待命。新旧入口的状态查询都成功。项目目录及状态目录权限 `700`，真实配置权限 `600`。未注册生产预约任务，未访问学校网页或提交预约。

2026-10-20 帮助命令更新：新增 `USAGE.md` 和独立的 `booking_config.py`，`booking-config help`、`-h`、`--help` 使用 Rich 14.1.0 渲染同一份说明，无参数仍用 nano 编辑配置。本地 Python 3.9 与 jige Python 3.12 均通过 79 项离线测试及依赖检查，覆盖错误配置、缺失配置、缺失依赖、编辑器行为、其他工作目录及 80 列终端的完整安全提示。已在 jige 的干净 Bash 中从 `/tmp` 实际运行更新后的别名，验证 Rich Markdown 与颜色输出；配置、任务状态、快照和服务 PID 均未改变，服务继续运行且待命，没有真实预约或服务重启。旧 `.bashrc` 的私有备份位于 `.booking-service/bashrc-before-rich-help.5EM2sx`（权限 `600`）。
