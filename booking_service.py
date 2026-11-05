"""单次预约的系统服务控制器。默认待命；注册任务不会立即预约。"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from reserve import BookingError, ROOT, SHANGHAI, load_settings

UNIT = "booking.service"
MAX_START_DELAY_SECONDS = 5
PHASES = {"standby", "waiting", "running", "stopping"}


def local_now():
    return datetime.now(SHANGHAI)


def parse_run_at(value, now):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
        raise BookingError("请填写 RUN_AT：北京时间 YYYY-MM-DD HH:MM:SS。")
    try:
        result = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI)
    except ValueError:
        raise BookingError("RUN_AT 的日期或时间无效。")
    if result <= now:
        raise BookingError("RUN_AT 已经过去；请填写未来的启动时间，不自动补跑。")
    return result


def resolve_run_at(module, start, now):
    value = getattr(module, "RUN_AT", "")
    if value != "":
        # 显式时间具有优先级；填写错误时也不能悄悄退回自动模式。
        return parse_run_at(value, now)
    hours = getattr(module, "RUN_BEFORE_HOURS", 6)
    if isinstance(hours, bool) or not isinstance(hours, (int, float)):
        raise BookingError("RUN_BEFORE_HOURS 必须是大于 0 的有限数字（单位：小时）。")
    try:
        hours = float(hours)
        if not math.isfinite(hours) or hours <= 0:
            raise ValueError
        result = start - timedelta(hours=hours)
    except (ValueError, OverflowError):
        raise BookingError("RUN_BEFORE_HOURS 必须是大于 0 的有限数字，且不能超出日期范围。")
    if result <= now:
        raise BookingError(
            f"自动计算的启动时间 {result:%Y-%m-%d %H:%M:%S}（北京时间）已经过去；"
            "请修改预约日期、RUN_BEFORE_HOURS 或填写未来的 RUN_AT，不自动补跑。"
        )
    return result


class Store:
    def __init__(self, root):
        self.directory = Path(root) / ".booking-service"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.path = self.directory / "state.json"

    @contextmanager
    def locked(self):
        with (self.directory / "state.lock").open("a") as handle:
            os.fchmod(handle.fileno(), 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def read(self):
        if not self.path.exists():
            return {"phase": "standby", "job": None, "last_result": None}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("phase") not in PHASES:
                raise ValueError
            if state["phase"] != "standby" and not isinstance(state.get("job"), dict):
                raise ValueError
            return state
        except (OSError, ValueError, TypeError):
            raise BookingError("服务状态无法读取，已停止；请检查私有状态文件，不会自动重置或重试。")

    def write(self, state):
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(state, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class Controller:
    def __init__(self, root=ROOT, launcher=None):
        self.root = Path(root)
        self.store = Store(self.root)
        self.launcher = launcher or subprocess.Popen
        self.process = None
        self.running_job = None
        self.log_handle = None

    def snapshot_path(self, job):
        name = job.get("snapshot", "")
        if not isinstance(name, str) or not re.fullmatch(r"job-[0-9a-f]{32}\.py", name):
            raise BookingError("任务快照路径无效，已停止。")
        return self.store.directory / name

    def arm(self, config_path, now, dry_run=False):
        # config.py 是用户拥有的 Python 配置；只执行捕获到的这一份配置。
        module = SimpleNamespace(__file__=str(config_path), __name__="booking_config")
        try:
            source = Path(config_path).read_text(encoding="utf-8")
            exec(compile(source, str(config_path), "exec"), module.__dict__)
        except Exception:
            raise BookingError("无法读取配置，请检查 config.py 的路径和语法；没有注册任务。")
        args = argparse.Namespace(date=None, start=None, end=None, dry_run=dry_run, headless=True)
        settings = load_settings(config_path, args, now, config_module=module)
        if not settings.username or not settings.password or not settings.account_name:
            raise BookingError("后台任务需要填写 USERNAME、PASSWORD 和 ACCOUNT_NAME；不能交互输入密码。")
        start = datetime.strptime(settings.date + " " + settings.start, "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI)
        run_at = resolve_run_at(module, start, now)
        if run_at >= start:
            raise BookingError("RUN_AT 必须早于座位使用的开始时间；请给登录和页面操作预留时间。")

        # 冻结求值后的参数，包括环境变量提供的凭据；运行时不重新读取用户配置。
        values = {
            "BASE_URL": settings.base_url, "AREA_ID": settings.area_id,
            "AREA_NAME": settings.area_name, "USERNAME": settings.username,
            "PASSWORD": settings.password, "ACCOUNT_NAME": settings.account_name,
            "DATE": settings.date, "START_TIME": settings.start, "END_TIME": settings.end,
            "SEATS": settings.seats, "REMARK": settings.remark, "SUBMIT": settings.submit,
            "HEADLESS": True, "BROWSER_CHANNEL": settings.channel,
            "TIMEOUT_MS": settings.timeout, "ACTION_DELAY_SECONDS": settings.action_delay,
            "OPEN_PERIODS": module.OPEN_PERIODS,
            "MIN_DURATION_MINUTES": module.MIN_DURATION_MINUTES,
            "MAX_DURATION_MINUTES": module.MAX_DURATION_MINUTES,
            "TIME_STEP_MINUTES": module.TIME_STEP_MINUTES,
        }
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id, "snapshot": f"job-{job_id}.py", "run_at": run_at.isoformat(),
            "date": settings.date, "start": settings.start, "end": settings.end,
            "seats": list(settings.seats), "submit": settings.submit,
        }
        with self.store.locked():
            state = self.store.read()
            if state["phase"] != "standby":
                raise BookingError("已有等待或运行中的任务；请先 booking-stop，再注册新任务。")
            path = self.snapshot_path(job)
            try:
                with path.open("x", encoding="utf-8") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write("\n".join(f"{key} = {value!r}" for key, value in values.items()) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                state.update(phase="waiting", job=job)
                self.store.write(state)
            except Exception:
                path.unlink(missing_ok=True)
                raise
        return state

    @staticmethod
    def finish_state(state, outcome, now):
        job = state.get("job") or {}
        state.update(phase="standby", job=None, last_result={
            "outcome": outcome, "time": now.isoformat(), "run_at": job.get("run_at"),
            "date": job.get("date"), "start": job.get("start"), "end": job.get("end"),
            "submit": job.get("submit"),
        })

    def retire_snapshot(self, job):
        # 只删除本控制器创建的、严格命名的配置快照，不触碰用户 config.py。
        if job:
            self.snapshot_path(job).unlink(missing_ok=True)

    def stop(self):
        with self.store.locked():
            state = self.store.read()
            if state["phase"] == "waiting":
                job = state["job"]
                self.finish_state(state, "cancelled_before_run", local_now())
                self.store.write(state)
                self.retire_snapshot(job)
            elif state["phase"] == "running":
                state["phase"] = "stopping"
                self.store.write(state)
            elif state["phase"] == "standby":
                self.store.write(state)
        return state

    def recover(self, now):
        with self.store.locked():
            state = self.store.read()
            job = state.get("job")
            if state["phase"] in {"running", "stopping"}:
                self.finish_state(state, "interrupted_check_records", now)
            elif state["phase"] == "waiting" and datetime.fromisoformat(job["run_at"]) <= now:
                self.finish_state(state, "missed", now)
            self.store.write(state)
            if job and state["phase"] == "standby":
                self.retire_snapshot(job)
        return state

    def terminate_child(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=3)

    def close_child(self):
        if self.log_handle:
            self.log_handle.close()
        self.process = self.running_job = self.log_handle = None

    def tick(self, now):
        with self.store.locked():
            state = self.store.read()
            if self.process is not None:
                stopped = state["phase"] == "stopping"
                if stopped:
                    self.terminate_child()
                code = self.process.poll()
                if code is None and not stopped:
                    return
                job = self.running_job
                self.finish_state(state, "stopped_check_records" if stopped else ("success" if code == 0 else "failed"), now)
                self.store.write(state)
                self.retire_snapshot(job)
                self.close_child()
                print("单次任务已结束，回到待命。", flush=True)
                return
            if state["phase"] != "waiting":
                return
            job = state["job"]
            run_at = datetime.fromisoformat(job["run_at"])
            if now < run_at:
                return
            if (now - run_at).total_seconds() > MAX_START_DELAY_SECONDS:
                self.finish_state(state, "missed", now)
                self.store.write(state)
                self.retire_snapshot(job)
                print("错过启动时间，回到待命，不补跑。", flush=True)
                return
            # 先持久记录已认领任务，再启动；崩溃恢复也绝不重放。
            state["phase"] = "running"
            self.store.write(state)
            try:
                snapshot = self.snapshot_path(job)
                if not snapshot.is_file():
                    raise BookingError("任务快照缺失。")
                log_path = self.store.directory / "last-run.log"
                self.log_handle = log_path.open("w", encoding="utf-8")
                os.fchmod(self.log_handle.fileno(), 0o600)
                self.process = self.launcher(
                    [sys.executable, str(self.root / "reserve.py"), "--config", str(snapshot), "--headless"],
                    stdin=subprocess.DEVNULL, stdout=self.log_handle, stderr=subprocess.STDOUT,
                    start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
                )
                self.running_job = job
                print("已启动单次预约流程；结果将记录到私有日志。", flush=True)
            except Exception:
                self.finish_state(state, "launch_failed", now)
                self.store.write(state)
                self.retire_snapshot(job)
                self.close_child()
                print("启动失败，回到待命；不自动重试。", flush=True)


def describe(state):
    labels = {"standby": "待命", "waiting": "等待执行", "running": "预约运行中", "stopping": "正在停止，请核查已提交结果"}
    lines = ["任务状态：" + labels[state["phase"]]]
    job = state.get("job")
    if job:
        at = datetime.fromisoformat(job["run_at"]).astimezone(SHANGHAI)
        lines.extend([f"启动时间：{at:%Y-%m-%d %H:%M:%S}（北京时间）",
                      f"座位使用：{job['date']} {job['start']}–{job['end']}",
                      "候选顺序：" + " → ".join(job["seats"]),
                      "执行模式：" + ("真实预约" if job["submit"] else "演练，不提交")])
    last = state.get("last_result")
    outcomes = {"success": "流程成功结束（演练任务不会提交）", "failed": "流程失败，请查看日志；没有自动重试",
                "launch_failed": "启动失败", "missed": "错过启动时间，未补跑",
                "cancelled_before_run": "任务已在启动前撤销", "interrupted_check_records": "运行中断，请核查个人中心",
                "stopped_check_records": "运行已停止，请核查个人中心；不会取消已成功预约"}
    if last:
        result = outcomes.get(last.get("outcome"), "未知，请核查")
        if last.get("outcome") == "success" and last.get("submit") is False:
            result = "演练完成，未提交预约"
        lines.append("上次结果：" + result)
    return "\n".join(lines)


def service_active():
    if sys.platform == "darwin":
        # 服务进程运行时会持续持有 daemon.lock 的排他锁。
        lock_path = ROOT / ".booking-service" / "daemon.lock"
        try:
            with lock_path.open("r+") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                fcntl.flock(handle, fcntl.LOCK_UN)
                return False
        except OSError:
            return False

    try:
        return subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", UNIT],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
    except OSError:
        return False


def daemon(root):
    controller = Controller(root)
    event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: event.set())
    signal.signal(signal.SIGINT, lambda *_: event.set())
    with (controller.store.directory / "daemon.lock").open("a") as handle:
        os.fchmod(handle.fileno(), 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BookingError("已有服务进程运行，不能重复启动。")
        controller.recover(local_now())
        print("预约服务已启动；默认待命，完成单次任务后仍待命。", flush=True)
        try:
            while not event.is_set():
                controller.tick(local_now())
                event.wait(1)
        finally:
            if controller.process is not None:
                controller.stop()
                controller.tick(local_now())


def main():
    parser = argparse.ArgumentParser(description="注册单次预约任务、待命和状态查询")
    parser.add_argument("command", choices=["arm", "stop", "status", "daemon"])
    parser.add_argument("--dry-run", action="store_true", help="注册演练任务，绝不提交")
    args = parser.parse_args()
    try:
        if args.command == "daemon":
            daemon(ROOT)
            return 0
        controller = Controller(ROOT)
        if args.command == "arm":
            if not service_active():
                raise BookingError("系统服务未运行，请先启动 booking.service；未注册任务。")
            state = controller.arm(ROOT / "config.py", local_now(), args.dry_run)
            print(describe(state))
            print("当前配置已锁定；修改配置后需 booking-stop，再 booking-run。")
        elif args.command == "stop":
            print(describe(controller.stop()))
        else:
            print("系统服务：" + ("运行中" if service_active() else "未运行"))
            with controller.store.locked():
                print(describe(controller.store.read()))
            print("最近运行日志：" + str(controller.store.directory / "last-run.log"))
        return 0
    except Exception as exc:
        # 不打印配置源、对象或堆栈。
        print(f"停止：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
