"""无凭据配置示例；复制为 config.py 后填写账号、显示姓名、日期和时间。"""

BASE_URL = "http://10.20.15.27"
AREA_ID = "100494721"
AREA_NAME = "四楼A区"
USERNAME = ""
PASSWORD = ""
ACCOUNT_NAME = ""

DATE = "today"
START_TIME = ""
END_TIME = ""
SEATS = ["4F-A114", "4F-A113", "4F-A112", "4F-A111"]
REMARK = ""

SUBMIT = False  # 初次配置先演练；确认后自行改为 True。
HEADLESS = True  # Ubuntu 服务器无界面运行。
BROWSER_CHANNEL = "chrome"
TIMEOUT_MS = 30000
ACTION_DELAY_SECONDS = 2.0
RUN_BEFORE_HOURS = 6  # 默认在座位使用开始前 6 小时启动；可改为 5、5.5 等正数。
RUN_AT = ""  # 留空按提前量计算；填写北京时间 YYYY-MM-DD HH:MM:SS 时优先使用。

OPEN_PERIODS = [("07:00", "12:00"), ("13:30", "18:00"), ("19:00", "22:00")]
MIN_DURATION_MINUTES = 60
MAX_DURATION_MINUTES = 300
TIME_STEP_MINUTES = 10
