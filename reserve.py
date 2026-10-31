"""通过网页正常流程预约一个座位。用 --dry-run 验证而不提交。"""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import math
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SHANGHAI = ZoneInfo("Asia/Shanghai")


class BookingError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    base_url: str
    area_id: str
    area_name: str
    username: str
    password: str
    account_name: str
    date: str
    start: str
    end: str
    seats: tuple
    remark: str
    submit: bool
    headless: bool
    channel: str
    timeout: int
    action_delay: float

    @property
    def area_url(self):
        return f"{self.base_url}/#/ic/seatPredetermine/{self.area_id}"

    @property
    def records_url(self):
        return f"{self.base_url}/#/ic/userinfo"


def minutes(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise BookingError(f"时间必须是 HH:MM 格式：{value!r}")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def parse_action_delay(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BookingError("ACTION_DELAY_SECONDS 必须是 0–60 之间的数字（单位：秒）。")
    if not math.isfinite(value) or not 0 <= value <= 60:
        raise BookingError("ACTION_DELAY_SECONDS 必须是 0–60 之间的有限数字（单位：秒）。")
    return float(value)


def load_settings(path, args, now=None, config_module=None):
    module = config_module
    if module is None:
        spec = importlib.util.spec_from_file_location("booking_config", path)
        if spec is None or spec.loader is None:
            raise BookingError("无法读取配置文件。")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    now = now or datetime.now(SHANGHAI)
    chosen_date = args.date or module.DATE
    chosen_date = now.date().isoformat() if chosen_date == "today" else chosen_date
    try:
        if not isinstance(chosen_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", chosen_date):
            raise ValueError
        day = datetime.strptime(chosen_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise BookingError("DATE 必须是 today 或 YYYY-MM-DD。")
    start, end = args.start or module.START_TIME, args.end or module.END_TIME
    if not start or not end:
        raise BookingError("请先在 config.py 填写 START_TIME 和 END_TIME。")
    start_min, end_min = minutes(start), minutes(end)
    duration = end_min - start_min
    if not module.MIN_DURATION_MINUTES <= duration <= module.MAX_DURATION_MINUTES:
        raise BookingError("预约时长必须在配置的最短、最长时长之间（默认 60–300 分钟）。")
    if any(v % module.TIME_STEP_MINUTES for v in (start_min, end_min)):
        raise BookingError("起止时间必须符合时间步长（默认每 10 分钟）。")
    if not any(minutes(a) <= start_min < end_min <= minutes(b) for a, b in module.OPEN_PERIODS):
        raise BookingError("预约必须完全位于一个开放时段内，不能跨午休或晚间休息。")
    if day < now.date() or (day == now.date() and start_min <= now.hour * 60 + now.minute):
        raise BookingError("开始时间已经过去，请修改配置；脚本不会自动改约到其他时间。")
    seats = tuple(module.SEATS)
    if not seats or len(set(seats)) != len(seats) or any(not re.fullmatch(r"4F-A\d{3}", s) for s in seats):
        raise BookingError("SEATS 必须是不重复的四楼A区座位编号列表。")
    if len(module.REMARK) > 50:
        raise BookingError("申请说明不能超过 50 个字符。")
    base = module.BASE_URL.rstrip("/")
    if not re.fullmatch(r"https?://[^/]+", base):
        raise BookingError("BASE_URL 应只包含协议、主机和可选端口。")
    return Settings(
        base, str(module.AREA_ID), module.AREA_NAME,
        module.USERNAME or os.environ.get("BOOKING_USERNAME", ""),
        module.PASSWORD or os.environ.get("BOOKING_PASSWORD", ""), module.ACCOUNT_NAME,
        chosen_date, start, end, seats, module.REMARK,
        bool(module.SUBMIT) and not args.dry_run,
        bool(module.HEADLESS) or args.headless, module.BROWSER_CHANNEL, int(module.TIMEOUT_MS),
        parse_action_delay(getattr(module, "ACTION_DELAY_SECONDS", 2.0)),
    )


def row_interval(text):
    """从预约记录的可见文本识别日期和时间。无法识别时不猜测。"""
    dates = re.findall(r"\d{4}[-/]\d{2}[-/]\d{2}", text)
    times = re.findall(r"(?<!\d)(?:[01]\d|2[0-3]):[0-5]\d", text)
    if not dates or len(times) < 2:
        raise BookingError("预约记录格式无法识别，已停止，避免重复预约。")
    start = datetime.strptime(dates[0].replace("/", "-") + " " + times[0], "%Y-%m-%d %H:%M")
    end_date = dates[1] if len(dates) > 1 else dates[0]
    end = datetime.strptime(end_date.replace("/", "-") + " " + times[1], "%Y-%m-%d %H:%M")
    if end <= start:
        raise BookingError("预约记录的时间范围异常，已停止。")
    return start, end


def classify_record(text, settings):
    # 未开始/已开始是查询分类；实际有效预约行的状态显示为“审核通过”。
    if not re.search(r"未开始|已开始|审核通过", text):
        raise BookingError("未能确认预约记录状态，已停止，避免重复预约。")
    start, end = row_interval(text)
    wanted_start = datetime.strptime(f"{settings.date} {settings.start}", "%Y-%m-%d %H:%M")
    wanted_end = datetime.strptime(f"{settings.date} {settings.end}", "%Y-%m-%d %H:%M")
    seat = re.search(r"\b4F-A\d{3}\b", text)
    if start == wanted_start and end == wanted_end and seat and seat.group() in settings.seats:
        return "existing", seat.group()
    if start < wanted_end and wanted_start < end:
        return "conflict", seat.group() if seat else "其他预约对象"
    return "unrelated", ""


@contextmanager
def run_lock():
    # macOS/Linux 的文件锁由系统释放，即使脚本崩溃也不会留下无法解除的锁。
    import fcntl
    with (ROOT / ".booking.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BookingError("另一个预约脚本正在运行，请等待其结束。")
        yield


class Booker:
    def __init__(self, page, settings, expect, timeout_error):
        self.page, self.s, self.expect = page, settings, expect
        self.timeout_error = timeout_error
        self.artifacts = ROOT / "output" / "playwright" / datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S-%f")
        self.artifacts.mkdir(parents=True, mode=0o700)

    def check_rate_limit(self):
        messages = self.page.locator(
            ".el-message:visible, .el-notification:visible, .el-message-box:visible"
        ).all_inner_texts()
        if re.search(
            r"操作(?:过于|太)?频繁|请求(?:过于|太)?频繁|访问(?:过于|太)?频繁|请勿频繁操作|too many requests",
            " ".join(messages), re.IGNORECASE,
        ):
            raise BookingError("网站提示操作过于频繁，已停止，不自动重试；请稍后查看个人中心确认是否已有预约。")

    def act(self, action, *args, **kwargs):
        # 额外间隔只用于降速；不代替 Playwright 的可操作性与响应等待。
        self.check_rate_limit()
        if self.s.action_delay:
            self.page.wait_for_timeout(self.s.action_delay * 1000)
        result = action(*args, **kwargs)
        self.check_rate_limit()
        return result

    def snapshot(self, name):
        # 登录表单可能含凭据，不保存其截图。
        if self.page.locator('input[type="password"]:visible').count():
            return
        path = self.artifacts / f"{name}.png"
        self.page.screenshot(path=str(path), full_page=True)
        path.chmod(0o600)
        print(f"截图：{path}")

    def settle(self):
        # 点击事件之后给 Vue 一个渲染周期，再等请求完成；失败时停止而非猜测。
        self.page.wait_for_timeout(250)
        self.page.wait_for_load_state("networkidle", timeout=self.s.timeout)
        self.expect(self.page.locator(".el-loading-mask:visible")).to_have_count(0)
        self.check_rate_limit()

    def login(self):
        account = self.page.get_by_role("button", name=re.compile(r"^" + re.escape(self.s.account_name) + r"(?:\s|$)"))
        if account.is_visible():
            return
        # 新浏览器初次打开时学校配置尚未提供占位文字，使用已核实的结构定位。
        username = self.page.locator(".loginName input:visible")
        password_field = self.page.locator('input[type="password"]:visible')
        self.expect(username).to_have_count(1)
        self.expect(password_field).to_have_count(1)
        self.expect(username).to_be_visible()
        if not self.s.username:
            raise BookingError("请在 config.py 中填写 USERNAME。")
        password = self.s.password or getpass.getpass("学校账号密码（输入不回显）：")
        if not password:
            raise BookingError("密码为空。")
        self.act(username.fill, self.s.username)
        self.act(password_field.fill, password)
        self.act(self.page.get_by_role("button", name="登 录", exact=True).click)
        try:
            self.expect(account).to_be_visible()
        except (self.timeout_error, AssertionError):
            raise BookingError("登录未成功或账号名称不匹配；请检查账号、密码、ACCOUNT_NAME 和页面提示。")
        self.settle()
        print("登录成功。")

    def active_record(self):
        self.act(self.page.goto, self.s.records_url, wait_until="networkidle")
        self.expect(self.page.get_by_role("heading", name="个人中心", exact=True)).to_be_visible()
        for label in ("开始日期", "结束日期"):
            field = self.page.get_by_placeholder(label, exact=True)
            self.act(field.fill, self.s.date)
            self.act(field.press, "Tab")
            self.expect(field).to_have_value(self.s.date)
        for label in ("未开始", "已开始"):
            self.set_checkbox(label, True)
        for label in ("已违约", "已结束"):
            self.set_checkbox(label, False)
        self.act(self.page.get_by_role("button", name="搜索", exact=True).click)
        self.settle()
        for _ in range(100):
            # 页面还有日期选择器的表格，只读取预约表正文，不读取日历行。
            rows = self.page.locator(".el-table__body-wrapper").get_by_role("row").all()
            data = [r.inner_text() for r in rows if r.get_by_role("cell").count()]
            if not data and not self.page.get_by_text("暂无数据", exact=True).is_visible():
                raise BookingError("预约记录尚未可靠加载，已停止。")
            for text in data:
                status, seat = classify_record(text, self.s)
                if status == "existing":
                    return seat
                if status == "conflict":
                    raise BookingError(f"目标时间内已有预约（{seat}），请先自行处理；脚本不会取消已有预约。")
            # 日历也有同名的右箭头；使用实际分页按钮的 class 排除日历。
            next_page = self.page.locator("button.btn-next")
            if not next_page.count() or not next_page.is_enabled():
                return None
            self.act(next_page.click)
            self.settle()
        raise BookingError("预约记录页数超过检查上限，已停止。")

    def set_checkbox(self, label, checked):
        field = self.page.get_by_role("checkbox", name=label, exact=True)
        if field.is_checked() != checked:
            # Element UI 的原生 input 被文字标签覆盖，点击可见标签正常切换。
            self.act(self.page.get_by_text(label, exact=True).click)
        if checked:
            self.expect(field).to_be_checked()
        else:
            self.expect(field).not_to_be_checked()

    def select_time(self, field, value):
        if field.input_value() == value:
            self.expect(field).to_have_value(value)
            return
        self.act(field.click)
        owner = field.locator("xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' el-select ')][1]")
        dropdown = owner.locator(".el-select-dropdown")
        if not dropdown.count():
            # Element UI 展开时会把菜单移到 body；等唯一可见菜单挂载后再操作。
            dropdown = self.page.locator(".el-select-dropdown:visible")
        self.expect(dropdown).to_have_count(1)
        self.expect(dropdown).to_be_visible()
        options = dropdown.locator(".el-select-dropdown__item").filter(
            has_text=re.compile(r"^" + re.escape(value) + r"$")
        )
        self.expect(options).to_have_count(1)
        if "is-disabled" in (options.get_attribute("class") or ""):
            raise BookingError(f"时间 {value} 在当前表单中不可选。")
        self.act(options.click)
        self.expect(field).to_have_value(value)
        self.settle()

    def open_area(self):
        self.act(self.page.goto, self.s.area_url, wait_until="networkidle")
        self.expect(self.page.get_by_role("heading", name=self.s.area_name, exact=True)).to_be_visible()
        field = self.page.get_by_placeholder("请选择", exact=True)
        self.expect(field).to_have_count(1)
        self.act(field.fill, self.s.date)
        self.act(field.press, "Enter")
        self.expect(field).to_have_value(self.s.date)
        self.settle()
        self.select_time(self.page.get_by_placeholder("请选择开始时间", exact=True), self.s.start)
        self.select_time(self.page.get_by_placeholder("请选择结束时间", exact=True), self.s.end)
        self.expect(self.page.get_by_placeholder("请选择开始时间", exact=True)).to_have_value(self.s.start)

    def prepare(self, seat):
        target = self.page.get_by_title(seat, exact=True)
        self.expect(target).to_have_count(1)
        self.expect(target).to_be_visible()
        # 半空闲不能满足整个指定时段；不改约到部分时间。
        if "green" not in (target.get_attribute("class") or "").split():
            print(f"跳过 {seat}：目标时段不是全程空闲。")
            return None
        self.act(target.click)
        notice = self.page.get_by_role("dialog", name="预约须知", exact=True)
        form = self.page.get_by_role("dialog", name="申请预约", exact=True)
        self.page.wait_for_function("""() => Array.from(document.querySelectorAll('[role=dialog]'))
            .some(e => e.getBoundingClientRect().width > 0 && /预约须知|申请预约/.test(e.innerText))""")
        if notice.is_visible():
            self.act(notice.get_by_role("button", name="确认", exact=True).click)
        self.expect(form).to_be_visible()
        self.expect(form.get_by_text(self.s.username, exact=True)).to_be_visible()
        self.expect(form.get_by_text(self.s.date, exact=True)).to_be_visible()
        if not re.search(r"\b" + re.escape(seat) + r"\b", form.inner_text()):
            raise BookingError("表单中的座位编号与目标不一致。")
        fields = form.get_by_placeholder("请选择", exact=True)
        self.expect(fields).to_have_count(2)
        self.select_time(fields.nth(0), self.s.start)
        self.select_time(fields.nth(1), self.s.end)
        self.expect(fields.nth(0)).to_have_value(self.s.start)
        self.expect(fields.nth(1)).to_have_value(self.s.end)
        self.act(form.get_by_placeholder("请输入相关备注", exact=True).fill, self.s.remark)
        return form

    def run(self):
        self.act(self.page.goto, self.s.area_url, wait_until="networkidle")
        self.login()
        existing = self.active_record()
        if existing:
            print(f"目标预约已存在：{existing}，不重复提交。")
            self.snapshot("existing")
            return
        for seat in self.s.seats:
            self.open_area()
            form = self.prepare(seat)
            if form is None:
                continue
            self.snapshot(f"prepared-{seat}")
            print(f"已核对：{seat}，{self.s.date} {self.s.start}–{self.s.end}")
            if not self.s.submit:
                self.act(form.get_by_role("button", name="取消", exact=True).click)
                print("演练完成：未提交预约。")
                return
            # 一次点击后只检查结果，不对超时进行自动重发。
            error_text = ""
            try:
                self.act(form.get_by_role("button", name="提交", exact=True).click)
                self.page.wait_for_timeout(500)
                self.settle()
                messages = self.page.locator(".el-message:visible, .el-notification:visible")
                error_text = " ".join(messages.all_inner_texts())
            except self.timeout_error:
                print("提交响应超时，先查询预约记录，不重复提交。")
            receipt = self.active_record()
            if receipt:
                self.snapshot("confirmed")
                print(f"预约成功并已核实记录：{receipt}，{self.s.date} {self.s.start}–{self.s.end}")
                return
            if re.search(r"座位.*(?:已被预约|被占用)|座位预约冲突", error_text):
                print(f"{seat} 提示座位冲突，尝试下一个。")
                continue
            raise BookingError("提交后未找到预约记录，已停止，避免重复预约。" + (f" 页面提示：{error_text}" if error_text else ""))
        raise BookingError("配置的所有候选座位在指定时段均不可用。")


def main():
    parser = argparse.ArgumentParser(description="四楼A区优先选座预约；参数集中在 config.py。")
    parser.add_argument("--config", type=Path, default=ROOT / "config.py", help="其他配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="填写表单并截图，但不提交")
    parser.add_argument("--check-config", action="store_true", help="仅校验配置，不启动浏览器")
    parser.add_argument("--headless", action="store_true", help="后台运行浏览器")
    parser.add_argument("--date", help="临时覆盖 DATE")
    parser.add_argument("--start", help="临时覆盖 START_TIME")
    parser.add_argument("--end", help="临时覆盖 END_TIME")
    args = parser.parse_args()
    try:
        settings = load_settings(args.config.resolve(), args)
        print(f"计划：{settings.area_name}，{settings.date} {settings.start}–{settings.end}")
        print("顺序：" + " → ".join(settings.seats))
        print("模式：" + ("真实提交" if settings.submit else "演练，不提交"))
        print(f"操作间隔：{settings.action_delay:g} 秒（另行等待页面响应）")
        if args.check_config:
            print("配置校验通过。")
            return 0
        try:
            from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout
        except ImportError:
            raise BookingError("缺少依赖，请运行 python -m pip install -r requirements.txt。")
        with run_lock(), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=settings.headless, channel=settings.channel or None)
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, timezone_id="Asia/Shanghai")
            page = context.new_page()
            page.set_default_timeout(settings.timeout)
            page.set_default_navigation_timeout(settings.timeout)
            expect.set_options(timeout=settings.timeout)
            booker = Booker(page, settings, expect, PlaywrightTimeout)
            try:
                booker.run()
            except Exception:
                try:
                    booker.snapshot("error")
                except Exception:
                    pass
                raise
            finally:
                context.close()
                browser.close()
        return 0
    except KeyboardInterrupt:
        print("已中止。若中止发生在提交之后，请检查个人中心的预约记录。", file=sys.stderr)
        return 130
    except Exception as exc:
        # 不打印配置对象、请求报文或完整堆栈，避免把凭据写入日志。
        print(f"停止：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
