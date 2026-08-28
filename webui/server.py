#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Galbot Piano 本地调试 Web UI。

这个服务只监听本机地址。它提供：
  - 机器人 SDK 连接状态、连接和断开；
  - 已验证的双灵巧手安全归零（张开）；
  - 《小星星》与《茉莉花》的选曲、启动和软停止；
  - 子进程运行日志，便于现场定位 SDK 或动作问题。

不依赖 Flask 等 Web 框架；运行机器人演奏时仍使用仓库现有脚本，避免
在 Web 层重复实现未经真机验证的演奏逻辑。
"""
from __future__ import annotations

import argparse
import json
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import subprocess
import sys
import threading
from typing import Any, Deque, Dict, Optional
from urllib.parse import unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = Path(__file__).resolve().parent / "static"


try:
    from galbot_sdk.g1 import ControlStatus, GalbotRobot, JointCommand

    SDK_IMPORT_ERROR: Optional[str] = None
except ImportError as error:
    ControlStatus = GalbotRobot = JointCommand = None  # type: ignore[assignment,misc]
    SDK_IMPORT_ERROR = str(error)


@dataclass(frozen=True)
class Song:
    """可从 Web UI 启动的一首曲目。"""

    key: str
    title: str
    script: str
    description: str


SONGS: Dict[str, Song] = {
    "twinkle": Song(
        key="twinkle",
        title="小星星",
        script="piano_extended.py",
        description="固定节拍演奏，使用 APScheduler 调度。",
    ),
    "molihua": Song(
        key="molihua",
        title="茉莉花",
        script="molihua_hand_move.py",
        description="包含左右手移位与逆运动学控制。",
    ),
}


class ApiError(Exception):
    """将可预期的控制层错误转换为 API 响应。"""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.message = message
        self.status = status


def now_iso() -> str:
    """以本地时区显示操作时间，便于现场对照日志。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def generate_safe_hand_commands() -> list:
    """生成项目当前已采用的双灵巧手安全张开姿态。

    RH56 的拇指旋转固定为 0；其余关节为 1000（张开）。这不是全身关节
    零位：全身零位的 SDK 接口和安全姿态须在真机上按官方规范另行确认。
    """
    if JointCommand is None:
        raise ApiError(
            f"galbot_sdk 不可用，无法发送归零指令：{SDK_IMPORT_ERROR}",
            HTTPStatus.SERVICE_UNAVAILABLE,
        )
    commands = [JointCommand() for _ in range(6)]
    commands[0].position = 0
    for command in commands[1:]:
        command.position = 1000
    return commands


class RobotController:
    """串行化机器人控制和演奏子进程，避免多个客户端抢占机器人。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._robot: Any = None
        self._selected_song = "twinkle"
        self._playback: Optional[subprocess.Popen[str]] = None
        self._playback_song: Optional[Song] = None
        self._playback_started_at: Optional[str] = None
        self._last_playback: Optional[Dict[str, Any]] = None
        self._logs: Deque[str] = deque(maxlen=160)

    def _require_sdk(self) -> None:
        if GalbotRobot is None:
            raise ApiError(
                f"未检测到 galbot_sdk：{SDK_IMPORT_ERROR}",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

    def _is_playing_locked(self) -> bool:
        return self._playback is not None and self._playback.poll() is None

    def _append_log_locked(self, message: str) -> None:
        self._logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def status(self) -> Dict[str, Any]:
        with self._lock:
            is_playing = self._is_playing_locked()
            if is_playing:
                connection = {
                    "state": "playing",
                    "label": "演奏中（连接由演奏进程持有）",
                    "detail": "演奏脚本正在自行连接机器人；此时不可同时发送调试指令。",
                }
            elif self._robot is not None:
                connection = {
                    "state": "connected",
                    "label": "已连接",
                    "detail": "Web UI 已完成 GalbotRobot.init()。",
                }
            elif SDK_IMPORT_ERROR:
                connection = {
                    "state": "unavailable",
                    "label": "SDK 不可用",
                    "detail": SDK_IMPORT_ERROR,
                }
            else:
                connection = {
                    "state": "disconnected",
                    "label": "未连接",
                    "detail": "可先连接机器人，或直接启动选中的曲目。",
                }

            active_playback: Optional[Dict[str, Any]] = None
            if is_playing and self._playback is not None and self._playback_song is not None:
                active_playback = {
                    "song": self._playback_song.key,
                    "title": self._playback_song.title,
                    "script": self._playback_song.script,
                    "pid": self._playback.pid,
                    "started_at": self._playback_started_at,
                }

            return {
                "connection": connection,
                "sdk_available": SDK_IMPORT_ERROR is None,
                "selected_song": self._selected_song,
                "songs": [song.__dict__ for song in SONGS.values()],
                "playback": active_playback,
                "last_playback": self._last_playback,
                "logs": list(self._logs),
                "safe_reset_scope": "双灵巧手安全张开姿态（非全身关节零位）",
            }

    def connect(self) -> None:
        self._require_sdk()
        with self._lock:
            if self._is_playing_locked():
                raise ApiError("演奏进行中，不能建立第二个机器人连接。", HTTPStatus.CONFLICT)
            if self._robot is not None:
                return

            robot = GalbotRobot()
            try:
                robot.init()
            except Exception as error:
                self._append_log_locked(f"连接失败：{error}")
                self._safe_destroy(robot)
                raise ApiError(f"机器人连接失败：{error}", HTTPStatus.SERVICE_UNAVAILABLE) from error

            self._robot = robot
            self._append_log_locked("机器人已连接。")

    def _safe_destroy(self, robot: Any) -> None:
        """尽最大努力释放 SDK 资源；失败也不阻塞后续状态恢复。"""
        for method_name in ("request_shutdown", "wait_for_shutdown", "destroy"):
            try:
                getattr(robot, method_name)()
            except Exception:
                pass

    def _disconnect_locked(self) -> None:
        robot, self._robot = self._robot, None
        if robot is not None:
            self._safe_destroy(robot)
            self._append_log_locked("机器人已断开。")

    def disconnect(self) -> None:
        with self._lock:
            if self._is_playing_locked():
                raise ApiError("演奏进行中，请先执行“软停止演奏”。", HTTPStatus.CONFLICT)
            self._disconnect_locked()

    def safe_reset_hands(self) -> None:
        with self._lock:
            if self._is_playing_locked():
                raise ApiError("演奏进行中，禁止插入归零动作。", HTTPStatus.CONFLICT)
            if self._robot is None:
                raise ApiError("请先连接机器人。", HTTPStatus.CONFLICT)

            commands = generate_safe_hand_commands()
            try:
                for end_effector in ("left_dexhand", "right_dexhand"):
                    result = self._robot.set_dexhand_command(
                        end_effector=end_effector,
                        dexhand_command=commands,
                        is_blocking=False,
                    )
                    if ControlStatus is not None and result != ControlStatus.SUCCESS:
                        raise RuntimeError(f"{end_effector} 返回 {result}")
            except Exception as error:
                self._append_log_locked(f"双手安全归零失败：{error}")
                raise ApiError(f"双手安全归零失败：{error}", HTTPStatus.BAD_GATEWAY) from error

            self._append_log_locked("已发送双灵巧手安全归零指令。")

    def select_song(self, key: str) -> None:
        with self._lock:
            if key not in SONGS:
                raise ApiError("未知曲目。")
            if self._is_playing_locked():
                raise ApiError("演奏进行中，不能切换曲目。", HTTPStatus.CONFLICT)
            self._selected_song = key
            self._append_log_locked(f"已选择《{SONGS[key].title}》。")

    def start_playback(self) -> None:
        self._require_sdk()
        with self._lock:
            if self._is_playing_locked():
                raise ApiError("已有曲目正在演奏。", HTTPStatus.CONFLICT)

            song = SONGS[self._selected_song]
            script_path = PROJECT_ROOT / song.script
            if not script_path.is_file():
                raise ApiError(f"找不到演奏脚本：{script_path}", HTTPStatus.NOT_FOUND)

            # 现有曲目脚本自行创建 GalbotRobot，因此启动前必须释放调试连接。
            if self._robot is not None:
                self._append_log_locked("为启动演奏脚本，已释放 Web UI 的机器人连接。")
                self._disconnect_locked()

            creation_flags = 0
            if os.name == "nt":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                process = subprocess.Popen(
                    [sys.executable, str(script_path)],
                    cwd=str(PROJECT_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creation_flags,
                )
            except OSError as error:
                raise ApiError(f"无法启动演奏脚本：{error}", HTTPStatus.SERVICE_UNAVAILABLE) from error

            self._playback = process
            self._playback_song = song
            self._playback_started_at = now_iso()
            self._last_playback = None
            self._append_log_locked(f"已启动《{song.title}》演奏进程（PID {process.pid}）。")
            threading.Thread(
                target=self._collect_playback_output,
                args=(process, song),
                name=f"playback-{song.key}",
                daemon=True,
            ).start()

    def _collect_playback_output(self, process: subprocess.Popen[str], song: Song) -> None:
        """持续清空 stdout，既保存现场日志也避免子进程因管道缓冲而卡住。"""
        if process.stdout is not None:
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    with self._lock:
                        self._append_log_locked(f"{song.title}: {line}")
        exit_code = process.wait()
        with self._lock:
            if self._playback is process:
                self._playback = None
                self._playback_song = None
                self._playback_started_at = None
            self._last_playback = {
                "song": song.key,
                "title": song.title,
                "exit_code": exit_code,
                "finished_at": now_iso(),
            }
            self._append_log_locked(f"《{song.title}》演奏进程结束（退出码 {exit_code}）。")

    def stop_playback(self) -> None:
        with self._lock:
            if not self._is_playing_locked() or self._playback is None:
                raise ApiError("当前没有正在演奏的曲目。", HTTPStatus.CONFLICT)
            try:
                if os.name == "nt":
                    self._playback.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self._playback.send_signal(signal.SIGINT)
            except (OSError, ValueError) as error:
                raise ApiError(f"无法向演奏进程发送安全停止信号：{error}") from error
            self._append_log_locked("已向演奏进程发送软停止信号，等待其执行安全清理。")

    def shutdown(self) -> None:
        """服务器退出时不强制杀死真机进程，只请求其走既有的安全退出流程。"""
        with self._lock:
            if self._is_playing_locked():
                try:
                    if os.name == "nt":
                        self._playback.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[union-attr]
                    else:
                        self._playback.send_signal(signal.SIGINT)  # type: ignore[union-attr]
                except (OSError, ValueError):
                    pass
            self._disconnect_locked()


class WebUiHandler(BaseHTTPRequestHandler):
    """提供静态页面与同源 JSON API。"""

    server_version = "GalbotPianoWebUI/1.0"

    @property
    def controller(self) -> RobotController:
        return self.server.controller  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        # API 访问日志交给控制台，避免混入机器人调试日志。
        print(f"[web] {self.address_string()} - {format % args}")

    def _send_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 16_384:
            raise ApiError("请求体过大。", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if length == 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError("请求必须是 UTF-8 JSON。") from error
        if not isinstance(payload, dict):
            raise ApiError("请求 JSON 必须是对象。")
        return payload

    def _serve_static(self, request_path: str) -> None:
        relative_path = "index.html" if request_path == "/" else unquote(request_path).lstrip("/")
        candidate = (STATIC_ROOT / relative_path).resolve()
        try:
            candidate.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }
        content = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_types.get(candidate.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == "/api/status":
            self._send_json({"ok": True, "data": self.controller.status()})
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/connect":
                self.controller.connect()
            elif path == "/api/disconnect":
                self.controller.disconnect()
            elif path == "/api/reset-hands":
                self.controller.safe_reset_hands()
            elif path == "/api/select-song":
                song = payload.get("song")
                if not isinstance(song, str):
                    raise ApiError("song 必须是字符串。")
                self.controller.select_song(song)
            elif path == "/api/play":
                self.controller.start_playback()
            elif path == "/api/stop":
                self.controller.stop_playback()
            else:
                raise ApiError("未知 API。", HTTPStatus.NOT_FOUND)
        except ApiError as error:
            self._send_json({"ok": False, "error": error.message}, error.status)
            return
        except Exception as error:  # noqa: BLE001 - 防止调试服务无响应
            self._send_json({"ok": False, "error": f"服务器内部错误：{error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self._send_json({"ok": True, "data": self.controller.status()})


def main() -> None:
    parser = argparse.ArgumentParser(description="Galbot Piano 本地调试 Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    parser.add_argument("--port", default=8080, type=int, help="监听端口（默认 8080）")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), WebUiHandler)
    server.controller = RobotController()  # type: ignore[attr-defined]
    print(f"Galbot Piano Web UI: http://{args.host}:{args.port}")
    print("按 Ctrl+C 关闭服务；演奏中的曲目会收到软停止信号。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在安全关闭 Web UI…")
    finally:
        server.controller.shutdown()  # type: ignore[attr-defined]
        server.server_close()


if __name__ == "__main__":
    main()
