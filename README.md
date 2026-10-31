# 自动预约工具

这是一个用于自动完成预约流程的 Python 工具。程序会按照配置尝试可用选项，成功后停止；也可以先使用演练模式，在提交前检查填写结果。

## 安装

需要 Python 3.9 或更高版本。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 配置

先复制示例配置：

```bash
cp config.example.py config.py
```

在 `config.py` 中填写账号、日期、时间和其他预约参数。真实配置文件可能包含敏感信息，请勿提交到 Git；项目已通过 `.gitignore` 排除 `config.py`。

常用参数包括：

```python
DATE = "today"
START_TIME = "19:00"
END_TIME = "22:00"
SUBMIT = False
HEADLESS = True
```

首次运行建议保持 `SUBMIT = False`，确认演练结果后再改为 `True`。

## 运行

检查配置：

```bash
python reserve.py --check-config
```

立即执行演练：

```bash
python reserve.py --dry-run
```

确认配置后执行真实预约：

```bash
python reserve.py
```

程序会检查已有记录、验证表单内容，并在结果无法确认时停止，不会自动重复提交。

## 定时任务

项目提供单次定时任务命令：

```bash
booking-run --dry-run  # 注册一次演练任务
booking-run            # 注册一次任务
booking-status         # 查看状态
booking-stop           # 撤销等待任务或请求停止运行中的任务
```

`RUN_AT` 可以指定启动时间；留空时按 `START_TIME` 和 `RUN_BEFORE_HOURS` 计算。任务完成或失败后回到待命状态，不会自动重复。

## 安全注意事项

- 不要把真实密码写入文档、日志或版本库。
- `config.py`、运行状态、日志和截图只应保留在受控环境中。
- 提交超时或结果不确定时，先到预约系统确认状态，再决定是否重试。
- 系统规则变化、登录失败或出现额外认证时，程序会停止等待人工处理。

## 测试

```bash
python -m unittest discover -s tests -v
```

完整使用说明见 [USAGE.md](USAGE.md)。
