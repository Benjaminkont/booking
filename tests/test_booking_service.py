import stat
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from booking_service import Controller, Store, parse_run_at
from reserve import BookingError, SHANGHAI


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 3, 12, tzinfo=SHANGHAI)
        self.run_at = self.now + timedelta(hours=2)
        self.config = self.root / "config.py"
        source = (Path(__file__).resolve().parents[1] / "config.example.py").read_text()
        self.config.write_text(source + '\nUSERNAME="fake"\nPASSWORD="fake-password"\nACCOUNT_NAME="fake"\nSTART_TIME="19:00"\nEND_TIME="22:00"\nSUBMIT=True\nRUN_AT="2026-10-03 14:00:00"\n')
        self.process = Mock(pid=987654)
        self.process.poll.return_value = None
        self.launch = Mock(return_value=self.process)
        self.controller = Controller(self.root, launcher=self.launch)
        self.addCleanup(self.controller.close_child)

    def test_invalid_run_time(self):
        for value in ("", "14:00", "2026-10-03 14:00", "2026-10-02 14:00:00"):
            with self.subTest(value=value), self.assertRaises(BookingError):
                parse_run_at(value, self.now)

    def test_default_is_standby(self):
        self.assertEqual(self.controller.store.read()["phase"], "standby")

    def test_blank_run_at_defaults_to_six_hours_before_start(self):
        self.config.write_text(self.config.read_text() + '\nRUN_AT=""\n')
        state = self.controller.arm(self.config, self.now)
        self.assertEqual(state["job"]["run_at"], "2026-10-03T13:00:00+08:00")
        self.launch.assert_not_called()

    def test_legacy_config_without_schedule_fields_defaults_to_six_hours(self):
        self.config.write_text(self.config.read_text() + '\ndel RUN_AT\nglobals().pop("RUN_BEFORE_HOURS", None)\n')
        state = self.controller.arm(self.config, self.now)
        self.assertEqual(state["job"]["run_at"], "2026-10-03T13:00:00+08:00")

    def test_custom_lead_time(self):
        self.config.write_text(self.config.read_text() + '\nRUN_AT=""\nRUN_BEFORE_HOURS=5\n')
        state = self.controller.arm(self.config, self.now)
        self.assertEqual(state["job"]["run_at"], self.run_at.isoformat())

    def test_fractional_lead_time(self):
        self.config.write_text(self.config.read_text() + '\nRUN_AT=""\nRUN_BEFORE_HOURS=5.5\n')
        state = self.controller.arm(self.config, self.now)
        self.assertEqual(state["job"]["run_at"], "2026-10-03T13:30:00+08:00")

    def test_manual_time_overrides_lead_even_if_lead_is_invalid(self):
        self.config.write_text(self.config.read_text() + '\nRUN_BEFORE_HOURS="unused"\n')
        state = self.controller.arm(self.config, self.now)
        self.assertEqual(state["job"]["run_at"], self.run_at.isoformat())

    def test_automatic_trigger_can_fall_on_previous_day(self):
        self.config.write_text(self.config.read_text() + '\nRUN_AT=""\nDATE="2026-10-04"\nSTART_TIME="07:00"\nEND_TIME="09:00"\nRUN_BEFORE_HOURS=8\n')
        state = self.controller.arm(self.config, self.now)
        self.assertEqual(state["job"]["date"], "2026-10-04")
        self.assertEqual(state["job"]["run_at"], "2026-10-03T23:00:00+08:00")

    def test_passed_or_current_automatic_time_rejected_without_task(self):
        self.config.write_text(self.config.read_text() + '\nRUN_AT=""\n')
        for hours in (7, 8):
            with self.subTest(hours=hours):
                self.config.write_text(self.config.read_text() + f'\nRUN_BEFORE_HOURS={hours}\n')
                with self.assertRaisesRegex(BookingError, "已.*过去"):
                    self.controller.arm(self.config, self.now)
                self.assertEqual(self.controller.store.read()["phase"], "standby")
                self.assertEqual(list(self.controller.store.directory.glob("job-*.py")), [])
                self.launch.assert_not_called()

    def test_invalid_lead_rejected_without_task(self):
        for value in (0, -1, True, False, "6", None, float("inf"), float("nan"), 1e300, 10 ** 400):
            expression = repr(value) if isinstance(value, (int, str, bool)) or value is None else f'float("{value}")'
            self.config.write_text(self.config.read_text() + f'\nRUN_AT=""\nRUN_BEFORE_HOURS={expression}\n')
            with self.subTest(value=value), self.assertRaisesRegex(BookingError, "RUN_BEFORE_HOURS"):
                self.controller.arm(self.config, self.now)
            self.assertEqual(self.controller.store.read()["phase"], "standby")
            self.launch.assert_not_called()

    def test_invalid_manual_time_does_not_fall_back_to_automatic(self):
        for value in (None, 0, " ", "14:00", "2026-10-03 10:00:00"):
            self.config.write_text(self.config.read_text() + f'\nRUN_AT={value!r}\n')
            with self.subTest(value=value), self.assertRaisesRegex(BookingError, "RUN_AT"):
                self.controller.arm(self.config, self.now)
            self.assertEqual(self.controller.store.read()["phase"], "standby")
            self.launch.assert_not_called()

    def test_auto_trigger_is_frozen_when_user_changes_lead(self):
        self.config.write_text(self.config.read_text() + '\nRUN_AT=""\n')
        state = self.controller.arm(self.config, self.now)
        self.config.write_text(self.config.read_text() + '\nRUN_BEFORE_HOURS=5\n')
        self.assertEqual(self.controller.store.read()["job"]["run_at"], state["job"]["run_at"])
        self.assertEqual(state["job"]["run_at"], "2026-10-03T13:00:00+08:00")

    def test_arm_freezes_config_and_today_date(self):
        result = self.controller.arm(self.config, self.now)
        self.assertEqual(result["phase"], "waiting")
        snapshot = self.controller.snapshot_path(result["job"])
        self.config.write_text("USER_CHANGED_CONFIG = True\n")
        text = snapshot.read_text()
        self.assertIn("PASSWORD = 'fake-password'", text)
        self.assertIn("DATE = '2026-10-03'", text)
        self.assertIn("HEADLESS = True", text)

    def test_dry_run_snapshot_cannot_submit(self):
        state = self.controller.arm(self.config, self.now, dry_run=True)
        self.assertFalse(state["job"]["submit"])
        self.assertIn("SUBMIT = False", self.controller.snapshot_path(state["job"]).read_text())

    def test_private_state_and_snapshot(self):
        state = self.controller.arm(self.config, self.now)
        self.assertEqual(stat.S_IMODE(self.controller.store.directory.stat().st_mode), 0o700)
        for path in (self.controller.store.path, self.controller.snapshot_path(state["job"])):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_second_arm_is_rejected(self):
        self.controller.arm(self.config, self.now)
        with self.assertRaises(BookingError):
            self.controller.arm(self.config, self.now)

    def test_missing_credentials_rejects_arm(self):
        self.config.write_text(self.config.read_text() + '\nPASSWORD=""\n')
        with self.assertRaises(BookingError):
            self.controller.arm(self.config, self.now)
        self.assertEqual(self.controller.store.read()["phase"], "standby")

    def test_run_after_reservation_start_is_rejected(self):
        self.config.write_text(self.config.read_text() + '\nRUN_AT="2026-10-03 20:00:00"\n')
        with self.assertRaises(BookingError):
            self.controller.arm(self.config, self.now)

    def test_no_launch_before_time(self):
        self.controller.arm(self.config, self.now)
        self.controller.tick(self.run_at - timedelta(seconds=1))
        self.launch.assert_not_called()

    def test_completion_returns_to_standby(self):
        self.controller.arm(self.config, self.now)
        self.controller.tick(self.run_at)
        self.assertEqual(self.controller.store.read()["phase"], "running")
        self.process.poll.return_value = 0
        self.controller.tick(self.run_at)
        self.controller.tick(self.run_at + timedelta(seconds=1))
        state = self.controller.store.read()
        self.assertEqual(state["phase"], "standby")
        self.assertEqual(state["last_result"]["outcome"], "success")
        self.launch.assert_called_once()

    def test_failure_does_not_retry(self):
        self.controller.arm(self.config, self.now)
        self.controller.tick(self.run_at)
        self.process.poll.return_value = 1
        self.controller.tick(self.run_at)
        self.controller.tick(self.run_at + timedelta(seconds=1))
        self.assertEqual(self.controller.store.read()["last_result"]["outcome"], "failed")
        self.launch.assert_called_once()

    def test_launch_failure_returns_to_standby(self):
        self.controller.arm(self.config, self.now)
        self.launch.side_effect = OSError("fake launch error")
        self.controller.tick(self.run_at)
        self.assertEqual(self.controller.store.read()["phase"], "standby")
        self.assertEqual(self.controller.store.read()["last_result"]["outcome"], "launch_failed")

    def test_stop_waiting_is_persistent(self):
        self.controller.arm(self.config, self.now)
        self.controller.stop()
        restarted = Controller(self.root, launcher=self.launch)
        restarted.recover(self.run_at)
        restarted.tick(self.run_at)
        self.assertEqual(restarted.store.read()["phase"], "standby")
        self.launch.assert_not_called()

    def test_stop_running_requests_termination(self):
        self.controller.arm(self.config, self.now)
        self.controller.tick(self.run_at)
        self.controller.stop()
        self.assertEqual(self.controller.store.read()["phase"], "stopping")
        self.controller.terminate_child = Mock()
        self.controller.tick(self.run_at)
        self.controller.terminate_child.assert_called_once()
        self.assertEqual(self.controller.store.read()["phase"], "standby")
        self.assertEqual(self.controller.store.read()["last_result"]["outcome"], "stopped_check_records")

    def test_restart_restores_future_wait(self):
        self.controller.arm(self.config, self.now)
        restarted = Controller(self.root, launcher=self.launch)
        self.addCleanup(restarted.close_child)
        restarted.recover(self.now + timedelta(hours=1))
        self.assertEqual(restarted.store.read()["phase"], "waiting")
        restarted.tick(self.run_at)
        self.launch.assert_called_once()

    def test_restart_never_replays_a_claimed_job(self):
        self.controller.arm(self.config, self.now)
        self.controller.tick(self.run_at)
        restarted = Controller(self.root, launcher=self.launch)
        restarted.recover(self.run_at)
        restarted.tick(self.run_at)
        self.assertEqual(restarted.store.read()["last_result"]["outcome"], "interrupted_check_records")
        self.launch.assert_called_once()

    def test_restart_missed_time_is_not_caught_up(self):
        self.controller.arm(self.config, self.now)
        restarted = Controller(self.root, launcher=self.launch)
        restarted.recover(self.run_at + timedelta(seconds=1))
        restarted.tick(self.run_at + timedelta(seconds=1))
        self.assertEqual(restarted.store.read()["last_result"]["outcome"], "missed")
        self.launch.assert_not_called()

    def test_live_daemon_stall_does_not_catch_up(self):
        self.controller.arm(self.config, self.now)
        self.controller.tick(self.run_at + timedelta(seconds=6))
        self.assertEqual(self.controller.store.read()["last_result"]["outcome"], "missed")
        self.launch.assert_not_called()

    def test_missing_snapshot_does_not_launch(self):
        state = self.controller.arm(self.config, self.now)
        self.controller.snapshot_path(state["job"]).unlink()
        self.controller.tick(self.run_at)
        self.assertEqual(self.controller.store.read()["last_result"]["outcome"], "launch_failed")
        self.launch.assert_not_called()

    def test_state_corruption_is_not_silently_reset(self):
        self.controller.store.path.write_text("not json")
        with self.assertRaises(BookingError):
            self.controller.store.read()

    def test_snapshot_path_cannot_escape_private_directory(self):
        with self.assertRaises(BookingError):
            self.controller.snapshot_path({"snapshot": "../../config.py"})

    def test_status_summary_never_contains_password(self):
        self.controller.arm(self.config, self.now)
        from booking_service import describe
        self.assertNotIn("fake-password", describe(self.controller.store.read()))

    def test_real_fake_worker_completes_without_website_access(self):
        # 真正启动 Python 子进程，但运行的是临时假脚本，不是预约脚本。
        (self.root / "reserve.py").write_text("print('FAKE_WORKER_DONE')\n")
        controller = Controller(self.root)
        self.addCleanup(controller.close_child)
        controller.arm(self.config, self.now)
        controller.tick(self.run_at)
        deadline = time.monotonic() + 5
        while controller.store.read()["phase"] != "standby" and time.monotonic() < deadline:
            time.sleep(0.02)
            controller.tick(self.run_at)
        self.assertEqual(controller.store.read()["phase"], "standby")
        self.assertEqual(controller.store.read()["last_result"]["outcome"], "success")
        self.assertIn("FAKE_WORKER_DONE", (controller.store.directory / "last-run.log").read_text())


if __name__ == "__main__":
    unittest.main()
