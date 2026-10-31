# Booking 使用方法

每次按优先顺序预约四楼 A 区的一个座位。系统服务负责等待启动时间；任务完成或失败后回到待命，不自动每天重复。

## 1. 项目在哪，常用命令是什么

jige 上的项目目录：`/home/karatani/services/booking`。需要能访问学校内网，已安装的项目依赖、Chrome 和用户级 systemd 服务可直接使用。

| 命令 | 作用 |
| --- | --- |
| `booking-config help` | 用 Rich 打印本说明；不读账号密码、不登录、不预约 |
| `booking-config` | 用 nano 编辑 `config.py`；保存本身不会注册任务 |
| `booking-run --dry-run` | 注册一次定时演练任务；到点执行，但不提交 |
| `booking-run` | 注册一次定时任务；是否真实提交由 `SUBMIT` 决定 |
| `booking-status` | 查看服务是否运行、任务状态、启动时间及最近结果 |
| `booking-stop` | 撤销等待任务，或请求停止正在运行的任务，随后待命 |

`booking-config -h` 和 `booking-config --help` 也可查看说明。更新命令入口后，现有 SSH 终端需执行 `source ~/.bashrc`，或重新登录。帮助只打印文档，不会启动编辑器。

## 2. 先配置，再演练，再预约

### 第一步：查看状态并编辑配置

```bash
booking-status
booking-config
```

在 nano 中，`Ctrl+O`、回车保存，`Ctrl+X` 退出。已有等待任务需要改配置时，先执行 `booking-stop` 撤销，再编辑并重新注册；只改文件不会改变已注册任务。

确认账号字段 `USERNAME`、`PASSWORD`、`ACCOUNT_NAME` 已填写，`ACCOUNT_NAME` 是网页显示的姓名。不要将真实密码写进文档或提交到 Git。后台任务不能交互询问密码。

下面仅展示需要调整的部分，不要用它覆盖整份配置；日期和时间必须按实际预约目标填写。

```python
DATE = "today"
START_TIME = "20:00"
END_TIME = "22:00"
RUN_BEFORE_HOURS = 6
RUN_AT = ""
ACTION_DELAY_SECONDS = 2.0
SUBMIT = False  # 首次先演练；准备真实预约时改为 True
```

`DATE = "today"` 是注册任务时的北京时间当天，也可填写具体 `YYYY-MM-DD` 日期。上例默认在当天 14:00 开始流程，使用座位的时段仍是 20:00–22:00。如果 14:00 已过，必须调整日期、提前量或手动启动时间，系统不会立即补跑或自动改约到明天。

### 第二步：注册一次演练

```bash
booking-run --dry-run
booking-status
```

应看到“等待执行”和计划启动时间。演练也是到点才执行，不是立即执行；`--dry-run` 即使配置中 `SUBMIT = True` 也不会提交。到点后查询并填写表单，核对后取消申请窗口；已有目标预约时可能直接结束。

再用 `booking-status` 确认回到“待命”，并结合最近结果、日志及截图检查演练结果。若要取消尚未执行的演练，用 `booking-stop`。

### 第三步：确认后注册真实预约

演练结束或撤销后，用 `booking-config` 确认日期、时间和座位优先顺序，设置 `SUBMIT = True`，并确保计划启动时间仍在未来，然后执行：

```bash
booking-run
booking-status
```

这只注册一次任务。退出 SSH 不影响等待，到点才启动登录及预约。成功后仍应在学校个人中心核查记录；脚本不负责自动签到或退座。

## 3. 座位时间和启动时间是两回事

| 参数 | 含义 |
| --- | --- |
| `DATE` | 使用座位的日期，不是后台服务启动日期 |
| `START_TIME` / `END_TIME` | 使用座位的起止时间，格式 `HH:MM` |
| `RUN_BEFORE_HOURS` | 提前多少小时启动流程，默认 `6`，可改为 `5`、`5.5` 等正的有限数字 |
| `RUN_AT` | 手动指定启动时间；留空自动计算，非空则优先于提前量 |

自动计算规则：使用日期的开始时间减去提前量，必要时跨到前一天。六小时只是可调的默认假设，尚未实测确认学校的预约开放规则。

想指定准确启动时间，把 `RUN_AT` 填成北京时间 `YYYY-MM-DD HH:MM:SS`，日期和秒都不能省略。例如希望某一天 13:50 启动，就填写那天的完整日期和 `13:50:00`。这里的格式是填写模板，不是可直接运行的日期值。

无论哪种模式，启动时间必须在未来且早于座位使用开始时间。填写错误的手动时间会报错，不悄悄退回自动模式。启动表示开始登录、加载页面和操作，不保证那一秒已经提交；页面等待和操作间隔仍生效。

## 4. 座位、速度和其他常用参数

| 参数 | 用途与边界 |
| --- | --- |
| `SEATS` | 候选优先顺序，默认 `4F-A114` → `4F-A113` → `4F-A112` → `4F-A111`；只预约其中一个 |
| `SUBMIT` | `True` 允许真实提交，`False` 仅演练；命令行 `--dry-run` 总能禁止提交 |
| `ACTION_DELAY_SECONDS` | 每次点击、填写、切换页面前额外等待的秒数，默认 `2.0`，范围 0–60；越大越慢 |
| `TIMEOUT_MS` | 单次等待响应或元素的最长毫秒数，不是操作速度，默认 `30000` |
| `HEADLESS` | 无界面模式；系统服务始终用无界面浏览器运行 |
| `BROWSER_CHANNEL` | 默认 `chrome`，使用已安装的 Chrome；空字符串需要已安装 Playwright Chromium |

预约时间按 10 分钟步长，默认时长 60–300 分钟，且必须完全位于一个开放时段内：07:00–12:00、13:30–18:00、19:00–22:00。网页规则变化时以校方实际规则为准，不要仅为绕过校验而修改规则参数。

脚本会先查现有预约：相同目标已存在则结束，时间冲突则停止。候选座位必须目标时段全程空闲；成功一个就结束。限流、提交超时或结果不确定时不自动重试；先在个人中心核查，再决定是否重新运行。

## 5. 待命、停止和开机启动

- `booking-run` 冻结当时的配置及日期。修改参数需要 `booking-stop` 后重新注册；已有任务时拒绝重复注册。
- 每次任务结束后都回到待命，下一次需要再次手动 `booking-run`。
- `booking-stop` 不关闭系统服务，不取消已成功提交的学校预约。运行中停止只是请求终止流程，需稍后检查状态并核查个人中心。
- 服务开机常驻，`booking-stop` 的待命状态也会保存；重启不会自行激活新预约。
- 重启只恢复尚未到点的等待任务。已经错过时间或之前正在执行的任务不重放；中断结果需自行核查。
- 正常调度允许最多 5 秒延迟，超过则视为错过，不补跑。这不是精确到秒的提交保证。

系统服务位置是 `~/.config/systemd/user/booking.service`，使用 `systemctl --user` 管理，不是 `sudo systemctl`。通常只需使用上面的 booking 命令。

## 6. 查看日志和排查问题

```bash
booking-status
tail -n 80 ~/services/booking/.booking-service/last-run.log
journalctl --user -u booking.service -n 30 --no-pager
```

`last-run.log` 在实际启动任务后才会产生，记录最近一次运行；尚未启动过时没有该文件是正常现象。截图在项目的 `output/playwright/` 下。

| 现象 | 先检查什么 |
| --- | --- |
| `booking-config help` 打开了编辑器 | 仍在用旧别名；退出编辑器，执行 `source ~/.bashrc` 或重新登录 |
| `booking-config` 或其他命令找不到 | 先加载 `.bashrc`；也可用下方完整 Python 命令 |
| 提示缺少 Rich | 在项目目录执行 `.venv/bin/python -m pip install -r requirements.txt` |
| 提示启动时间已过去 | 检查 `DATE`、开始时间、提前量及 `RUN_AT`；不自动补跑 |
| 已有等待或运行中的任务 | 查看 `booking-status`；确定替换时先 `booking-stop`，待停止后再注册 |
| 服务未运行 | 查看 `systemctl --user status booking.service` 和服务日志；确认原因后才手动启动服务 |

遇到“操作过于频繁”或无法确认提交结果时，先核查学校个人中心；稍后增大操作间隔，不要立刻反复重跑。

配置文件含账号密码，权限应为 `600`；私有状态目录 `.booking-service/` 应为 `700`。日志、截图和配置快照可能含预约或个人信息，不要直接公开。旧路径 `~/GitHub/booking` 只是兼容链接，项目实际在 `~/services/booking`，不要当成两份项目分别运行。

## 7. 不使用别名时，以及立即执行的区别

在 jige 上，即使当前目录不是项目目录，也可以直接打印帮助：

```bash
~/services/booking/.venv/bin/python -B \
  ~/services/booking/booking_config.py help
```

去掉末尾 `help` 就是编辑配置。本地电脑也可在项目目录执行 `.venv/bin/python booking_config.py help`。

下面是立即演练，不经过系统服务、不等待 `RUN_AT` 或提前量；它会真实登录并操作网页，但不提交。仅在你确实想立即检查网页流程时手动执行，不要和后台任务同时运行：

```bash
cd ~/services/booking
.venv/bin/python reserve.py --headless --dry-run
```

仅检查预约参数可用 `.venv/bin/python reserve.py --check-config`；它不验证系统服务的调度时间。直接运行 `reserve.py` 不加 `--dry-run` 且 `SUBMIT = True` 时会立即真实预约。

帮助格式参考：[Rich Markdown 官方文档](https://rich.readthedocs.io/en/stable/markdown.html)。学校开放时间、限流阈值和网页结构仍以实际平台为准；独立观察模式尚未实现。
