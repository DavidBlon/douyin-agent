"""Douyin Agent 守护进程入口。

RunHiddenConsole 会把本脚本拉起为一个无窗口后台进程，崩溃后自动重启。
脚本在当前进程内运行 uvicorn，并把 stdout / stderr 追加写入 data/logs/server.log。

为什么不直接让 RunHiddenConsole 跑 uvicorn：

1. RunHiddenConsole 会把子进程的工作目录设成「目标 exe 所在目录」（不是项目根目录），
   所以这里用 --app-dir 显式指定项目根目录，保证 `app.main:app` 能被导入。
2. RunHiddenConsole 自带的 /o 重定向在每次自动重启时会清空日志文件，崩溃堆栈会丢；
   这里改用追加模式自己记录日志，重启也留痕。
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "data" / "logs" / "server.log"


class _TeeStream:
    """写入日志文件的文本流；原始流是终端时额外镜像一份到终端。"""

    def __init__(self, path: Path, mirror=None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8", buffering=1)
        self._mirror = mirror

    def write(self, text: str) -> int:
        self._file.write(text)
        if self._mirror is not None:
            self._mirror.write(text)
        return len(text)

    def flush(self) -> None:
        self._file.flush()
        if self._mirror is not None:
            self._mirror.flush()

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("TeeStream has no file descriptor")


def _is_console(stream) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Douyin Agent 守护进程入口")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--app-dir", default=str(ROOT))
    parser.add_argument("--log-file", default=str(DEFAULT_LOG))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log_path = Path(args.log_file)

    # 被 RunHiddenConsole 拉起时原始流不是终端，只写文件；手动运行时镜像到终端。
    sys.stdout = _TeeStream(log_path, sys.stdout if _is_console(sys.stdout) else None)
    sys.stderr = _TeeStream(log_path, sys.stderr if _is_console(sys.stderr) else None)

    print(
        f"[runner] 启动 pid={os.getpid()} at {datetime.now():%Y-%m-%d %H:%M:%S} "
        f"app={args.app_dir} {args.host}:{args.port}"
    )
    print(f"[runner] 日志 {log_path}")
    sys.stdout.flush()

    try:
        import uvicorn
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        return 2

    try:
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            app_dir=args.app_dir,
            log_level="info",
        )
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
