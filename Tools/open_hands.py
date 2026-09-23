#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手指展开：把双手灵巧手重置为张开状态。

  - 拇指旋转 (Joint 1) = 0
  - 其他所有关节 (Joint 2..6, 即拇弯曲 + 食/中/无名/小) = 1000

参考: /userdata/cx/QZ_Piano/main_code/backlighting.py 的 reset_hands() 与
       generateDexhandCommands() 实现, 行为完全一致.

启动: cd /userdata/cx/QZ_Piano/main_code
       python3 Tools/open_hands.py [--no-init]

安全
----
  - 不调用 motion, 只下发灵巧手指令
  - 仅占用 ~0.1s, 不需要急停确认
  - 与钢琴脚本 (molihua/farewell/backlighting/loop_player) 互斥:
    弹奏中按下不会立刻响应, 服务会返回 "互斥" 错误
"""
from __future__ import annotations

import argparse
import time

from galbot_sdk.g1 import (
    GalbotRobot,
    ControlStatus,
    JointCommand,
)

# ===== 常量 =====
DEXHAND_LEFT = "left_dexhand"
DEXHAND_RIGHT = "right_dexhand"
DEXHANDS = (DEXHAND_LEFT, DEXHAND_RIGHT)

# Inspire RH56 关节划分 (与 backlighting.py 一致):
#   idx 0 = 拇指旋转 (Joint 1)
#   idx 1 = 拇指弯曲 (Joint 6)
#   idx 2 = 食指 (Joint 2)
#   idx 3 = 中指 (Joint 3)
#   idx 4 = 无名指 (Joint 4)
#   idx 5 = 小指  (Joint 5)
THUMB_ROTATION_OPEN = 0      # 拇指旋转 张开位
FINGER_OPEN = 1000           # 其余所有手指 张开位
JOINTS_PER_HAND = 6

INIT_DELAY = 1.0    # robot.init 后等待灵巧手上电稳定
HOLD_SECONDS = 1.0  # 下发后等待指令生效


def make_open_command():
    """构造一帧 6-DoF "张开" 指令: 拇旋转=0, 其余=1000."""
    cmds = [JointCommand() for _ in range(JOINTS_PER_HAND)]
    cmds[0].position = THUMB_ROTATION_OPEN
    for i in range(1, JOINTS_PER_HAND):
        cmds[i].position = FINGER_OPEN
    return cmds


def hit(robot: GalbotRobot, end_effector: str, commands: list) -> None:
    """下发一帧指令到指定灵巧手 (与钢琴脚本一致: is_blocking=False)."""
    pos = [c.position for c in commands]
    print("[hit] {} positions={}".format(end_effector, pos))
    status = robot.set_dexhand_command(
        end_effector=end_effector,
        dexhand_command=commands,
        is_blocking=False,
    )
    if status != ControlStatus.SUCCESS:
        print("[hit] {} status={}".format(end_effector, status))


def open_hands(robot: GalbotRobot) -> None:
    """双手下发张开指令."""
    cmds = make_open_command()
    for hand in DEXHANDS:
        hit(robot, hand, cmds)


def main():
    p = argparse.ArgumentParser(description="双手灵巧手重置为张开状态")
    p.add_argument("--no-init", action="store_true",
                   help="跳过 robot.init/destroy, 仅打印将下发的指令 (排错用)")
    args = p.parse_args()

    if args.no_init:
        # 不连机器人, 仅打印 (供 UI 验证 / 子进程探活)
        print("[dry-run] 将下发以下指令 (各 6 个关节):")
        for hand in DEXHANDS:
            cmds = make_open_command()
            print("  {}: thumb_rot={}, others={}".format(
                hand, cmds[0].position, [c.position for c in cmds[1:]]))
        return 0

    robot = GalbotRobot()
    robot.init()
    print("[init] 完成, 等待 {:.1f}s 让灵巧手上电稳定".format(INIT_DELAY))
    time.sleep(INIT_DELAY)

    exit_code = 0
    try:
        print("[步骤] 双手下发张开指令 ...")
        open_hands(robot)
        print("[步骤] 等待 {:.1f}s 让指令生效".format(HOLD_SECONDS))
        time.sleep(HOLD_SECONDS)
        print("[完成] 手指已展开")
    except KeyboardInterrupt:
        print("\n[中断] Ctrl+C, 立即清理")
        exit_code = 130
    except Exception as e:
        print("[失败] {}: {}".format(type(e).__name__, e))
        exit_code = 1
    finally:
        try:
            robot.request_shutdown()
            robot.wait_for_shutdown()
            robot.destroy()
            print("[清理] SDK 已释放")
        except Exception as e:
            print("[清理] 异常: {}".format(e))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
