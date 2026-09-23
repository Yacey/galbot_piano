#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复位脚本：先把双臂移动到 FIRST_POSITION (与三首钢琴脚本一致),
然后调用 motion.move_whole_body_joint_zero() 让全身姿态归零。

参考:
  - /userdata/cx/tutorials/galbot_piano/Tools/set_whole_joints_to_zero.py
  - /userdata/cx/QZ_Piano/main_code/{backlighting,farewell,molihua_hand_move}.py
    的 FIRST_POSITION_LEFT/RIGHT 与 move_hand_to 实现

启动:
    cd /userdata/cx/QZ_Piano/main_code
    python3 Tools/reset_to_initial.py [--speed 0.5] [--wait 5.0]
                              [--no-first-position] [--no-whole-body-zero]

设计要点
--------
  - 不复用钢琴脚本里 FIRST_POSITION 之外的任何逻辑 (播放 / 重置灵巧手 / 路径规划)
  - 复位前可选择先到 FIRST_POSITION (默认开启), 也可只做全身归零
  - 失败立即抛出, 由 web 服务端把异常打到日志并显示在网页
  - finally 强制释放 SDK 资源, Ctrl+C 也安全
"""
from __future__ import annotations

import argparse
import time

from galbot_sdk.g1 import (
    GalbotMotion,
    GalbotRobot,
    G1JointGroup,
    ControlStatus,
    MotionStatus,
)

# ====== 与三首钢琴脚本 FIRST_POSITION 完全一致 ======
# 末端位姿: [x, y, z, qx, qy, qz, qw]
FIRST_POSITION_LEFT = [
    0.4806415066506866, 0.4077152635089186, 0.9535732583692513,
    -0.005149318291046016, 0.04899123823789116,
    -0.012803810725276237, 0.9987038627781345,
]

FIRST_POSITION_RIGHT = [
    0.4897794908823929, -0.25784188082984283, 0.9507047540455765,
    -0.018596974853149324, 0.04110136617151251,
    0.03533006709425718, 0.9983569584994446,
]

# ====== 默认参数 ======
DEFAULT_SPEED_RAD_S = 0.5           # 关节速度 (rad/s)
DEFAULT_FIRST_POSITION_WAIT = 5.0    # FIRST_POSITION 到位后稳定等待 (秒)
DEFAULT_JOINT_ZERO_WAIT = 5.0        # 全身归零后等待 (秒)
MOTION_STARTUP_DELAY = 2.0          # motion 服务初始化后等待 (秒)


def move_arm_to_pose(motion, robot, target_pose, joint_group,
                     speed_rad_s, is_blocking):
    """IK + set_joint_positions 模式 (与 backlighting.py 的 move_hand_to 一致).

    Returns:
        True  = 成功
        False = 失败 (已打印原因)
    """
    status, positions = motion.inverse_kinematics(target_pose, [joint_group])
    if status != MotionStatus.SUCCESS:
        print(f"[IK] {joint_group} 逆运动学失败: {status}")
        return False
    joint_positions = positions[joint_group]
    status = robot.set_joint_positions(
        joint_positions=joint_positions,
        joint_groups=[joint_group],
        speed_rad_s=speed_rad_s,
        is_blocking=is_blocking,
    )
    if status != ControlStatus.SUCCESS:
        print(f"[移位] {joint_group} set_joint_positions 失败: {status}")
        return False
    return True


def main():
    p = argparse.ArgumentParser(description="双臂到 FIRST_POSITION 后全身归零")
    p.add_argument("--speed", type=float, default=DEFAULT_SPEED_RAD_S,
                   help="关节速度 (rad/s), 默认 {}".format(DEFAULT_SPEED_RAD_S))
    p.add_argument("--wait", type=float, default=DEFAULT_FIRST_POSITION_WAIT,
                   help="FIRST_POSITION 到位后稳定等待秒数, 默认 {}".format(DEFAULT_FIRST_POSITION_WAIT))
    p.add_argument("--no-first-position", action="store_true",
                   help="跳过 FIRST_POSITION, 直接执行全身归零")
    p.add_argument("--no-whole-body-zero", action="store_true",
                   help="只移到 FIRST_POSITION, 不执行全身归零")
    args = p.parse_args()

    robot = GalbotRobot()
    robot.init()
    motion = GalbotMotion()
    motion.init()
    time.sleep(MOTION_STARTUP_DELAY)
    print("[初始化] SDK 完成, motion 等待 {:.1f}s 稳定".format(MOTION_STARTUP_DELAY))

    exit_code = 0
    try:
        if not args.no_first_position:
            print("[步骤 1/2] 双臂移到 FIRST_POSITION (速度 {} rad/s) ...".format(args.speed))
            ok_l = move_arm_to_pose(
                motion, robot,
                list(FIRST_POSITION_LEFT), G1JointGroup.left_arm,
                args.speed, is_blocking=True,
            )
            ok_r = move_arm_to_pose(
                motion, robot,
                list(FIRST_POSITION_RIGHT), G1JointGroup.right_arm,
                args.speed, is_blocking=True,
            )
            if not (ok_l and ok_r):
                raise RuntimeError("双臂未到达 FIRST_POSITION, 中止")
            print("[步骤 1/2] 等待 {:.1f}s 让手臂稳定".format(args.wait))
            time.sleep(args.wait)

        if not args.no_whole_body_zero:
            print("[步骤 2/2] motion.move_whole_body_joint_zero() ...")
            status = motion.move_whole_body_joint_zero()
            print("[步骤 2/2] status:", status)
            if status != MotionStatus.SUCCESS:
                raise RuntimeError("全身归零失败: {}".format(status))
            print("[步骤 2/2] 等待 {:.1f}s".format(DEFAULT_JOINT_ZERO_WAIT))
            time.sleep(DEFAULT_JOINT_ZERO_WAIT)

        print("[完成] 复位流程成功")
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
            print("[清理] SDK 资源已释放")
        except Exception as e:
            print("[清理] 异常: {}".format(e))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
