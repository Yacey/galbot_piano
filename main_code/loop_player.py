# -*- coding: utf-8 -*-
"""循环播放编排器：依次调用本目录下（同目录）的弹曲脚本。

设计目标：
  - 顺序执行曲子（同一时间只有一首曲子占用机器人）。
  - 每首曲子作为独立子进程运行，自带 init/destroy，互不污染全局状态。
  - 三层循环次数均可独立调整：
        L1  子脚本内 SCORE 循环（修改子脚本里 PLAYBACK_REPEAT_COUNT）
        L2  进程级循环（本文件 SCORES 每项的第二个数字）
        L3  歌单级循环（本文件 PLAYLIST_REPEAT_COUNT，<=0 表示无限循环）
  - 追加新曲子：直接在 SCORES 末尾追加一行即可，不需改其它代码。

注意：
  - 子脚本不读取环境变量覆盖；如需调整 L1（每首曲子内 SCORE 重复次数），
    请直接修改对应子脚本顶部的 PLAYBACK_REPEAT_COUNT。
  - 子进程默认 30 分钟超时；防止脚本卡死时编排器永久挂起。
  - 任一首曲子失败（超时/非零退出码）立即终止整张歌单，不静默继续。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

# ===== 路径 =====
# 编排器自身所在目录即为子脚本所在目录；不依赖运行时 cwd
SCRIPT_DIR = Path(__file__).resolve().parent

# ===== 歌单配置（用户主要改这里）=====
# 每项：(脚本名, 进程级循环次数, 标签)
# 追加新曲子：末尾加一行 ("your_script.py", K, "显示名")
SCORES: List[Tuple[str, int, str]] = [
    ("molihua_hand_move.py", 1, "茉莉花"),
    ("farewell.py",          3, "送别"),
    ("backlighting.py",      3, "逆光（实为送别 SCORE）"),
    ("darwin.py",            1, "达尔文"),
]

# 整张歌单重复轮数；<=0 表示无限循环（Ctrl+C 终止）
PLAYLIST_REPEAT_COUNT = 2

# ===== 时间参数 =====
# 同一首曲子两次进程调用之间：给机器人状态留稳定时间
INTER_PROCESS_PAUSE_SECONDS = 5.0
# 上一首曲子结束后到下一首首次调起之间：留给物理归位 / 用户观察
INTER_SCORE_PAUSE_SECONDS = 4.0
# 整张歌单每轮之间的停顿（仅在 PLAYLIST_REPEAT_COUNT > 1 时生效）
INTER_PLAYLIST_PAUSE_SECONDS = 5.0
# 单个子进程最长执行时间（秒）；超时即判定为卡死并强制结束
SUBPROCESS_TIMEOUT_SECONDS = 1800.0  # 30 分钟


def run_subprocess(script_path: Path, label: str) -> bool:
    """启动子进程弹奏指定路径对应的曲子。

    Returns:
        True  = 子进程正常退出（returncode == 0）
        False = 子进程超时或非零退出码
    """
    cmd = [sys.executable, "-u", str(script_path)]
    print(f"[编排] 启动子进程: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(SCRIPT_DIR),
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[编排] X 《{label}》超时 (>{SUBPROCESS_TIMEOUT_SECONDS:.0f}s)，"
            "已强制结束子进程"
        )
        return False
    if proc.returncode != 0:
        print(f"[编排] X 《{label}》退出码 = {proc.returncode}")
        return False
    print(f"[编排] OK 《{label}》本进程正常结束")
    return True


def play_score(script_name: str, rounds: int, label: str) -> bool:
    """连续调起子脚本 rounds 次弹奏同一首曲子。"""
    script_path = SCRIPT_DIR / script_name
    if not script_path.is_file():
        print(f"[编排] X 找不到脚本: {script_path}")
        return False
    for i in range(1, rounds + 1):
        print(f"\n[编排] === 《{label}》 进程调起 {i}/{rounds} ===")
        if not run_subprocess(script_path, label):
            print(f"[编排] X 《{label}》进程 {i}/{rounds} 失败，停止该曲")
            return False
        if i < rounds:
            print(
                f"[编排] 等待 {INTER_PROCESS_PAUSE_SECONDS:g}s "
                f"再进入《{label}》下一次进程..."
            )
            time.sleep(INTER_PROCESS_PAUSE_SECONDS)
    return True


def _run_one_playlist_round(
    playlist: List[Tuple[str, int, str]],
    pl_round: int,
    total_rounds: int,
) -> bool:
    """执行一轮歌单；按曲间停顿参数决定后续等待时长。"""
    last_idx = len(playlist) - 1
    is_last_round = total_rounds > 0 and pl_round >= total_rounds
    for idx, (script_name, rounds, label) in enumerate(playlist):
        if not play_score(script_name, rounds, label):
            print(f"[编排] 在《{label}》处失败，终止整张歌单")
            return False
        # 最后一首且最后一轮：直接结束本轮（不等待）
        if idx == last_idx and is_last_round:
            continue
        if idx == last_idx:
            wait = INTER_PLAYLIST_PAUSE_SECONDS
            print(f"[编排] 本轮歌单结束，等待 {wait:g}s 进入下一轮")
        else:
            wait = INTER_SCORE_PAUSE_SECONDS
            print(f"[编排] 切换下一首，等待 {wait:g}s")
        time.sleep(wait)
    return True


def play_playlist(
    playlist: List[Tuple[str, int, str]], total_rounds: int
) -> bool:
    """按 playlist 顺序播放整张歌单，重复 total_rounds 轮（<=0 = 无限）。"""
    pl = 0
    while True:
        pl += 1
        is_finite = total_rounds > 0
        is_last = is_finite and pl >= total_rounds
        round_label = (
            f"{pl}/{total_rounds}" if is_finite else f"{pl}（无限循环）"
        )
        print(f"\n========== 歌单第 {round_label} 轮 ==========")
        if not _run_one_playlist_round(playlist, pl, total_rounds):
            return False
        if is_last:
            break
    return True


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="循环播放本目录下弹曲脚本（子进程编排）"
    )
    p.add_argument(
        "--rounds", type=int, default=None,
        help="覆盖每首曲子进程级循环次数（不影响歌单顺序）",
    )
    p.add_argument(
        "--playlist", type=int, default=None,
        help="覆盖歌单轮次数；<=0 视为无限循环",
    )
    p.add_argument(
        "--once", action="store_true",
        help="等价 --rounds 1，仅调试时使用",
    )
    p.add_argument(
        "--list", action="store_true", dest="list_only",
        help="仅打印当前歌单与配置，不实际启动任何子进程",
    )
    return p.parse_args()


def build_playlist(
    args_rounds, args_playlist, args_once
) -> Tuple[List[Tuple[str, int, str]], int]:
    """根据命令行覆盖与静态配置计算最终生效的 playlist 与轮次数。"""
    rounds_override = args_rounds
    if args_once:
        rounds_override = 1

    playlist: List[Tuple[str, int, str]] = []
    for s, r, lbl in SCORES:
        rr = rounds_override if rounds_override is not None else r
        playlist.append((s, rr, lbl))

    total_rounds = (
        args_playlist if args_playlist is not None else PLAYLIST_REPEAT_COUNT
    )
    return playlist, total_rounds


def print_config(playlist, total_rounds):
    print("=" * 60)
    print("[编排] 当前歌单：")
    for i, (s, r, lbl) in enumerate(playlist, 1):
        print(f"  {i}. {lbl}  ({s})  进程级循环={r}")
    print(f"[编排] 歌单总轮数: {total_rounds}（<=0 表示无限循环）")
    print(
        f"[编排] 停顿: 曲内进程间={INTER_PROCESS_PAUSE_SECONDS:g}s, "
        f"曲间={INTER_SCORE_PAUSE_SECONDS:g}s, "
        f"轮间={INTER_PLAYLIST_PAUSE_SECONDS:g}s"
    )
    print(f"[编排] 单进程超时: {SUBPROCESS_TIMEOUT_SECONDS:.0f}s")
    print("=" * 60)


def main() -> int:
    args = parse_args()
    playlist, total_rounds = build_playlist(
        args.rounds, args.playlist, args.once
    )
    print_config(playlist, total_rounds)

    if args.list_only:
        return 0

    try:
        play_playlist(playlist, total_rounds)
    except KeyboardInterrupt:
        print("\n[编排] 检测到 Ctrl+C，退出编排器")
        return 130

    print("\n[编排] OK 整张歌单循环结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())