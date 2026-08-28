"""获取当前左右手末端位姿,方便硬编码到 move-hand-posion.py 的 ORIGINAL_LEFT/ORIGINAL_RIGHT

用法:
    python get-hand-posion.py

输出:
    - 人类可读格式(位置 + 四元数,标注 xyz / qxqyqzqw)
    - 可直接粘贴到源码的 Python 字面量
    - 同时保存一份 current_pose.json 备用

注意:
    - SDK 文档说 get_end_effector_pose_on_chain "很可能要重试几次才能获取到",
      本脚本默认最多重试 5 次,每次间隔 0.5s
    - 必须在灵巧手已经上电、并能正常读取状态的环境下运行
"""

import json
import time
from typing import List, Optional

from galbot_sdk.g1 import (
    G1JointGroup,
    GalbotMotion,
    GalbotRobot,
    MotionStatus,
    SensorType,
)


def get_pose_with_retry(
    motion: GalbotMotion,
    joint_group,
    max_retry: int = 5,
    sleep_between: float = 0.5,
) -> Optional[List[float]]:
    """重试获取末端位姿(SDK 有时第一次拿不到)"""
    for i in range(max_retry):
        status, pose = motion.get_end_effector_pose_on_chain(joint_group)
        if status == MotionStatus.SUCCESS:
            return list(pose)
        print(f"  [重试 {i + 1}/{max_retry}] 获取 {joint_group} 位姿失败: {status}")
        time.sleep(sleep_between)
    return None


def format_pose_human(pose: List[float], name: str) -> str:
    """格式化位姿为人类可读:位置 + 四元数"""
    x, y, z, qx, qy, qz, qw = pose
    return (
        f"  {name}:\n"
        f"    位置 (m):  x={x:+.6f}, y={y:+.6f}, z={z:+.6f}\n"
        f"    四元数:    qx={qx:+.6f}, qy={qy:+.6f}, qz={qz:+.6f}, qw={qw:+.6f}"
    )


def format_pose_python_literal(pose: List[float], var_name: str) -> str:
    """格式化为 Python 列表字面量,可直接粘贴到代码"""
    elements = ", ".join(f"{v!r}" for v in pose)
    return f"{var_name} = [{elements}]"


def main():
    robot = GalbotRobot()
    robot.init({SensorType.LEFT_ARM_CAMERA, SensorType.LEFT_ARM_DEPTH_CAMERA})
    motion = GalbotMotion()
    motion.init()
    time.sleep(1)

    try:
        print("=" * 70)
        print("获取当前左右手末端位姿")
        print("=" * 70)

        left_pose = get_pose_with_retry(motion, G1JointGroup.left_arm)
        if left_pose is None:
            raise RuntimeError("获取左手位姿失败,达到最大重试次数")
        print(format_pose_human(left_pose, "左手"))
        print()

        right_pose = get_pose_with_retry(motion, G1JointGroup.right_arm)
        if right_pose is None:
            raise RuntimeError("获取右手位姿失败,达到最大重试次数")
        print(format_pose_human(right_pose, "右手"))

        print()
        print("=" * 70)
        print("可直接粘贴到源码的 Python 字面量:")
        print("=" * 70)
        print(format_pose_python_literal(left_pose, "ORIGINAL_LEFT"))
        print(format_pose_python_literal(right_pose, "ORIGINAL_RIGHT"))

        # 同时存一份 JSON 备用
        save_path = "current_pose.json"
        data = {
            "_comment": "由 move-hand-fix-get-posion.py 生成,可直接用 JSON 加载还原",
            "ORIGINAL_LEFT": left_pose,
            "ORIGINAL_RIGHT": right_pose,
        }
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\n[已保存] {save_path}")

    finally:
        robot.request_shutdown()
        robot.wait_for_shutdown()
        robot.destroy()


if __name__ == "__main__":
    main()