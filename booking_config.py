"""编辑配置或用 Rich 打印使用说明；帮助不读取或执行用户配置。"""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def render_help(root):
    try:
        from rich.console import Console
        from rich.markdown import Markdown
    except ImportError:
        print("缺少 Rich，请在项目虚拟环境中执行：python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    try:
        guide = (root / "USAGE.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        print("无法读取 USAGE.md，请确认使用说明与 booking_config.py 位于同一目录且为 UTF-8。", file=sys.stderr)
        return 1
    Console().print(Markdown(guide))
    return 0


def main(argv=None, root=ROOT):
    arguments = list(sys.argv[1:] if argv is None else argv)
    root = Path(root)
    if arguments in (["help"], ["-h"], ["--help"]):
        return render_help(root)
    if arguments:
        print("用法：booking-config [help]；无参数编辑配置，help 打印使用说明。", file=sys.stderr)
        return 2
    path = root / "config.py"
    if not path.is_file():
        print("找不到 config.py，请先在项目目录复制 config.example.py 为 config.py 并填写参数。", file=sys.stderr)
        return 1
    try:
        return subprocess.call(["nano", str(path)])
    except OSError:
        print("无法启动 nano，请确认已安装 nano，或用其他编辑器手动编辑项目中的 config.py。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
