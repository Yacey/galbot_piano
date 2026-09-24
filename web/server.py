#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QZ Piano 网页遥控器

启动:  cd /userdata/cx/QZ_Piano/web
        python3 server.py
然后浏览器打开启动日志中的 frontend_url (默认 http://<机器人 IP>:8088)

设计要点
--------
  - 仅依赖 Python 标准库 (Python 3.8+), 不需要 pip install.
  - 不调用 galbot_sdk; 只负责起停 piano 脚本子进程 (脚本自身会 robot.init / motion.init).
  - HTTP 轮询获取状态与日志 (前端每 1.5 秒拉一次), 不引入 WebSocket.
  - 端口 8088 避开 /userdata/guader/galbot_controller_web 的 8080, 两套 web 可并存.
  - 参考: /userdata/guader/galbot_controller_web/src/controller_server.py

被管理的脚本 (均在 /userdata/cx/QZ_Piano/main_code/ 下)
  loop_player.py           -- 歌单编排器, 顺序跑 molihua/farewell/backlighting
  molihua_hand_move.py     -- 茉莉花
  farewell.py              -- 送别
  backlighting.py          -- 逆光 (实为送别 SCORE)
"""
from __future__ import annotations

import argparse
import re
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ===== 路径 =====
WEB_DIR = Path(__file__).resolve().parent
PIANO_DIR = WEB_DIR.parent / "main_code"  # loop_player.py 与三首曲子所在目录
STATIC_DIR = WEB_DIR / "static"
LOGS_DIR = WEB_DIR / "logs"

# ===== 网络 =====
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8088  # 避开 controller_server 的 8080

# ===== 机器人状态探测目标 =====
# 机器人有 eth0(192.168.100.88) / eth1(192.168.1.88) / wlan0(10.130.78.118).
# 控制栈常见监听端口 50050 / 50051.
ROBOT_CONTROL_HOST = "192.168.1.88"
ROBOT_CONTROL_PORTS = (50050, 50051)
TCP_PROBE_TIMEOUT = 1.0  # 秒

# ===== 脚本清单 =====
# exclusive_with: 该脚本占用机器人时, 与之互斥的"组"集合.
#   playlist 组: loop_player (它自己管三首曲子)
#   score   组: 三首单曲子 (与 loop_player 同时跑会争用机器人)
SCRIPTS = [
    {
        "name": "loop_player.py",
        "label": "歌单（loop_player）",
        "desc": "按 SCORES 顺序循环播放 茉莉花 / 送别 / 逆光 / 达尔文",
        "group": "playlist",
        "exclusive_with": {"playlist", "score", "reset"},
        "action": "run",
        "requires_confirm": False,
        "play_count_arg": "--rounds",
        "default_play_count": 1,
        "play_count_min": 1,
        "play_count_max": 99,
        "section": "songs",
    },
    {
        "name": "molihua_hand_move.py",
        "label": "茉莉花",
        "desc": "单首茉莉花 (默认 1 次)",
        "group": "score",
        "exclusive_with": {"playlist", "score", "reset"},
        "action": "run",
        "requires_confirm": False,
        "wrapper": "Tools/run_score.py",
        "play_count_arg": "--repeat",
        "default_play_count": 1,
        "play_count_min": 1,
        "play_count_max": 99,
        "section": "songs",
    },
    {
        "name": "farewell.py",
        "label": "送别",
        "desc": "单首送别 (默认 1 次)",
        "group": "score",
        "exclusive_with": {"playlist", "score", "reset"},
        "action": "run",
        "requires_confirm": False,
        "wrapper": "Tools/run_score.py",
        "play_count_arg": "--repeat",
        "default_play_count": 1,
        "play_count_min": 1,
        "play_count_max": 99,
        "section": "songs",
    },
    {
        "name": "backlighting.py",
        "label": "逆光",
        "desc": "单首逆光 (默认 3 次)",
        "group": "score",
        "exclusive_with": {"playlist", "score", "reset"},
        "action": "run",
        "requires_confirm": False,
        "wrapper": "Tools/run_score.py",
        "play_count_arg": "--repeat",
        "default_play_count": 3,
        "play_count_min": 1,
        "play_count_max": 99,
        "section": "songs",
    },
    {
        "name": "darwin.py",
        "label": "达尔文",
        "desc": "单首达尔文 (默认 1 次)",
        "group": "score",
        "exclusive_with": {"playlist", "score", "reset"},
        "action": "run",
        "requires_confirm": False,
        "wrapper": "Tools/run_score.py",
        "play_count_arg": "--repeat",
        "default_play_count": 1,
        "play_count_min": 1,
        "play_count_max": 99,
        "section": "songs",
    },
    {
        "name": "Tools/reset_to_initial.py",
        "label": "复位",
        "desc": "双臂先到 FIRST_POSITION, 再执行全身归零 (move_whole_body_joint_zero)",
        "group": "reset",
        "exclusive_with": {"playlist", "score", "reset"},
        "action": "reset",
        "requires_confirm": True,
        "section": "tools",
    },
    {
        "name": "Tools/open_hands.py",
        "label": "张开手指",
        "desc": "双手灵巧手重置为张开状态 (拇旋转=0, 其余关节=1000)",
        "group": "hand",
        "exclusive_with": {"playlist", "score", "hand"},
        "action": "open",
        "requires_confirm": False,
        "section": "tools",
    },
    {
        "name": "Tools/calibrate_pose.py",
        "label": "基础调姿",
        "desc": "双手到 FIRST_POSITION → ORIGINAL 等 N 秒 → 全身归零 (用户校准琴键位姿)",
        "group": "reset",
        "exclusive_with": {"playlist", "score", "reset"},
        "action": "reset",
        "requires_confirm": True,
        "play_count_arg": "--wait",
        "default_play_count": 10,
        "play_count_min": 1,
        "play_count_max": 300,
        "play_count_label": "秒",
        "section": "tools",
    },
]
SCRIPT_BY_NAME = {s["name"]: s for s in SCRIPTS}

# ===== 子进程环境 =====
# galbot_sdk 在 /data/galbot/lib, 需要 PYTHONPATH + LD_LIBRARY_PATH 才能加载.
# 用户终端下 ~/.bashrc 会自动设置, 但 subprocess.Popen 不经过 bashrc,
# 所以这里显式注入, 否则所有钢琴脚本 + 复位脚本都会 "ModuleNotFoundError: galbot_sdk".
# 若机器人 SDK 路径不同, 调整 SDK_ENV 即可.
SDK_ENV = {
    "PYTHONPATH": "/data/galbot/lib",
    "LD_LIBRARY_PATH": "/data/galbot/lib:/home/galbot/.local/lib",
    "PATH": "/data/galbot/lib/galbot_sdk/bin",
}

# ===== 进程注册表 (我们启动的) =====
# {name: {"proc": Popen, "started_at": float, "started_at_iso": str, "log_path": Path}}
processes = {}
processes_lock = threading.Lock()


# ---------- 工具 ----------
def _local_ipv4_addrs() -> list:
    """枚举本机所有非回环 IPv4 地址 (跨平台, 无外部依赖).

    Returns: [(iface_name, ip), ...]
    """
    found = []
    try:
        import fcntl  # type: ignore
        import struct
        import array
        SIOCGIFCONF = 0x8912  # linux
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            max_bytes = 64 * 1024
            names = array.array('B', b'\0' * max_bytes)
            sock_fileno = s.fileno()
            size_info = fcntl.ioctl(sock_fileno, SIOCGIFCONF,
                                    struct.pack('iL', max_bytes, names.buffer_info()[0]))
            size = struct.unpack('iL', size_info)[0]
            nbytes = 0
            buf = names.tobytes()
            idx = 0
            while nbytes < size:
                # struct ifreq { char ifr_name[16]; ...; struct sockaddr_in ifr_addr; ... }
                name = buf[idx:idx + 16].split(b'\0', 1)[0].decode()
                # sockaddr_in starts after 16 bytes name + 8 bytes padding on linux
                ip_bytes = buf[idx + 20:idx + 24]
                ip = socket.inet_ntoa(ip_bytes)
                if not ip.startswith("127.") and ip not in [a for _, a in found]:
                    found.append((name, ip))
                # struct ifreq size on linux = 40 bytes for AF_INET
                idx += 40
                nbytes += 40
        finally:
            s.close()
    except Exception:
        pass

    if found:
        return found

    # 回退: 启发式拿一个地址
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return [("default", sock.getsockname()[0])]
    except OSError:
        return [("lo", "127.0.0.1")]


def get_all_access_hosts() -> list:
    """返回本机所有可访问地址列表 [(iface, ip), ...]. 跳过 127.* 回环."""
    addrs = _local_ipv4_addrs()
    return [ip for (iface, ip) in addrs if not ip.startswith("127.")]


def get_access_host() -> str:
    """向后兼容: 返回第一个非回环地址, 没有时退回 127.0.0.1."""
    addrs = get_all_access_hosts()
    return addrs[0] if addrs else "127.0.0.1"


def frontend_url() -> str:
    return "http://{}:{}".format(get_access_host(), HTTP_PORT)


def frontend_urls() -> list:
    """列出所有可访问的 URL (按接口名), 用于 LAN 用户访问提示."""
    return [("http://{}:{}".format(ip, HTTP_PORT), iface) for (iface, ip) in [(i, ip) for i, ip in _local_ipv4_addrs()]]


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    line = "[{}] {}".format(now_iso(), msg)
    print(line, flush=True)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with (LOGS_DIR / "server.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ---------- 机器人状态探测 ----------
def tcp_probe(host: str, port: int, timeout: float = TCP_PROBE_TIMEOUT) -> dict:
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return {"ok": True, "latency_ms": round((time.time() - t0) * 1000, 1)}
    except (OSError, socket.timeout) as e:
        return {"ok": False, "error": str(e)}


def systemctl_is_active(unit: str) -> dict:
    """查询 systemd unit 状态, 不抛异常."""
    try:
        out = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=3,
        )
        state = out.stdout.strip()
        return {"active": out.returncode == 0 and state == "active", "state": state}
    except FileNotFoundError:
        return {"active": None, "error": "systemctl not found"}
    except Exception as e:
        return {"active": None, "error": str(e)}


def probe_robot() -> dict:
    """汇总机器人控制栈在线状态."""
    sources = {}

    # systemd services
    for unit in ("galbot_daemon.service", "galbot_svc_hpu_comm.service"):
        sources[unit] = systemctl_is_active(unit)

    # service_motion_plan 进程 (通常长驻)
    try:
        out = subprocess.run(
            ["pgrep", "-f", "service_motion_plan"],
            capture_output=True, text=True, timeout=3,
        )
        pids = [int(x) for x in out.stdout.split() if x.isdigit()]
        sources["service_motion_plan"] = {"running": bool(pids), "pids": pids}
    except Exception as e:
        sources["service_motion_plan"] = {"running": False, "error": str(e)}

    # TCP 控制端口
    tcp_results = {str(p): tcp_probe(ROBOT_CONTROL_HOST, p) for p in ROBOT_CONTROL_PORTS}
    sources["tcp_control"] = tcp_results

    tcp_ok = any(r["ok"] for r in tcp_results.values())
    motion_ok = sources["service_motion_plan"].get("running", False)
    daemon_ok = sources["galbot_daemon.service"].get("active") is True
    overall = tcp_ok and motion_ok and daemon_ok

    return {
        "overall_ok": overall,
        "checked_at": now_iso(),
        "control_host": ROBOT_CONTROL_HOST,
        "tcp_ok": tcp_ok,
        "motion_ok": motion_ok,
        "daemon_ok": daemon_ok,
        "sources": sources,
    }


# ---------- 进程感知 ----------
def detect_external_scripts() -> list:
    """检测不是我们启动、但当前正在跑的 piano 相关进程."""
    out = subprocess.run(
        ["pgrep", "-af", r"loop_player\.py|molihua_hand_move\.py|farewell\.py|backlighting\.py"],
        capture_output=True, text=True, timeout=3,
    )
    rows = []
    with processes_lock:
        owned_pids = {e["proc"].pid for e in processes.values()}
    for line in out.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        cmd = parts[1]
        if pid in owned_pids:
            continue
        name = None
        for n in SCRIPT_BY_NAME:
            if n in cmd:
                name = n
                break
        rows.append({"pid": pid, "cmd": cmd, "script": name, "owned": False})
    return rows


def list_runs() -> list:
    rows = []
    with processes_lock:
        names = list(processes.keys())
    for name in names:
        with processes_lock:
            entry = processes.get(name)
        if not entry:
            continue
        proc = entry["proc"]
        rc = proc.poll()
        rows.append({
            "name": name,
            "pid": proc.pid,
            "started_at": entry["started_at_iso"],
            "log_path": str(entry["log_path"]),
            "running": rc is None,
            "returncode": rc,
        })
    return rows


def start_script(name: str, extra_args, play_count=None) -> dict:
    """启动一个钢琴脚本子进程。

    参数:
      name: 脚本名 (SCRIPT_BY_NAME 里注册的 key, 如 'molihua_hand_move.py')
      extra_args: 透传给子进程的额外参数列表
      play_count: 重复次数 (loop_player 用 --rounds, 单曲用 --repeat 经 wrapper 注入)
    """
    spec = SCRIPT_BY_NAME.get(name)
    if not spec:
        return {"ok": False, "error": "未知脚本: " + str(name)}
    if not isinstance(extra_args, list):
        return {"ok": False, "error": "args 必须是列表"}

    # 校验 play_count
    pc_arg = spec.get("play_count_arg")
    if play_count is not None:
        try:
            play_count = int(play_count)
        except (TypeError, ValueError):
            return {"ok": False, "error": "play_count 必须是整数"}
        pc_min = spec.get("play_count_min", 1)
        pc_max = spec.get("play_count_max", 99)
        if play_count < pc_min or play_count > pc_max:
            return {"ok": False, "error": "play_count 必须在 {}-{} 之间".format(pc_min, pc_max)}

    # 决定实际要执行的命令:
    #   - 若脚本有 "wrapper" 配置, 用 wrapper 包装执行, 注入 --script/--repeat
    #   - 否则直接执行, 可选地附加 play_count_arg N
    wrapper = spec.get("wrapper")
    if wrapper:
        if not (PIANO_DIR / name).is_file():
            return {"ok": False, "error": "原脚本不存在: " + str(PIANO_DIR / name)}
        cmd = [sys.executable, "-u", str(PIANO_DIR / wrapper)]
        cmd.extend(["--script", name])
        if play_count is not None and pc_arg:
            cmd.extend([pc_arg, str(play_count)])
        cmd.extend(list(extra_args))
    else:
        if not (PIANO_DIR / name).is_file():
            return {"ok": False, "error": "脚本不存在: " + str(PIANO_DIR / name)}
        cmd = [sys.executable, "-u", str(PIANO_DIR / name)]
        if play_count is not None and pc_arg:
            cmd.extend([pc_arg, str(play_count)])
        cmd.extend(list(extra_args))

    # 1) 同名已经在跑
    with processes_lock:
        existing = processes.get(name)
        if existing and existing["proc"].poll() is None:
            return {"ok": False, "error": name + " 已经在运行 (PID " + str(existing["proc"].pid) + ")"}

    # 2) 我们启动的互斥组冲突
    with processes_lock:
        exclusive_groups = set()
        for n, e in processes.items():
            if e["proc"].poll() is None:
                exclusive_groups.update(SCRIPT_BY_NAME[n]["exclusive_with"])
        if spec["group"] in exclusive_groups:
            return {"ok": False, "error": spec["label"] + " 与其它正在运行的脚本互斥；请先停止"}

    # 3) 外部进程占用 -> 阻止 (避免双占用机器人)
    external = detect_external_scripts()
    if external and spec["group"] in {"playlist", "score"}:
        names = ", ".join(r["script"] or ("PID " + str(r["pid"])) for r in external)
        return {"ok": False, "error": "外部已在运行: " + names + "; 请先停止再启动"}

    # 4) 启动
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    # 用脚本 basename 作为日志文件名, 避免 "Tools/reset.py" 走成子目录
    log_path = LOGS_DIR / (Path(name).name + ".log")
    log_path.write_text("", encoding="utf-8")
    log("启动: " + " ".join(cmd) + " (cwd=" + str(PIANO_DIR) + ")")
    log_fh = log_path.open("a", encoding="utf-8", buffering=1)

    # 注入 SDK 环境变量 (PYTHONPATH / LD_LIBRARY_PATH / PATH)
    child_env = os.environ.copy()
    for _k, _v in SDK_ENV.items():
        _cur = child_env.get(_k, "")
        child_env[_k] = _v + (os.pathsep + _cur if _cur else "")

    proc = subprocess.Popen(
        cmd,
        cwd=str(PIANO_DIR),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=child_env,
    )

    # 立刻打印一下生效的 SDK 环境到日志, 方便排错
    log("[env] PYTHONPATH=" + child_env.get("PYTHONPATH", ""))
    log("[env] LD_LIBRARY_PATH=" + child_env.get("LD_LIBRARY_PATH", ""))

    with processes_lock:
        processes[name] = {
            "proc": proc,
            "started_at": time.time(),
            "started_at_iso": now_iso(),
            "log_path": log_path,
        }
    return {"ok": True, "pid": proc.pid, "log_path": str(log_path), "cmd": cmd}


def stop_script(name: str) -> dict:
    with processes_lock:
        entry = processes.get(name)
    if not entry:
        return {"ok": False, "error": "我们没有启动 " + name}
    proc = entry["proc"]
    if proc.poll() is not None:
        return {"ok": False, "error": "{} 已经结束 (rc={})".format(name, proc.returncode)}
    pid = proc.pid
    log("停止: " + name + " (PID " + str(pid) + ")")
    for sig, timeout in ((signal.SIGINT, 2.0), (signal.SIGTERM, 2.0), (signal.SIGKILL, 0.0)):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return {"ok": True, "pid": pid, "signal": signal.Signals(sig).name}
        if timeout <= 0:
            break
        try:
            proc.wait(timeout=timeout)
            return {"ok": True, "pid": pid, "signal": signal.Signals(sig).name}
        except subprocess.TimeoutExpired:
            continue
    return {"ok": True, "pid": pid, "signal": "SIGKILL (信号已发送, 等待 OS 清理)"}


def stop_all() -> list:
    with processes_lock:
        names = list(processes.keys())
    out = []
    for n in names:
        out.append({"name": n, **stop_script(n)})
    return out


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默 BaseHTTPRequestHandler 默认访问日志

    # ===== CORS (跨域), 让网页能在切换服务器 IP 时跨源 fetch =====
    # 这是个局域网调试工具, 用 * 允许任意来源; 如需限制改为具体 origin.
    def end_headers(self):
        origin = self.headers.get("Origin")
        self.send_header("Access-Control-Allow-Origin", origin if origin else "*")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        super().end_headers()

    def do_OPTIONS(self):
        # CORS 预检
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()


    def _send_json(self, code, payload):
        body = json.dumps(_to_jsonable(payload), ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type="text/html; charset=utf-8"):
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, str(path.name) + " not found")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(n).decode("utf-8") if n else ""
            return json.loads(raw) if raw.strip() else {}
        except Exception:
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        try:
            return self._do_GET_impl(u)
        except Exception as e:
            log("do_GET error: " + repr(e))
            try:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "internal: " + str(e)})
            except Exception:
                pass

    def _do_GET_impl(self, u):
        if u.path in ("/", "/index.html"):
            return self._send_file(STATIC_DIR / "index.html")
        if u.path == "/api/status":
            return self._send_json(HTTPStatus.OK, {
                "server": {
                    "now": now_iso(),
                    "frontend_url": frontend_url(),
                    "access_urls": frontend_urls(),
                    "piano_dir": str(PIANO_DIR),
                    "log_dir": str(LOGS_DIR),
                    "pid": os.getpid(),
                },
                "robot": probe_robot(),
                "runs": list_runs(),
                "external": detect_external_scripts(),
                "scripts": SCRIPTS,
            })
        if u.path.startswith("/api/logs/"):
            name = u.path[len("/api/logs/"):]
            if name not in SCRIPT_BY_NAME:
                return self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown script"})
            qs = parse_qs(u.query)
            try:
                since = int(qs.get("since", ["0"])[0] or "0")
            except ValueError:
                since = 0
            try:
                limit = int(qs.get("limit", ["2000"])[0] or "2000")
            except ValueError:
                limit = 2000
            with processes_lock:
                entry = processes.get(name)
            if not entry:
                return self._send_json(HTTPStatus.OK, {
                    "name": name, "running": False,
                    "lines": [], "next_since": 0, "truncated": False, "total_bytes": 0,
                })
            log_path = entry["log_path"]
            try:
                data = log_path.read_bytes()
            except FileNotFoundError:
                data = b""
            total = len(data)
            truncated = False
            max_bytes = 200000  # 单次最多 200KB
            since = max(0, since)
            if since >= total:
                new_data = b""
            elif total - since > max_bytes:
                truncated = True
                start = total - max_bytes
                new_data = data[start:]
            else:
                new_data = data[since:]
            text = new_data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            if 0 < limit < len(lines):
                truncated = True
                lines = lines[-limit:]
            return self._send_json(HTTPStatus.OK, {
                "name": name,
                "running": entry["proc"].poll() is None,
                "lines": lines,
                "next_since": total,
                "truncated": truncated,
                "total_bytes": total,
            })
        if u.path.startswith("/static/"):
            rel = u.path[len("/static/"):]
            target = (STATIC_DIR / rel).resolve()
            try:
                STATIC_DIR.resolve().relative_to(STATIC_DIR.resolve())  # sanity
            except Exception:
                pass
            if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
                return self.send_error(HTTPStatus.FORBIDDEN)
            return self._send_file(target, content_type=_guess_mime(rel))
        return self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            return self._do_POST_impl(u)
        except Exception as e:
            log("do_POST error: " + repr(e))
            try:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "internal: " + str(e)})
            except Exception:
                pass

    def _do_POST_impl(self, u):
        data = self._read_body()
        if u.path == "/api/run":
            name = data.get("name", "")
            extra = data.get("args", []) or []
            count = data.get("count")  # 每首歌的重复次数 (可选)
            res = start_script(name,
                               extra if isinstance(extra, list) else [],
                               play_count=count)
            return self._send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.CONFLICT, res)
        if u.path == "/api/stop":
            name = data.get("name", "")
            if not name:
                return self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing name"})
            res = stop_script(name)
            return self._send_json(HTTPStatus.OK if res.get("ok") else HTTPStatus.CONFLICT, res)
        if u.path == "/api/stop_all":
            return self._send_json(HTTPStatus.OK, {"results": stop_all()})
        return self.send_error(HTTPStatus.NOT_FOUND)


def _to_jsonable(obj):
    """递归把 set 变成 sorted list, 让 json.dumps 不抛 TypeError."""
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _guess_mime(rel: str) -> str:
    rel = rel.lower()
    if rel.endswith(".css"): return "text/css; charset=utf-8"
    if rel.endswith(".js"): return "application/javascript; charset=utf-8"
    if rel.endswith(".svg"): return "image/svg+xml"
    if rel.endswith(".png"): return "image/png"
    if rel.endswith(".jpg") or rel.endswith(".jpeg"): return "image/jpeg"
    if rel.endswith(".ico"): return "image/x-icon"
    return "application/octet-stream"


# ---------- 入口 ----------
def main():
    p = argparse.ArgumentParser(description="QZ Piano 网页遥控器")
    p.add_argument("--host", default=HTTP_HOST)
    p.add_argument("--port", type=int, default=HTTP_PORT)
    args = p.parse_args()

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # 绑定 + 冲突检测
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        if getattr(e, "errno", None) == 98 or "Address already in use" in str(e):
            # 找出占着端口的进程, 给出明确指引
            holder_pid = None
            try:
                # 直接 ss, 不经 bash -lc (之前用 bash 导致 str(args.port) 没被 Python 替换)
                holder_pid = None
                try:
                    ss_out = subprocess.run(
                        ["ss", "-tlnp"],
                        capture_output=True, text=True, timeout=5,
                    )
                    for ln in ss_out.stdout.splitlines():
                        if ":" + str(args.port) + " " not in ln:
                            continue
                        m = re.search(r"pid=(\d+)", ln)
                        if m:
                            holder_pid = int(m.group(1))
                            break
                except Exception:
                    pass
            except Exception:
                pass

            msg = (
                "端口 {} 被占用 ({}).".format(args.port, e)
                + (" 占用进程 PID={}.".format(holder_pid) if holder_pid else "")
                + "\n  释放方法 (任选其一):"
                + "\n    1) kill -INT <pid>      # 优雅停 (会先停本控制器的子进程)"
                + "\n    2) kill -KILL <pid>     # 强杀"
                + "\n    3) python3 server.py --port 9090  # 换端口"
            )
            print("\n[启动失败] " + msg + "\n", flush=True)
            raise SystemExit(1)
        raise

    log("=" * 60)
    log("QZ Piano Web 已启动")
    log("前端: " + frontend_url())
    log("钢琴脚本目录: " + str(PIANO_DIR))
    log("日志目录: " + str(LOGS_DIR))
    log("停止: Ctrl+C (会先发送 SIGINT 给所有子进程)")
    log("=" * 60)

    def _shutdown(sig, frame):
        log("收到 " + signal.Signals(sig).name + ", 正在停止子进程...")
        stop_all()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        server.serve_forever()
    finally:
        server.server_close()
        log("已退出")


if __name__ == "__main__":
    main()
