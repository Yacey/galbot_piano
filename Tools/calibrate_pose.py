#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基础弹奏位姿调校：把双手移到 FIRST_POSITION，再到 ORIGINAL 等待用户校准，最后归零。

完整流程：
  1. 双手移到 FIRST_POSITION（IK + set_joint_positions blocking）
  2. 双手移到 ORIGINAL 弹奏位姿（IK + set_joint_positions blocking）
  3. 等待 N 秒（用户校准琴键位置 / 检查手姿）
  4. 双手回到 FIRST_POSITION（blocking）
  5. 调用 motion.move_whole_body_joint_zero() 全身归零

参考：
  - /userdata/cx/QZ_Piano/main_code/backlighting.py 中的
    FIRST_POSITION_LEFT/RIGHT 与 ORIGINAL_LEFT/RIGHT
  - /userdata/cx/QZ_Piano/galbot_piano/Tools/set_whole_joints_to_zero.py
    中的 motion.move_whole_body_joint_zero() 调用方式
  - /userdata/cx/QZ_Piano/main_code/Tools/reset_to_initial.py 的 IK + set_joint_positions

启动:
  cd /userdata/cx/QZ_Piano/main_code
  python3 Tools/calibrate_pose.py [--wait N] [--speed 0.5]

安全
----
  - 不移动腿 / 头部 / 腰部（只动双臂 + 最终全身归零）
  - 等用户超时后才归零，期间可 Ctrl+C 随时中止（finally 块强制释放 SDK）
  - 不与钢琴脚本 / loop_player 互斥（虽然共用机器人，但用户主动调校时希望独占；
    网页侧 exclusive_with 决定）
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

# ===== 位姿常量 (与 backlighting.py 完全一致) =====
# FIRST_POSITION: 调校起始 / 结束归位位姿 (两臂已抬起并接近琴键)
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

# ORIGINAL: 弹奏位姿 (手掌指尖落在琴键上)
ORIGINAL_LEFT = [
    0.5236415066506866, 0.1427152635089186, 0.7435732583692513,
    -0.005149318291046016, 0.04899123823789116, -0.012803810725276237,
    0.9987038627781345,
]

ORIGINAL_RIGHT = [
    0.5187794908823929, -0.04884188082984283, 0.7447047540455765,
    -0.018596974853149324, 0.04110136617151251, 0.03533006709425718,
    0.9983569584994446,
]

# ===== 默认参数 =====
DEFAULT_WAIT_SECONDS = 10.0
DEFAULT_SPEED_RAD_S = 0.5
MOTION_STARTUP_DELAY = 2.0      # motion 初始化后等待
JOINT_ZERO_WAIT = 5.0          # 全身归零后等待


def move_arm_to_pose(motion, robot, target_pose, joint_group, speed_rad_s):
    """IK + set_joint_positions 阻塞式移动单手到目标位姿.

    Returns:
        True  成功
        False 失败 (已打印原因)
    """
    status, positions = motion.inverse_kinematics(target_pose, [joint_group])
    if status != MotionStatus.SUCCESS:
        print("[IK] {} 逆运动学失败: {}".format(joint_group, status))
        return False
    joint_positions = positions[joint_group]
    status = robot.set_joint_positions(
        joint_positions=joint_positions,
        joint_groups=[joint_group],
        speed_rad_s=speed_rad_s,
        is_blocking=True,
    )
    if status != ControlStatus.SUCCESS:
        print("[移位] {} set_joint_positions 失败: {}".format(joint_group, status))
        return False
    return True


def main():
    p = argparse.ArgumentParser(description="基础弹奏位姿调校")
    p.add_argument("--wait", type=float, default=DEFAULT_WAIT_SECONDS,
                   help="ORIGINAL 位姿后等待秒数 (用户调整琴键时间), 默认 {}".format(DEFAULT_WAIT_SECONDS))
    p.add_argument("--speed", type=float, default=DEFAULT_SPEED_RAD_S,
                   help="关节速度 (rad/s), 默认 {}".format(DEFAULT_SPEED_RAD_S))
    args = p.parse_args()

    robot = GalbotRobot()
    robot.init()
    motion = GalbotMotion()
    motion.init()
    print("[初始化] SDK 完成, 等待 {:.1f}s 让 motion 就绪".format(MOTION_STARTUP_DELAY))
    time.sleep(MOTION_STARTUP_DELAY)

    exit_code = 0
    try:
        # 步骤 1: 双手 → FIRST_POSITION
        print("\n[步骤 1/5] 双手 → FIRST_POSITION ...")
        ok_l = move_arm_to_pose(motion, robot, list(FIRST_POSITION_LEFT),
                                G1JointGroup.left_arm, args.speed)
        ok_r = move_arm_to_pose(motion, robot, list(FIRST_POSITION_RIGHT),
                                G1JointGroup.right_arm, args.speed)
        if not (ok_l and ok_r):
            raise RuntimeError("双臂未到达 FIRST_POSITION, 中止")

        # 步骤 2: 双手 → ORIGINAL (弹奏位姿)
        print("\n[步骤 2/5] 双手 → ORIGINAL 弹奏位姿 ...")
        ok_l = move_arm_to_pose(motion, robot, list(ORIGINAL_LEFT),
                                G1JointGroup.left_arm, args.speed)
        ok_r = move_arm_to_pose(motion, robot, list(ORIGINAL_RIGHT),
                                G1JointGroup.right_arm, args.speed)
        if not (ok_l and ok_r):
            raise RuntimeError("双臂未到达 ORIGINAL, 中止")

        # 步骤 3: 等待用户校准
        print("\n[步骤 3/5] 等待 {:.1f}s (可 Ctrl+C 中止)...".format(args.wait))
        end_at = time.time() + args.wait
        remaining = args.wait
        while remaining > 0:
            time.sleep(min(1.0, remaining))
            remaining = end_at - time.time()
            if remaining > 0:
                print("  ...剩余 {:.1f}s".format(remaining), flush=True)
        print("[步骤 3/5] 等待结束")

        # 步骤 4: 双手 → FIRST_POSITION (为归零准备)
        print("\n[步骤 4/5] 双手 → FIRST_POSITION ...")
        ok_l = move_arm_to_pose(motion, robot, list(FIRST_POSITION_LEFT),
                                G1JointGroup.left_arm, args.speed)
        ok_r = move_arm_to_pose(motion, robot, list(FIRST_POSITION_RIGHT),
                                G1JointGroup.right_arm, args.speed)
        if not (ok_l and ok_r):
            raise RuntimeError("双臂未回到 FIRST_POSITION, 中止")

        # 步骤 5: 全身归零
        print("\n[步骤 5/5] motion.move_whole_body_joint_zero() ...")
        status = motion.move_whole_body_joint_zero()
        print("[步骤 5/5] status:", status)
        if status != MotionStatus.SUCCESS:
            raise RuntimeError("全身归零失败: {}".format(status))
        print("[步骤 5/5] 等待 {:.1f}s 让归零完成".format(JOINT_ZERO_WAIT))
        time.sleep(JOINT_ZERO_WAIT)

        print("\n[完成] 基础弹奏位姿调校流程全部成功")
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
