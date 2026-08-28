"""本代码用于：让双手移动到固定位姿后，双手各自朝设定方向平移固定的距离

用法:
    python move-hand-posion.py

输出:
    - 双手会移动到固定好的位姿
    - 位姿移动完毕后，双手各自会向各自设定的方向移动（可以用于测试手移动距离，弹奏前可用）
"""


import time
from typing import List

from galbot_sdk.g1 import (
    G1JointGroup,
    GalbotMotion,
    GalbotRobot,
    MotionStatus,
    Parameter,
    PoseState,
    SensorType,
)


def is_at_pose(current, target, pos_tol=0.005, quat_tol=0.05):
    """判断当前位姿是否与目标位姿一致

    Args:
        current: 当前 7 元素位姿 [x, y, z, qx, qy, qz, qw]
        target: 目标 7 元素位姿
        pos_tol: 位置容差（米），默认 5mm
        quat_tol: 四元数容差，默认 0.05

    Note:
        q 与 -q 代表相同的旋转，所以会同时检查两种符号
    """
    if len(current) != 7 or len(target) != 7:
        return False
    # 位置 (xyz)
    for i in range(3):
        if abs(current[i] - target[i]) > pos_tol:
            return False
    # 四元数 (xyzw)，检查两种符号
    for i in range(3, 7):
        diff_pos = abs(current[i] - target[i])
        diff_neg = abs(current[i] + target[i])
        if diff_pos > quat_tol and diff_neg > quat_tol:
            return False
    return True


def move_hand(motion, hand_pose, joint_group):
    """移动单手到目标位姿

    Args:
        motion: GalbotMotion 实例
        hand_pose: 7 元素位姿 [x, y, z, qx, qy, qz, qw]
        joint_group: G1JointGroup.left_arm 或 G1JointGroup.right_arm
    """
    pose_state = PoseState()
    pose_state.chain_name = joint_group

    params = Parameter()
    params.set_direct_execute(True)
    params.set_move_line(True)  # 末端移动轨迹走直线
    params.is_blocking = False  # 不阻塞

    motion.motion_plan_multi_waypoints(
        {pose_state: [hand_pose]},
        enable_collision_check=False,
        params=params,
    )


def main():
    robot = GalbotRobot()
    robot.init({SensorType.LEFT_ARM_CAMERA, SensorType.LEFT_ARM_DEPTH_CAMERA})
    motion = GalbotMotion()
    motion.init()
    time.sleep(1)

    # 初始位姿（硬编码）
    ORIGINAL_LEFT = [0.5562077698154346, 0.07420021567193136, 0.8814180536631244, -0.013693723044796892, 0.07832387506267267, 0.005747793156086455, 0.996817343056477]
    ORIGINAL_RIGHT =  [0.5671736560309317, -0.12500163431131414, 0.8765803142847286, 0.013786234773956027, 0.05649516908943167, 0.059342328871129335, 0.9965423842488912]
    try:
        # 0. 读取当前左右手末端位姿（仅用于调试打印）
        status, current_left = motion.get_end_effector_pose_on_chain(G1JointGroup.left_arm)
        if status != MotionStatus.SUCCESS:
            raise RuntimeError(f"get left ee pose failed: {status}")
        # status, current_right = motion.get_end_effector_pose_on_chain(G1JointGroup.right_arm)
        # if status != MotionStatus.SUCCESS:
            # raise RuntimeError(f"get right ee pose failed: {status}")
        print(f"读取到位姿:")
        print(f"  左手当前: {current_left}")
        # print(f"  右手当前: {current_right}")

        # 1. 无条件强制移动左右手都到达原始位姿
        print(f"\n无条件移动左右手到达原始位姿:")
        print(f"  左手目标: {ORIGINAL_LEFT}")
        print(f"  右手目标: {ORIGINAL_RIGHT}")
        move_hands2(motion, list(ORIGINAL_LEFT), list(ORIGINAL_RIGHT))
        time.sleep(3)  # 给足够时间完成，确保左右手都到达

        # 1.5 再读一次确认位姿
        status, verified_left = motion.get_end_effector_pose_on_chain(G1JointGroup.left_arm)
        # status, verified_right = motion.get_end_effector_pose_on_chain(G1JointGroup.right_arm)
        print(f"\n移动后确认位姿:")
        print(f"  左手实际: {verified_left}")
        # print(f"  右手实际: {verified_right}")

        # 2. 只移动右手 -Y 5cm（左手不动）
        new_right = list(ORIGINAL_RIGHT)
        new_right[1] -= 0.05  # -Y 5cm
        print(f"\n只移动右手 -Y 10cm（左手不动）:")
        print(f"  左手目标（不动）: {ORIGINAL_LEFT}")
        print(f"  右手目标: {new_right}")
        move_hand(motion, new_right, G1JointGroup.right_arm)
        time.sleep(3)  # 等右手移动完成

        # 3. 等待 3 秒观察
        print(f"\n等待 3 秒观察...")
        time.sleep(3)

        # 4. 只移动左手 +Y 16cm
        new_left = list(ORIGINAL_LEFT)
        new_left[1] += 0.16  # +Y 16cm，手食指对应低8度音do   0.09m 食指对应  
        print(f"\n移动左手 +Y 5cm :")
        move_hand(motion, new_left, G1JointGroup.left_arm)
        time.sleep(3)  # 等右手移动完成

        # 4.5 读取移动后的左手位姿(便于核对定位精度)
        status, left_after = motion.get_end_effector_pose_on_chain(G1JointGroup.left_arm)
        if status != MotionStatus.SUCCESS:
            print(f"  [警告] 读取移动后左手位姿失败: {status}")
        else:
            print(f"\n移动后左手实际位姿:")
            print(f"  目标: {new_left}")
            print(f"  实际: {left_after}")
            # 与目标位姿的位置偏差(米)
            dx = left_after[0] - new_left[0]
            dy = left_after[1] - new_left[1]
            dz = left_after[2] - new_left[2]
            print(f"  位置偏差 (m): dx={dx:+.4f}, dy={dy:+.4f}, dz={dz:+.4f}")


        # 5. 恢复：右手回到原始位姿（左手不动）
        print(f"\n恢复：右手回到原始位姿:")
        move_hand(motion, list(ORIGINAL_LEFT), G1JointGroup.left_arm)
        time.sleep(3)  # 等右手恢复完成
        move_hand(motion, list(ORIGINAL_RIGHT), G1JointGroup.right_arm)
        time.sleep(3)  # 等右手恢复完成

        # 6. 程序退出
        print(f"\n程序退出")

    finally:
        # 资源释放
        robot.request_shutdown()
        robot.wait_for_shutdown()
        robot.destroy()


# 左右手分别移动，设置为非阻塞的话虽然可以两只手几乎同时移动，但是不方便检查status
# def move_hands(motion: GalbotMotion, left_pose: List[float], right_pose: List[float]):
#     status = motion.set_end_effector_pose(
#         left_pose, G1JointGroup.left_arm, params=Parameter()
#     )
#     if status != MotionStatus.SUCCESS:
#         print(f"move left hand failed: {status}")
#     status = motion.set_end_effector_pose(
#         right_pose, G1JointGroup.right_arm, params=Parameter()
#     )
#     if status != MotionStatus.SUCCESS:
#         print(f"move right hand failed: {status}")


def move_hands2(motion: GalbotMotion, left_pose: List[float], right_pose: List[float]):
    left_pose_state = PoseState()
    right_pose_state = PoseState()

    left_pose_state.chain_name = G1JointGroup.left_arm
    right_pose_state.chain_name = G1JointGroup.right_arm

    params = Parameter()
    params.set_direct_execute(True)
    params.set_move_line(True)  # 末端移动轨迹走直线
    params.is_blocking = False  # 不阻塞

    motion.motion_plan_multi_waypoints(
        # 注意这里就只让左右手只经过一个点，实际上就相当于直接到达终点，中间不用走任何地方
        {
            left_pose_state: [left_pose],
            right_pose_state: [right_pose],
        },
        enable_collision_check=False,  # 默认打开的，但是打开的就直接plan失败（笑死，印象中问过银河，明确说不支持（其实关于碰撞的，好多地方都不支持）
        params=params,
    )

    # 阻塞方式执行
    # status, _ = motion.motion_plan_multi_waypoints(
    #     {
    #         left_pose_state: [left_pose],
    #         right_pose_state: [right_pose],
    #     },
    #     enable_collision_check=False,
    #     params=params,
    # )
    # if status != MotionStatus.SUCCESS:
    #     raise RuntimeError(f"move hands failed: {status}")


if __name__ == "__main__":
    main()