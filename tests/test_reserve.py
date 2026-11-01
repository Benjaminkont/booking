import argparse
import ast
import contextlib
import inspect
import io
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from datetime import datetime

import reserve
from reserve import BookingError, Booker, ROOT, SHANGHAI, classify_record, load_settings


def settings(**overrides):
    values = dict(date=None, start="19:00", end="22:00", dry_run=False, headless=True)
    values.update(overrides)
    return load_settings(ROOT / "config.py", argparse.Namespace(**values), datetime(2026, 10, 2, 14, tzinfo=SHANGHAI))


class ParameterTests(unittest.TestCase):
    def test_today_uses_shanghai_date(self):
        self.assertEqual(settings().date, "2026-10-02")

    def test_dry_run_cannot_submit(self):
        self.assertFalse(settings(dry_run=True).submit)

    def test_crossing_closed_period_is_rejected(self):
        with self.assertRaises(BookingError):
            settings(start="17:00", end="20:00")

    def test_past_start_is_rejected_without_rescheduling(self):
        with self.assertRaises(BookingError):
            settings(start="13:30", end="18:00")

    def test_invalid_step_is_rejected(self):
        with self.assertRaises(BookingError):
            settings(start="19:05", end="22:00")

    def test_short_booking_is_rejected(self):
        with self.assertRaises(BookingError):
            settings(start="19:00", end="19:50")

    def test_date_format_is_strict(self):
        with self.assertRaises(BookingError):
            settings(date="2026-10-3")


class RecordTests(unittest.TestCase):
    def test_exact_existing_reservation(self):
        self.assertEqual(classify_record("123 4F-A113 2026-10-02 19:00 ~ 22:00 未开始", settings()), ("existing", "4F-A113"))
    def test_signed_in_reservation_blocks_overlapping_booking(self):
        target = SimpleNamespace(
            date="2026-11-01",
            start="19:00",
            end="22:00",
            seats=("4F-A114",),
        )
        text = (
            "图书馆二楼 二楼C区 2F-C057 "
            "开始:2026-11-01 09:20 "
            "结束:2026-11-01 22:00 已签到"
        )
        status, _ = classify_record(text, target)
        self.assertEqual(status, "conflict")

    def test_overlap_on_another_seat_is_a_conflict(self):
        self.assertEqual(classify_record("123 4F-A110 2026-10-02 20:00 ~ 22:00 未开始", settings())[0], "conflict")

    def test_disjoint_booking_is_unrelated(self):
        self.assertEqual(classify_record("123 4F-A114 2026-10-02 13:30 ~ 18:00 已开始", settings())[0], "unrelated")

    def test_full_timestamp_format(self):
        self.assertEqual(classify_record("123 4F-A114 2026/10/02 19:00:00 ~ 2026/10/02 22:00:00 未开始", settings())[0], "existing")

    def test_actual_approved_receipt_is_existing(self):
        receipt = "图书馆四楼\n四楼A区\n4F-A114\n开始:2026-10-02 20:00\n结束:2026-10-02 22:00\n审核通过\n取消预约\n详细"
        self.assertEqual(classify_record(receipt, settings(start="20:00")), ("existing", "4F-A114"))

    def test_unknown_record_format_stops(self):
        with self.assertRaises(BookingError):
            classify_record("123 4F-A114 时间未知 未开始", settings())

    def test_unknown_status_stops(self):
        with self.assertRaises(BookingError):
            classify_record("123 4F-A114 2026-10-02 19:00 ~ 22:00 状态未知", settings())


class CustomControlTests(unittest.TestCase):
    def test_time_already_selected_does_not_reopen_dropdown(self):
        b = object.__new__(Booker)
        b.expect = Mock()
        field = Mock()
        field.input_value.return_value = "22:00"
        b.select_time(field, "22:00")
        field.click.assert_not_called()
        b.expect.return_value.to_have_value.assert_called_once_with("22:00")

    def test_checkbox_uses_visible_label_instead_of_covered_input(self):
        b = object.__new__(Booker)
        b.page, b.expect = Mock(), Mock()
        b.s = SimpleNamespace(action_delay=2.0)
        b.check_rate_limit = Mock()
        field = b.page.get_by_role.return_value
        field.is_checked.return_value = True
        b.set_checkbox("已结束", False)
        b.page.locator.assert_called_once_with(".el-checkbox")
        labels = b.page.locator.return_value
        labels.get_by_text.assert_called_once_with("已结束", exact=True)
        labels.get_by_text.return_value.click.assert_called_once()
        field.click.assert_not_called()
        b.expect.return_value.not_to_be_checked.assert_called_once()
        b.page.wait_for_timeout.assert_called_once_with(2000)


class PacingTests(unittest.TestCase):
    def booker(self, delay=2.0):
        b = object.__new__(Booker)
        b.s = SimpleNamespace(action_delay=delay, timeout=30000)
        b.page, b.expect = Mock(), Mock()
        b.check_rate_limit = Mock()
        return b

    def test_valid_delays(self):
        for value in (0, 0.5, 2, 60):
            with self.subTest(value=value):
                self.assertEqual(reserve.parse_action_delay(value), float(value))

    def test_invalid_delays(self):
        for value in (-1, 61, float("nan"), float("inf"), True, "2", None):
            with self.subTest(value=value), self.assertRaises(BookingError):
                reserve.parse_action_delay(value)

    def test_config_delay_is_loaded(self):
        with unittest.mock.patch.object(reserve, "parse_action_delay", return_value=3.5) as parse:
            self.assertEqual(settings().action_delay, 3.5)
            parse.assert_called_once()

    def test_action_waits_before_running(self):
        b = self.booker(2.5)
        events = []
        b.page.wait_for_timeout.side_effect = lambda ms: events.append(("wait", ms))
        action = Mock(side_effect=lambda: events.append(("action", None)))
        b.act(action)
        self.assertEqual(events, [("wait", 2500), ("action", None)])

    def test_zero_disables_extra_wait(self):
        b = self.booker(0)
        action = Mock()
        b.act(action)
        b.page.wait_for_timeout.assert_not_called()
        action.assert_called_once()

    def test_each_action_is_paced_and_keeps_arguments_and_result(self):
        b = self.booker(3)
        action = Mock(return_value="result")
        self.assertEqual(b.act(action, "value", exact=True), "result")
        b.act(action, "next")
        self.assertEqual(b.page.wait_for_timeout.call_count, 2)
        b.page.wait_for_timeout.assert_called_with(3000)
        self.assertEqual(action.call_args_list[0], unittest.mock.call("value", exact=True))

    def test_limit_warning_before_action_prevents_action(self):
        b = self.booker()
        b.check_rate_limit.side_effect = BookingError("操作过于频繁")
        action = Mock()
        with self.assertRaises(BookingError):
            b.act(action)
        action.assert_not_called()

    def test_limit_warning_after_action_stops_without_retry(self):
        b = self.booker()
        b.check_rate_limit.side_effect = [None, BookingError("操作过于频繁")]
        action = Mock()
        with self.assertRaises(BookingError):
            b.act(action)
        action.assert_called_once()

    def test_settle_still_waits_for_response_and_loading_mask(self):
        b = self.booker()
        b.settle()
        b.page.wait_for_load_state.assert_called_once_with("networkidle", timeout=30000)
        b.expect.return_value.to_have_count.assert_called_once_with(0)
        b.check_rate_limit.assert_called_once()

    def test_all_browser_mutations_use_pacing(self):
        tree = ast.parse(inspect.getsource(Booker))
        mutations = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute)
                     and node.func.attr in ("click", "fill", "press", "goto")]
        self.assertEqual(mutations, [], "浏览器修改操作必须通过 act() 留出间隔")


class FrequencyWarningTests(unittest.TestCase):
    def test_visible_frequency_warning_stops(self):
        for message in ("操作过于频繁，请稍后再试", "请求太频繁", "Too many requests"):
            with self.subTest(message=message):
                b = object.__new__(Booker)
                b.page = Mock()
                b.page.locator.return_value.all_inner_texts.return_value = [message]
                with self.assertRaises(BookingError):
                    b.check_rate_limit()

    def test_ordinary_message_does_not_stop(self):
        b = object.__new__(Booker)
        b.page = Mock()
        b.page.locator.return_value.all_inner_texts.return_value = ["登录成功"]
        b.check_rate_limit()


class FakePage:
    def goto(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, *args):
        pass

    def locator(self, *args):
        return self

    def all_inner_texts(self):
        return []


class FakeForm:
    def __init__(self, booker):
        self.booker = booker

    def get_by_role(self, role, name, **kwargs):
        self.action = name
        return self

    def click(self):
        self.booker.actions.append(self.action)


class FlowBooker(Booker):
    def __init__(self, dry_run=False, receipt=None):
        # 只影响 FakePage 的测试场景，不让用户真实配置的 SUBMIT 改变模拟测试。
        self.s = replace(settings(dry_run=dry_run), submit=not dry_run)
        self.page = FakePage()
        self.timeout_error = TimeoutError
        self.receipts = iter([None, receipt])
        self.prepared, self.actions = [], []
        self.available = {"4F-A113", "4F-A112"}

    def login(self):
        pass

    def active_record(self):
        return next(self.receipts)

    def open_area(self):
        pass

    def prepare(self, seat):
        self.prepared.append(seat)
        return FakeForm(self) if seat in self.available else None

    def snapshot(self, name):
        pass

    def settle(self):
        pass


class FlowTests(unittest.TestCase):
    def run_quietly(self, booker):
        with contextlib.redirect_stdout(io.StringIO()):
            booker.run()

    def test_dry_run_skips_unavailable_then_cancels(self):
        b = FlowBooker(dry_run=True)
        self.run_quietly(b)
        self.assertEqual(b.prepared, ["4F-A114", "4F-A113"])
        self.assertEqual(b.actions, ["取消"])

    def test_success_stops_after_one_submission(self):
        b = FlowBooker(receipt="4F-A113")
        self.run_quietly(b)
        self.assertEqual(b.prepared, ["4F-A114", "4F-A113"])
        self.assertEqual(b.actions, ["提交"])

    def test_ambiguous_result_does_not_try_the_next_seat(self):
        b = FlowBooker(receipt=None)
        with self.assertRaises(BookingError):
            self.run_quietly(b)
        self.assertEqual(b.prepared, ["4F-A114", "4F-A113"])
        self.assertEqual(b.actions, ["提交"])

    def test_existing_record_does_not_submit(self):
        b = FlowBooker()
        b.receipts = iter(["4F-A114"])
        self.run_quietly(b)
        self.assertEqual(b.prepared, [])
        self.assertEqual(b.actions, [])

    def test_frequency_warning_does_not_fall_back_to_next_seat(self):
        b = FlowBooker()
        b.page.all_inner_texts = lambda: ["操作过于频繁，座位预约冲突"]
        with self.assertRaises(BookingError):
            self.run_quietly(b)
        self.assertEqual(b.prepared, [])
        self.assertEqual(b.actions, [])


if __name__ == "__main__":
    unittest.main()
