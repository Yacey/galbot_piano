# -*- coding: utf-8 -*-
"""灵巧手弹奏《茉莉花》—— 简谱版

旋律：江苏民歌《茉莉花》（何仿改编，祖英演唱）
简谱读法：
  - 数字 1-7  = C-B（首调唱名 do-re-mi...）
  - 数字上加 . = 高八度
  - 数字后 -   = 延音一拍（half note）
  - "0"        = 休止符
  - 连音线/延音线（"⌒"）= 前一个音持续到下一个音

弹奏逻辑与小星星一致，但增加了：
  - 延音线（"X -"）：手指按住 2 拍
  - 连音线（"⌒"）：第一个音的 duration 标记为 True 表示延音一拍
  - 减时线（如 8分音符、16分音符）：用 duration=0.5/0.25 表示半拍/四分之一拍
"""
import json
import math
import sys
import threading
import time
from pathlib import Path
from typing import Sequence

from apscheduler.schedulers.background import BackgroundScheduler
from galbot_sdk.g1 import (
    ControlStatus,
    GalbotMotion,
    GalbotRobot,
    G1JointGroup,
    JointCommand,
    MotionStatus,
    Trajectory,
    TrajectoryPoint,
)


# ===== 调速参数 =====
INTERVAL = 0.7
RESET_DELAY = 0.55

# ===== 循环弹奏与轮次间归零 =====
# 修改 PLAYBACK_REPEAT_COUNT 可设置整首 SCORE 的弹奏次数。
# 每一轮结束后先等待 INTER_ROUND_PAUSE_SECONDS，再把双臂移动到
# FIRST_POSITION_LEFT/RIGHT；ENABLE_WHOLE_BODY_JOINT_ZERO 为 True 时，
# 随后调用 move_whole_body_joint_zero() 让全身姿态归零。
PLAYBACK_REPEAT_COUNT = 1
INTER_ROUND_PAUSE_SECONDS = 3.0
FIRST_POSITION_WAIT_SECONDS = 3.0
ORIGINAL_POSITION_WAIT_SECONDS = 5.0
ENABLE_WHOLE_BODY_JOINT_ZERO = True
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
# 每轮实际弹奏时采用的初始位姿；手掌 stop 位移均以此为基准。
ORIGINAL_LEFT = [
    # y(减少手往后) x(减少往右) z
    0.5236415066506866, 0.1427152635089186, 0.7435732583692513, 
    -0.005149318291046016, 0.04899123823789116, -0.012803810725276237, 
    0.9987038627781345
    ]

ORIGINAL_RIGHT = [
    0.5187794908823929, -0.04884188082984283, 0.7447047540455765, 
    -0.018596974853149324, 0.04110136617151251, 0.03533006709425718, 
    0.9983569584994446
    ]

# 每轮弹奏前先让食指、中指、无名指和小拇指张开至 1000，
# 再开始手臂移动；拇指沿用既有固定控制值。
FINGER_OPEN_WAIT_SECONDS = 0.5

# ===== 手部位姿管理 =====
# 坐标约定沿用原脚本：Y 增大为向左，Y 减小为向右。
# 新初始位姿：
#   左手：食=D4、中=C4、无=B3、小=A3
#   右手：食=B4、中=C5、无=D5、小=E5
LEFT_HAND_OFFSET_RIGHT = -0.047  # 左手向右 5cm：用于 E4/F4
LEFT_HAND_OFFSET_LOW   = 0.020   # 左手向左 2.5cm：用于低八度 A3 -> G3
RIGHT_HAND_OFFSET_LEFT = 0.045   # 右手向左 5cm：用于 G4/A4
HAND_MOVE_SPEED_RAD_S  = 0.40     # 手部移位速度（弧度/秒），0.2 起慢慢加
HAND_MOVE_DELAY        = 0.30     # 速度控制后预计手部到位所需时间

# motion 服务初始化后，第一次读取末端位姿偶尔会返回 DATA_FETCH_FAILED。
# 先等待服务就绪，再进行有限重试；避免瞬态状态未就绪导致整首曲子退出。
MOTION_STARTUP_DELAY = 2.0
POSE_FETCH_MAX_RETRIES = 10
POSE_FETCH_RETRY_INTERVAL = 0.5

# ===== 弹奏时的情感动作 =====
# 头部摇动与双臂外摆分别运行。双臂轨迹会在每次琴键移位前暂停，
# 并在移位后以当前实际关节角重新生成，从而不与弹琴的手掌位移抢关节。
ENABLE_EMOTION_MOVEMENT = True
ENABLE_HEAD_SHAKE = True
# 双臂轨迹与弹琴移位控制同一组手臂关节；未完成实机联调前默认关闭。
# 需要测试双臂外摆时再手动改为 True。
ENABLE_ARM_SWING = False
EMOTION_TRAJECTORY_SECONDS = 180.0  # 覆盖整首已启用 SCORE 的保守时长
HEAD_TRAJECTORY_DT = 0.015    # 表示头部轨迹相邻两帧之间的时间；数值越大，摇头越慢
ARM_SWING_CYCLE_POINTS = 120
ARM_SWING_TRAJECTORY_DT = 1.0 / 24.0  # 120 点约 5 秒，动作较舒缓
# 新目录结构：
#   galbot_piano/main_code/molihua_hand_move.py
#   galbot_piano/sources/{emotion_movement.py, head_positions.json, mjcf/}
# 通过入口脚本自身的位置计算 sources，不能依赖运行时 cwd；这样从任意目录执行
# /userdata/cx/tutorials/galbot_piano/main_code/molihua_hand_move.py 都能找到资源。
# 回退到脚本同级目录是为了兼容旧的单目录部署和本机历史测试目录。
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
_NEW_SOURCES_DIR = PROJECT_ROOT / "sources"
SOURCES_DIR = _NEW_SOURCES_DIR if _NEW_SOURCES_DIR.is_dir() else SCRIPT_DIR
HEAD_POSITIONS_JSON = SOURCES_DIR / "head_positions.json"

# emotion_movement.py 在 sources 中。把该目录放到模块搜索路径首位，确保后台
# 摆臂线程导入的是本项目这一份，而不是 Python 环境里同名的其他模块。
if str(SOURCES_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCES_DIR))


# ===== 音符到关节的映射（新初始弹奏位姿） =====
# 每个 stop 都记录“此时实际能按到”的音。hit_next 会优先留在当前 stop，
# 只有当前手指覆盖不到该音时才移动手掌，避免为回原位而产生听感断裂。
# 位置值沿用既有标定：食=830、中=850、无=830、小=700/600。

# 左手原位：食=D4、中=C4、无=B3、小=A3。
# 左手右移 5cm：食=F4、中=E4、无=D4、小=C4。
# 左手左移 2.5cm：无=A3、小=G3（低八度 6→5 联奏）。
LEFT_HAND_STOPS = {
    "BASE": {
        1: (3, 850),  # C4：中指
        2: (2, 830),  # D4：食指
        11: (5, 700), # A3：小拇指（单独低八度 6）
        12: (5, 830), # B3：无名指（单独低八度 7）
    },
    "RIGHT": {
        1: (5, 750),  # C4：小拇指
        2: (4, 830),  # D4：无名指；Mi 后的 Re 不需回原位
        3: (3, 850),  # E4：中指
        4: (2, 830),  # F4：食指
    },
    "LOW": {
        10: (5, 750), # G3：小拇指
        11: (4, 830), # A3：无名指（低八度 6→5 联奏）
    },
}
LEFT_HAND_PREFERRED_STOP = {
    1: "BASE", 2: "BASE", 3: "RIGHT", 4: "RIGHT",
    10: "LOW", 11: "BASE", 12: "BASE",
}

# 右手原位：食=B4、中=C5、无=D5、小=E5。
# 右手左移 5cm：食=G4、中=A4、无=B4、小=C5。
RIGHT_HAND_STOPS = {
    "BASE": {
        7: (2, 830),  # B4：食指
        8: (3, 850),  # C5：中指
        9: (4, 830),  # D5：无名指
    },
    "LEFT": {
        5: (2, 830),  # G4：食指
        6: (3, 850),  # A4：中指
        7: (4, 830),  # B4：无名指；La 后的 Si 不需回原位
        8: (5, 600),  # C5：小拇指；La 后的高 Do 不需回原位
    },
}
RIGHT_HAND_PREFERRED_STOP = {
    5: "LEFT", 6: "LEFT", 7: "BASE", 8: "BASE", 9: "BASE",
}


def get_command_for_note(note):
    """按两手当前停靠位返回实际应弯曲的手指；调用前必须已完成必要移位。"""
    if note in LEFT_HAND_PREFERRED_STOP:
        command = LEFT_HAND_STOPS[current_left_stop].get(note)
        if command is None:
            raise RuntimeError(f"左手 stop={current_left_stop} 未覆盖 note={note}")
        joint_idx, position = command
        return "left_dexhand", joint_idx, position

    if note in RIGHT_HAND_PREFERRED_STOP:
        command = RIGHT_HAND_STOPS[current_right_stop].get(note)
        if command is None:
            raise RuntimeError(f"右手 stop={current_right_stop} 未覆盖 note={note}")
        joint_idx, position = command
        return "right_dexhand", joint_idx, position

    raise ValueError(f"未定义的 note={note}")


def get_left_target_stop(note, next_note):
    """返回左手本拍目标 stop；右手音与休止不移动左手。"""
    if note == 0 or note not in LEFT_HAND_PREFERRED_STOP:
        return current_left_stop
    if note == 11 and next_note == 10:
        return "LOW"
    if note in LEFT_HAND_STOPS[current_left_stop]:
        return current_left_stop
    return LEFT_HAND_PREFERRED_STOP[note]


def get_right_target_stop(note):
    """返回右手本拍目标 stop；左手音与休止不移动右手。"""
    if note == 0 or note not in RIGHT_HAND_PREFERRED_STOP:
        return current_right_stop
    if note in RIGHT_HAND_STOPS[current_right_stop]:
        return current_right_stop
    return RIGHT_HAND_PREFERRED_STOP[note]


# ===== 茉莉花乐谱（简谱 → 事件序列） =====
# 事件格式: (note, duration) 或 (note, duration, is_tied)
#   note:      0=休止符, 1-7=音符
#   duration:  拍数（1=四分, 2=二分, 4=全音符, 0.5=八分音符, ...）
#   is_tied:   True=延音一拍（用于 "X -" 延音或 "⌒" 连音线第一个音）
#
# 注意：本曲节拍简化处理，每个音默认 1 拍。
# 如需精细节奏（八分音符、十六分音符），调整对应 duration 即可。

SCORE = [
    # 前奏 1
    # (0, 1), (0, 1), (5, 2), (2, 2), (0, 1), (0, 1),
    # (1, 2), (0, 1), (0, 1), (2, 4),
    # # 前奏 2
    # (5, 4), (5, 4), (5, 4), (5, 4),
    # 好一朵美丽的茉莉花 (含 8 分音符 + 高八度 Do)
    # 原谱：3 35 6·1 1·6 | 5 56 5 -
    # 高八度 Do（1·, 1 sticky）用 8 表示（右手小拇指）
    # 6· 和 6 暂不处理（其他高八度音后续通过移动手解决）



    # 长亭外古道边
    (5, 1), (3, 0.5), (5, 0.5), (8, 1, True), 
    (6, 1), (8, 0.5), (6, 0.5),
    (5, 1), 

    # 芳草碧连天
    (5, 1), (1, 0.5), (2, 0.5,True),(3, 1), (2, 0.5),(1, 0.5),(2, 2,True),

    # 晚风拂柳 笛声残
    (5, 1), (3, 0.5), (5, 0.5,True), (8, 1, True), (7, 0.5), 
    (6, 0.5), (8, 1), (5, 1, True),


    # 夕阳山外山
    (5, 1), (2, 0.5), (3, 0.5), (4, 1,True), (12, 0.5), 
    (1, 2, True),

    # 天之涯地之角
    (6, 1), (8, 1,True), (8, 2,True), 
    (7, 1), (6, 0.5), (7, 0.5,True), (8, 1,True),

    # 知交半零落
    (6, 0.5), (7, 0.5,True), (8, 0.5), (6, 0.5,True), (6, 0.5),(5, 0.5, True), (3, 0.5), (1, 0.5,True), (2, 4,True), 

    # 一壶浊酒尽余欢
    (5, 1), (3, 0.5), (5, 0.5,True), (8, 1, True), (7, 0.5), 
    (6, 1), (8, 1), (5, 1, True),

    # 今宵别梦寒
    (5, 1), (2, 0.5), (3, 0.5,True), (4, 1, True), (12, 0.5,True), 
    (1, 2,True), 

    # 长亭外古道边
    (5, 1), (3, 0.5), (5, 0.5), (8, 1, True), 
    (6, 1), (8, 0.5), (6, 0.5),
    (5, 1), 
    
    # 芳草碧连天
    (5, 1), (1, 0.5), (2, 0.5,True),(3, 1), (2, 0.5),(1, 0.5),(2, 2,True),
    
    # 问君此去几时来
    (5, 1), (3, 0.5), (5, 0.5,True), (8, 1, True), (7, 0.5), 
    (6, 0.5), (8, 1), (5, 1, True),
    
    
    # 来时莫徘徊
    (5, 1), (2, 0.5), (3, 0.5), (4, 1,True), (12, 0.5), 
    (1, 2, True),

    
    
    # 天之涯地之角
    (6, 1), (8, 1,True), (8, 2,True), 
    (7, 1), (6, 0.5), (7, 0.5,True), (8, 1,True),

    # 知交半零落
    (6, 0.5), (7, 0.5,True), (8, 0.5), (6, 0.5,True), (6, 0.5),(5, 0.5, True), (3, 0.5), (1, 0.5,True), (2, 4,True), 

    # 人生难得是欢聚
    (5, 1), (3, 0.5), (5, 0.5,True), (8, 1, True), (7, 0.5), 
    (6, 1), (8, 1), (5, 1, True),



    # 惟有离别多
    (5, 1), (2, 0.5), (3, 0.5,True), (4, 1, True), (12, 0.5,True), 
    (1, 2,True), 

    # 惟有离别多
    (5, 1), (2, 0.5), (3, 0.5,True), (4, 1, True), (12, 0.5,True), 
    (5, 2,True), 



]


def generateDexhandCommands(joint_idx: int, joint_position: int) -> list:
    """构造一帧 6 个 JointCommand 的指令列表。

    手指关节划分（参考 Inspire RH56）：
      - cmds[0] = Joint 1: 拇指旋转
      - cmds[1] = Joint 6: 拇指弯曲
      - cmds[2] = Joint 2: 食指
      - cmds[3] = Joint 3: 中指
      - cmds[4] = Joint 4: 无名指
      - cmds[5] = Joint 5: 小指

    拇指两个关节（idx 0, 1）全程固定：
      - 拇指旋转 (idx 0) = 0
      - 拇指弯曲 (idx 1) = 1000

    其他 4 个手指（食、中、无名、小拇）默认张开（1000），
    活动手指弯曲到 joint_position。
    """
    cmds = [JointCommand() for _ in range(6)]
    # 拇指两个关节固定（每次发送都会重新设置）
    cmds[0].position = 0      # 拇指旋转 (Joint 1)
    cmds[1].position = 1000   # 拇指弯曲 (Joint 6)
    # 其他 4 个手指（食、中、无名、小拇）默认张开
    for i in [2, 3, 4, 5]:
        cmds[i].position = 1000
    # 活动手指弯曲（不包括拇指）
    if joint_idx not in (0, 1):
        cmds[joint_idx].position = joint_position
    return cmds

def hit(robot: GalbotRobot, end_effector: str, commands: list):
    """发送一帧 6-DoF 角度指令到指定灵巧手（左/右）。

    is_blocking=False 让调用立即返回，不阻塞调度器。
    SDK 返回非 SUCCESS 时输出警告（不报异常）。
    """
    print(f"hit: {end_effector}, {commands}")
    status = robot.set_dexhand_command(
        end_effector=end_effector,
        dexhand_command=commands,
        is_blocking=False,
    )
    if status != ControlStatus.SUCCESS:
        print(f"bad status: {status}")


def reset_hands(robot: GalbotRobot):
    """重置双手为开启状态（拇旋转=0，其他关节=1000）。

    用于：
    - 弹奏前的初姿势
    - 弹奏后的复位
    - Ctrl+C 强制退出时
    """
    reset_cmds = generateDexhandCommands(0, 1000)
    hit(robot, "left_dexhand",  reset_cmds)
    hit(robot, "right_dexhand", reset_cmds)


def open_fingers_before_playback(robot: GalbotRobot):
    """每轮弹奏前张开双手非拇指手指，再等待其物理到位。"""
    print("[轮次] 双手食/中/无名/小拇指展开到 1000")
    reset_hands(robot)
    time.sleep(FINGER_OPEN_WAIT_SECONDS)


def move_hand_to(motion, robot, target_pose, joint_group,
                  speed_rad_s=HAND_MOVE_SPEED_RAD_S, is_blocking=False):
    """移动单手到绝对目标位姿（速度控制版）。

    与原版（motion_plan_multi_waypoints 路径规划）的区别：
      - 原版：路径规划后走直线轨迹，速度由 SDK 内部决定
      - 本版：先求逆运动学（IK），再用 set_joint_positions 直接驱动关节
        速度可控（弧度/秒），可预测

    常规弹奏中的移位保持非阻塞，由调用方用 HAND_MOVE_DELAY 控制节奏；
    曲终回原位可传入 is_blocking=True，确保手臂到位后才复位手指或关闭机器人，
    避免运动被后续清理指令中途打断而产生顿挫。
    """
    # 1) 求逆运动学：把目标末端位姿 -> 关节角度
    status, positions = motion.inverse_kinematics(
        target_pose, [joint_group],
    )
    if status != MotionStatus.SUCCESS:
        print(f"[IK] 逆运动学失败 ({joint_group}): {status}")
        return False
    joint_positions = positions[joint_group]

    # 2) 以指定速度驱动关节到目标角度（非阻塞）
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


def move_arms_to_first_positions(motion, robot):
    """阻塞式移动双臂至归零前的指定 First position。"""
    print("[轮次] 双臂移动到 First position")
    left_ok = move_hand_to(
        motion,
        robot,
        list(FIRST_POSITION_LEFT),
        G1JointGroup.left_arm,
        is_blocking=True,
    )
    right_ok = move_hand_to(
        motion,
        robot,
        list(FIRST_POSITION_RIGHT),
        G1JointGroup.right_arm,
        is_blocking=True,
    )
    if not left_ok or not right_ok:
        raise RuntimeError("双臂未能到达 First position，无法继续轮次流程")


def move_arms_to_original_positions(motion, robot):
    """阻塞式移动双臂至 ORIGINAL_LEFT/RIGHT 弹琴初始位姿。"""
    print("[轮次] 双臂移动到 ORIGINAL 弹琴初始位姿")
    left_ok = move_hand_to(
        motion,
        robot,
        list(ORIGINAL_LEFT),
        G1JointGroup.left_arm,
        is_blocking=True,
    )
    right_ok = move_hand_to(
        motion,
        robot,
        list(ORIGINAL_RIGHT),
        G1JointGroup.right_arm,
        is_blocking=True,
    )
    if not left_ok or not right_ok:
        raise RuntimeError("双臂未能到达 ORIGINAL 弹琴初始位姿，无法开始下一轮")


def reset_whole_body_between_rounds(motion, robot):
    """完成一轮后的等待、双臂 First position 和可配置的全身归零。"""
    print(f"[轮次] 本轮结束，等待 {INTER_ROUND_PAUSE_SECONDS:g} 秒")
    time.sleep(INTER_ROUND_PAUSE_SECONDS)
    move_arms_to_first_positions(motion, robot)
    if ENABLE_WHOLE_BODY_JOINT_ZERO:
        print("[轮次] 执行全身姿态归零")
        status = motion.move_whole_body_joint_zero()
        time.sleep(5)  # 等待归零动作完成
        if status != MotionStatus.SUCCESS:
            raise RuntimeError(f"全身姿态归零失败: {status}")
    else:
        print("[轮次] 已跳过全身姿态归零（ENABLE_WHOLE_BODY_JOINT_ZERO=False）")


def get_origin_poses_with_retry(motion, max_retries=POSE_FETCH_MAX_RETRIES,
                                retry_interval=POSE_FETCH_RETRY_INTERVAL):
    """读取左右手末端原位；SDK 状态数据未就绪时按固定次数重试。

    返回：
        (left_pose, right_pose)，每个都是 7 元素位姿列表。

    Raises:
        RuntimeError: 经过全部重试后仍无法读取任一手位姿。
    """
    last_left_status = None
    last_right_status = None
    for attempt in range(1, max_retries + 1):
        status_left, left_pose = motion.get_end_effector_pose_on_chain(
            G1JointGroup.left_arm
        )
        status_right, right_pose = motion.get_end_effector_pose_on_chain(
            G1JointGroup.right_arm
        )
        last_left_status = status_left
        last_right_status = status_right

        if status_left == MotionStatus.SUCCESS and status_right == MotionStatus.SUCCESS:
            return list(left_pose), list(right_pose)

        if status_left != MotionStatus.SUCCESS:
            print(f"[重试 {attempt}/{max_retries}] 左手位姿获取失败: {status_left}")
        if status_right != MotionStatus.SUCCESS:
            print(f"[重试 {attempt}/{max_retries}] 右手位姿获取失败: {status_right}")
        if attempt < max_retries:
            time.sleep(retry_interval)

    raise RuntimeError(
        f"经过 {max_retries} 次重试仍无法获取手部原位："
        f"left={last_left_status}, right={last_right_status}。"
        "请确认机器人运动服务、双臂状态和 SDK 连接均正常。"
    )


def repeat_frames_to_duration(frames: Sequence[Sequence[float]], dt: float,
                              duration: float):
    """把一个闭合动作周期重复到指定时长，供 SDK 作为有限轨迹执行。"""
    if not frames:
        raise ValueError("情感动作轨迹不能为空")
    if dt <= 0:
        raise ValueError("情感动作轨迹 dt 必须大于 0")
    cycle_count = max(1, math.ceil(duration / (len(frames) * dt)))
    return [list(frame) for _ in range(cycle_count) for frame in frames]


def build_joint_trajectory(states: Sequence[Sequence[float]], joint_groups,
                           dt: float) -> Trajectory:
    """将关节角帧转换为 SDK Trajectory。"""
    seconds = 0.0
    points = []
    for state in states:
        seconds += dt
        commands = []
        for position in state:
            command = JointCommand()
            command.position = position
            commands.append(command)

        point = TrajectoryPoint()
        point.time_from_start_second = seconds
        point.joint_command_vec = commands
        points.append(point)

    trajectory = Trajectory()
    trajectory.joint_groups = joint_groups
    trajectory.points = points
    return trajectory


class EmotionMovementController:
    """协调头部摇动、双臂外摆和弹琴手掌移位。

    头部和双臂分别提交轨迹。双臂使用 MuJoCo null-space IK，从实时双臂关节角生成
    保持末端手掌 pose 不变的外摆轨迹；在琴键移位时暂停，并在新手位稳定后异步重建。
    """

    def __init__(self, robot: GalbotRobot):
        self.robot = robot
        self._lock = threading.RLock()
        self._running = False
        self._paused = True
        self._trajectory_active = False
        self._generation = 0
        self._arm_worker = None
        self._head_trajectory = None
        self._head_enabled = ENABLE_HEAD_SHAKE
        self._arm_enabled = ENABLE_ARM_SWING

        if self._head_enabled:
            try:
                with HEAD_POSITIONS_JSON.open("r", encoding="utf-8") as file:
                    head_cycle = json.load(file)
                self._head_trajectory = build_joint_trajectory(
                    repeat_frames_to_duration(
                        head_cycle,
                        HEAD_TRAJECTORY_DT,
                        EMOTION_TRAJECTORY_SECONDS,
                    ),
                    [G1JointGroup.head],
                    HEAD_TRAJECTORY_DT,
                )
            except Exception as exc:
                self._head_enabled = False
                print(f"[情感动作] 头部轨迹加载失败，已禁用摇头: {exc}")

    def _stop_trajectories_locked(self):
        """SDK 只能全局停止轨迹，因此暂停摆臂时也会一并暂停摇头。"""
        if not self._trajectory_active:
            return
        status = self.robot.stop_trajectory_execution()
        if status != ControlStatus.SUCCESS:
            print(f"[情感动作] 停止轨迹失败: {status}")
        self._trajectory_active = False

    def _start_head_locked(self):
        if not self._head_enabled or self._head_trajectory is None:
            return
        # 与测试脚本一致：先将头部摆正，再从完整摇头周期开始。
        status = self.robot.set_joint_positions(
            joint_positions=[0.0, 0.0],
            joint_groups=[G1JointGroup.head],
            is_blocking=True,
        )
        if status != ControlStatus.SUCCESS:
            print(f"[情感动作] 头部归正失败，已跳过本次摇头: {status}")
            return
        status = self.robot.execute_joint_trajectory(
            self._head_trajectory,
            is_blocking=False,
        )
        if status != ControlStatus.SUCCESS:
            print(f"[情感动作] 启动摇头失败: {status}")
            return
        self._trajectory_active = True

    def _prepare_and_start_arm_swing(self, generation: int):
        """后台生成当前手位的摆臂轨迹，完成后仅在状态仍有效时启动。"""
        try:
            # 仅在需要摆臂时导入 MuJoCo 依赖，缺少时不影响弹琴和摇头。
            from emotion_movement import generate_arm_swing_trajectory

            positions = self.robot.get_joint_positions(
                joint_groups=[G1JointGroup.left_arm, G1JointGroup.right_arm]
            )
            if len(positions) != 14:
                raise RuntimeError(
                    f"读取双臂关节角数量错误：期望 14，实际 {len(positions)}"
                )

            arm_cycle = generate_arm_swing_trajectory(
                left_arm_q=positions[:7],
                right_arm_q=positions[7:],
                n_positions=ARM_SWING_CYCLE_POINTS,
            )
            arm_trajectory = build_joint_trajectory(
                repeat_frames_to_duration(
                    arm_cycle,
                    ARM_SWING_TRAJECTORY_DT,
                    EMOTION_TRAJECTORY_SECONDS,
                ),
                [G1JointGroup.left_arm, G1JointGroup.right_arm],
                ARM_SWING_TRAJECTORY_DT,
            )
        except Exception as exc:
            print(f"[情感动作] 双臂摆动未启动: {exc}")
            return

        with self._lock:
            # 期间若发生下一次手掌移位，当前计算结果已经过期，直接丢弃。
            if (
                not self._running
                or self._paused
                or generation != self._generation
            ):
                return
            status = self.robot.execute_joint_trajectory(
                arm_trajectory,
                is_blocking=False,
            )
            if status != ControlStatus.SUCCESS:
                print(f"[情感动作] 启动双臂摆动失败: {status}")
                return
            self._trajectory_active = True
            print("[情感动作] 双臂外摆轨迹已启动（保持当前手掌位姿）")

    def _start_arm_worker_locked(self):
        if not self._arm_enabled:
            return
        generation = self._generation
        self._arm_worker = threading.Thread(
            target=self._prepare_and_start_arm_swing,
            args=(generation,),
            name="emotion-arm-swing",
            daemon=True,
        )
        self._arm_worker.start()

    def start(self):
        """在弹奏开始时启动头部动作，并异步准备当前手位的双臂动作。"""
        if not ENABLE_EMOTION_MOVEMENT:
            return
        with self._lock:
            self._running = True
            self._paused = False
            self._generation += 1
            print(
                "[情感动作] 配置："
                f"摇头={'开启' if self._head_enabled else '关闭'}，"
                f"双臂外摆={'开启' if self._arm_enabled else '关闭'}"
            )
            self._start_head_locked()
            self._start_arm_worker_locked()

    def pause_for_hand_move(self):
        """手掌移位前暂停双臂轨迹，防止摆臂与键位移动争用手臂关节。

        摇头只控制 head 关节，与双臂移位没有控制权冲突。特别是在仅启用摇头
        （ENABLE_ARM_SWING=False）时，绝不能调用全局 stop_trajectory_execution：
        该调用会停止摇头，随后 resume 中的阻塞式头部归正会造成按键卡顿。
        """
        if not ENABLE_EMOTION_MOVEMENT:
            return
        # 没有双臂摆动时，头部动作可以跨越手掌移位持续执行，不做任何停/启操作。
        if not self._arm_enabled:
            return
        with self._lock:
            if not self._running:
                return
            self._paused = True
            self._generation += 1
            self._stop_trajectories_locked()

    def resume_after_hand_move(self):
        """手掌移位稳定后恢复摇头，并从新的实际手位重建摆臂。"""
        if not ENABLE_EMOTION_MOVEMENT:
            return
        # 仅摇头时 pause_for_hand_move 未停止头部轨迹，因此也无需归正或重启它。
        # 这样换键的耗时只由实际手掌移位决定，不会额外卡住 1~2 秒。
        if not self._arm_enabled:
            return
        with self._lock:
            if not self._running:
                return
            self._paused = False
            self._generation += 1
            self._start_head_locked()
            self._start_arm_worker_locked()

    def stop(self):
        """停止全部情感动作；可安全重复调用。"""
        if not ENABLE_EMOTION_MOVEMENT:
            return
        with self._lock:
            self._running = False
            self._paused = True
            self._generation += 1
            self._stop_trajectories_locked()


# ===== 调度状态 =====
idx = 0
MAX = len(SCORE)
finished = threading.Event()
shutdown_event = threading.Event()

# ===== 手部状态（移位管理）=====
left_origin_pose = None
right_origin_pose = None
left_hand_moved = False
right_hand_moved = False
current_left_stop = "BASE"
current_right_stop = "BASE"


def hit_next(robot: GalbotRobot, motion: GalbotMotion,
             emotion: EmotionMovementController = None):
    """处理一个 SCORE 事件。

    调度原理：
    - 事件本身占用的时间 = actual_time（不包含 RESET_DELAY 补足部分）
    - Timer delay = max(0.001, duration * INTERVAL - actual_time)
      · 0.5 拍事件间隔 0.5*0.7 = 0.35s → 连音
      · 1   拍事件间隔 1*0.7   = 0.7s  → 原始节奏
      · 2   拍事件间隔 2*0.7   = 1.4s
    """
    global idx, left_hand_moved, right_hand_moved
    global current_left_stop, current_right_stop
    if idx >= MAX or shutdown_event.is_set():
        if idx >= MAX:
            finished.set()
        return

    event = SCORE[idx]
    if len(event) == 2:
        note, duration = event
        is_extended = False
    else:
        note, duration, is_extended = event

    next_note = SCORE[idx + 1][0] if idx + 1 < MAX else None

    # ===== 手部移位管理（按当前 stop 优先，减少不必要的往返） =====
    # 例如左手右移弹 Mi 后，无名指仍在 Re，因此下一音 Re 留在 RIGHT stop。
    # 右手左移弹 La 后，无名指/小拇指仍在 Si/高 Do，也不需要立刻回原位。
    target_left_stop = get_left_target_stop(note, next_note)
    target_right_stop = get_right_target_stop(note)
    needs_hand_move = (
        target_left_stop != current_left_stop
        or target_right_stop != current_right_stop
    )
    shift_failed = False

    # 摆臂控制和手掌移位都使用双臂的 14 个关节。先全局暂停情感轨迹，
    # 移位完成后再以新关节角重新生成保持手掌位姿的摆臂轨迹。
    if emotion is not None and needs_hand_move:
        emotion.pause_for_hand_move()

    left_stop_offsets = {
        "BASE": 0.0,
        "RIGHT": LEFT_HAND_OFFSET_RIGHT,
        "LOW": LEFT_HAND_OFFSET_LOW,
    }
    left_stop_labels = {
        "BASE": "原位",
        "RIGHT": "右移 5cm",
        "LOW": "左移 2.5cm",
    }
    if target_left_stop != current_left_stop:
        print(f"[手部] 左手 {left_stop_labels[current_left_stop]} -> "
              f"{left_stop_labels[target_left_stop]}")
        target = list(left_origin_pose)
        target[1] += left_stop_offsets[target_left_stop]
        if move_hand_to(motion, robot, target, G1JointGroup.left_arm):
            time.sleep(HAND_MOVE_DELAY)
            current_left_stop = target_left_stop
            left_hand_moved = current_left_stop != "BASE"
        else:
            shift_failed = True

    right_stop_offsets = {"BASE": 0.0, "LEFT": RIGHT_HAND_OFFSET_LEFT}
    right_stop_labels = {"BASE": "原位", "LEFT": "左移 5cm"}
    if target_right_stop != current_right_stop:
        print(f"[手部] 右手 {right_stop_labels[current_right_stop]} -> "
              f"{right_stop_labels[target_right_stop]}")
        target = list(right_origin_pose)
        target[1] += right_stop_offsets[target_right_stop]
        if move_hand_to(motion, robot, target, G1JointGroup.right_arm):
            time.sleep(HAND_MOVE_DELAY)
            current_right_stop = target_right_stop
            right_hand_moved = current_right_stop != "BASE"
        else:
            shift_failed = True

    if emotion is not None and needs_hand_move:
        emotion.resume_after_hand_move()

    target_press = duration * RESET_DELAY
    actual_time = target_press  # 不再“补足”到 RESET_DELAY

    if note != 0:
        if shift_failed:
            # 移位失败不能按键，否则会在旧停靠位触发错误琴键。
            print(f"[跳过] note={note} 移位失败，本拍不按键")
            time.sleep(target_press)
        else:
            end_effector, joint_idx, position = get_command_for_note(note)
            hit(robot, end_effector, generateDexhandCommands(joint_idx, position))
            if target_press < RESET_DELAY:
                # 短音符：直接按 target_press 时间释放（保留连音效果）
                time.sleep(target_press)
                hit(robot, end_effector, generateDexhandCommands(0, 1000))
            else:
                time.sleep(RESET_DELAY)
                if target_press > RESET_DELAY:
                    time.sleep(target_press - RESET_DELAY)
                if is_extended:
                    time.sleep(RESET_DELAY)
                    hit(robot, end_effector, generateDexhandCommands(0, 1000))
                    actual_time += RESET_DELAY
                else:
                    hit(robot, end_effector, generateDexhandCommands(0, 1000))
    else:
        time.sleep(target_press)

    idx += 1

    if idx >= MAX:
        finished.set()
        return

    if not shutdown_event.is_set():
        # 用拍数算下次触发时间，连音与原节奏同时满足
        delay = max(0.001, duration * INTERVAL - actual_time)
        timer = threading.Timer(delay, hit_next, args=(robot, motion, emotion))
        timer.daemon = True
        timer.start()

def main():
    """程序入口：连接机器人、播放、完整清理后退出。

    流程：
      1) 创建 GalbotRobot 并 init（连真机）
      2) 等待 1s 让灵巧手完成上电复位
      3) 把双手摆到张开状态
      4) 按 PLAYBACK_REPEAT_COUNT 循环调度 SCORE
      5) 每轮结束后等待、双臂到 First position、可选全身姿态归零
      6) 等待播放完成或用户按 Ctrl+C 中断
      7) finally：重置手部 + 释放机器人资源（try/except 保证不卡死）
    """
    global idx, left_origin_pose, right_origin_pose, left_hand_moved, right_hand_moved
    global current_left_stop, current_right_stop
    robot = GalbotRobot()
    emotion = None
    robot.init()
    motion = GalbotMotion()
    motion.init()
    # motion 服务比灵巧手上电稍晚就绪；首次位姿读取前留出稳定时间。
    time.sleep(MOTION_STARTUP_DELAY)

    try:
        # 后续 stop 位移均以给定 ORIGINAL 弹琴初始位姿为基准。
        left_origin_pose = list(ORIGINAL_LEFT)
        right_origin_pose = list(ORIGINAL_RIGHT)
        print(f"[初始化] 左手 ORIGINAL 弹琴初始位姿: {left_origin_pose}")
        print(f"[初始化] 右手 ORIGINAL 弹琴初始位姿: {right_origin_pose}")
        shutdown_event.clear()

        for round_index in range(1, PLAYBACK_REPEAT_COUNT + 1):
            if shutdown_event.is_set():
                break

            # 每一轮（包括首轮）先经过 First position，稳定后再进入
            # ORIGINAL 弹琴初始位姿。后续轮次会在此前已经完成归零。
            open_fingers_before_playback(robot)
            move_arms_to_first_positions(motion, robot)
            print(f"[轮次] First position 稳定等待 {FIRST_POSITION_WAIT_SECONDS:g} 秒")
            time.sleep(FIRST_POSITION_WAIT_SECONDS)
            move_arms_to_original_positions(motion, robot)
            print(
                f"[轮次] ORIGINAL 弹琴初始位姿稳定等待 "
                f"{ORIGINAL_POSITION_WAIT_SECONDS:g} 秒"
            )
            time.sleep(ORIGINAL_POSITION_WAIT_SECONDS)
            idx = 0
            left_hand_moved = False
            right_hand_moved = False
            current_left_stop = "BASE"
            current_right_stop = "BASE"
            finished.clear()

            # 头部先开始规律摇动；双臂外摆会基于当前实际关节角在后台生成。
            emotion = EmotionMovementController(robot)
            emotion.start()
            print(f"[轮次] 开始弹奏第 {round_index}/{PLAYBACK_REPEAT_COUNT} 遍")
            hit_next(robot, motion, emotion)

            while not finished.is_set() and not shutdown_event.is_set():
                time.sleep(0.1)

            if emotion is not None:
                emotion.stop()
            if shutdown_event.is_set():
                break

            # 按要求，每一遍（包含最后一遍）结束后都执行轮次间处理。
            reset_whole_body_between_rounds(motion, robot)

        if not shutdown_event.is_set():
            reset_hands(robot)
            time.sleep(0.3)  # 等待手指物理上完成归位

    except KeyboardInterrupt:
        print("\n[中断] 检测到 Ctrl+C，正在安全退出...")
        shutdown_event.set()
    finally:
        # 必须先停止双臂轨迹，再执行手掌/手指复位，避免关节指令互相覆盖。
        if emotion is not None:
            try:
                emotion.stop()
            except Exception as e:
                print(f"停止情感动作失败: {e}")
        # 重置手部为开启状态（保证退出时不卡住）
        try:
            reset_hands(robot)
            time.sleep(0.2)
        except Exception as e:
            print(f"重置手部失败: {e}")

        # 释放机器人资源（try/except 保证不卡死）
        try:
            robot.request_shutdown()
        except Exception as e:
            print(f"request_shutdown 失败: {e}")
        try:
            robot.wait_for_shutdown()
        except Exception as e:
            print(f"wait_for_shutdown 失败: {e}")
        try:
            robot.destroy()
        except Exception as e:
            print(f"destroy 失败: {e}")
        print("[完成] 已退出")


if __name__ == "__main__":
    main()
