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

此版代码中，初始位姿有所改变（左右手中间隔四个键位）

| 手 |  原位食指  |   原位中指  | 原位无名指 | 原位小拇指 |
|--- |-----------|-------------|-----------|-----------|
| 左手 | D4（Re） |   C4（Do）  | B3（低 Si） | A3（低 La） |
| 右手 | B4（Si） | C5（高 Do） | D5（高 Re） | E5（高 Mi） |
  

|  手  |   手掌位置 | 小拇指         |    无名指       |   中指        |   食指  |
|------|-----------|---------------|-----------------|--------------|---------|
| 左手 |   原位     | A3（低6）     | B3（未用于当前谱）| C4（1）      | D4（2）  |
| 左手 | 向右 5cm   |  C4（1）      | D4（2）         | E4（3）       |  F4（4） |
| 左手 | 向左 2.5cm | G3（低5 / 10）| A3（低6 / 11）   | —            | —       |
| 右手 | 原位       | E5（当前谱未用）| D5（高2 / 9）   | C5（高1 / 8） | B4（7） |
| 右手 | 向左 5cm   | C5（高1 / 8） |    B4（7）       | A4（6）       | G4（5） |

"""
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler
from galbot_sdk.g1 import (
    ControlStatus,
    GalbotMotion,
    GalbotRobot,
    G1JointGroup,
    JointCommand,
    MotionStatus,
)


# ===== 调速参数 =====
INTERVAL = 0.7
RESET_DELAY = 0.55



# 初始位姿为
# ORIGINAL_LEFT = [
#   0.5581821103099842, 0.07262865463298485, 0.8827970267118115, 
#   0.006255145382323624, 0.08071926031697177, 0.009632238159119438, 
#   0.9966707049764094]
# ORIGINAL_RIGHT = [
#   0.5674243747810743, -0.12291054789286718, 0.8766152675353718, 
#   0.013935849142309971, 0.05652338361686925, 0.06110483610243252, 
#   0.9964321844551579]


# ===== 手部位姿管理 =====
# 坐标约定沿用原脚本：Y 增大为向左，Y 减小为向右。
# 新初始位姿：
#   左手：食=D4、中=C4、无=B3、小=A3
#   右手：食=B4、中=C5、无=D5、小=E5
LEFT_HAND_OFFSET_RIGHT = -0.050  # 左手向右 5cm：用于 E4/F4
LEFT_HAND_OFFSET_LOW   = 0.025   # 左手向左 2.5cm：用于低八度 A3 -> G3
RIGHT_HAND_OFFSET_LEFT = 0.050   # 右手向左 5cm：用于 G4/A4
HAND_MOVE_SPEED_RAD_S  = 0.3     # 手部移位速度（弧度/秒），0.2 起慢慢加
HAND_MOVE_DELAY        = 0.3     # 速度控制后预计手部到位所需时间

# motion 服务初始化后，第一次读取末端位姿偶尔会返回 DATA_FETCH_FAILED。
# 先等待服务就绪，再进行有限重试；避免瞬态状态未就绪导致整首曲子退出。
MOTION_STARTUP_DELAY = 2.0
POSE_FETCH_MAX_RETRIES = 10
POSE_FETCH_RETRY_INTERVAL = 0.5


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
    },
    "RIGHT": {
        1: (5, 700),  # C4：小拇指
        2: (4, 830),  # D4：无名指；Mi 后的 Re 不需回原位
        3: (3, 850),  # E4：中指
        4: (2, 830),  # F4：食指
    },
    "LOW": {
        10: (5, 700), # G3：小拇指
        11: (4, 830), # A3：无名指（低八度 6→5 联奏）
    },
}
LEFT_HAND_PREFERRED_STOP = {
    1: "BASE", 2: "BASE", 3: "RIGHT", 4: "RIGHT",
    10: "LOW", 11: "BASE",
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


    # -------伴奏--------
    # 好一朵美丽的茉莉花
    (3, 1), (3, 0.5), (5, 0.5), (6, 0.5), (8, 0.5), (8, 0.5), (6, 0.5),
    (5, 1), (5, 0.5), (6, 0.5), (5, 2),

    # 好一朵美丽的茉莉花
    (3, 1), (3, 0.5), (5, 0.5), (6, 0.5), (8, 0.5), (8, 0.5), (6, 0.5),
    (5, 1), (5, 0.5), (6, 0.5), (5, 2),

    # 芬芳美丽满枝丫
    (5, 1), (5, 1), (5, 1), (3, 0.5), (5, 0.5),
    (6, 1), (6, 1), (5, 2),

    # 又香又白人人夸
    (3, 1), (2, 0.5), (3, 0.5), (5, 1), (3, 0.5), (2, 0.5),
    (1, 1), (1, 0.5), (2, 0.5), (1, 2),

    # 让我来将你摘下
    (3, 0.5), (2, 0.5), (1, 0.5), (3, 0.5),(2, 1, True), (3, 1),
    (5, 1), (6, 0.5), (8, 0.5), (5, 2),

    # 送给别人家
    (2, 1), (3, 0.5), (5, 0.5), (2, 0.5), (3, 0.5),(1, 0.5), (11, 0.5),  # 低八度6
    (10, 2),   # 低八度5
    
    # 茉莉花 茉莉花
    (11, 1), (1, 1), 
    (2, 0.5,True), (3, 0.5), (1, 0.5), (2, 0.5), (1, 0.5), (11, 0.5),
    (10, 2, True), 

    # -------独奏--------
    # 好一朵美丽的茉莉花
    (3, 1), (3, 0.5, True), (5, 0.5), (6, 0.5), (8, 0.5, True), (8, 0.5), (6, 0.5, True),
    (5, 1), (5, 0.5), (6, 0.5), (5, 1),

    # 好一朵美丽的茉莉花
    (3, 1), (3, 0.5), (5, 0.5, True), (6, 0.5), (8, 0.5, True), (8, 0.5), (6, 0.5, True),
    (5, 1), (5, 0.5), (6, 0.5), (5, 1),

    # 芬芳美丽满枝丫
    (5, 1), (5, 1), (5, 1), (3, 0.5), (5, 0.5),
    (6, 1), (6, 1), (5, 1),

    # 又香又白人人夸
    (3, 1), (2, 0.5), (3, 0.5), (5, 1), (3, 0.5), (2, 0.5),
    (1, 1), (1, 0.5), (2, 0.5), (1, 2),

    # 让我来将你摘下
    (3, 0.5), (2, 0.5, True), (1, 0.5), (3, 0.5),(2, 1, True), (3, 0.5),
    (5, 1), (6, 0.5), (8, 0.5), (5, 2),

    # 送给别人家
    (2, 1), (3, 0.5), (10, 0.5), (2, 0.5), (3, 0.5), (1, 0.5), (11, 0.5),  # 低八度6
    (10, 2),   # 低八度5

    # --------茉莉花哈茉莉花（【I.】）---------
    # 仅保留主旋律：6 61 2 3 12 16 5 - -
    (11, 1), (11, 0.5), (1, 0.5),  # 低八度6
    (2, 1, True), (3, 0.5),
    (1, 0.5), (2, 0.5, True), (1, 0.5), (11, 0.5),  # 低八度6
    (10, 2),   # 低八度5
    (0, 1),    # 休止符
    
    # -----------合奏------------
    #  好一朵美丽的茉莉花
    (3, 1), (3, 0.5), (5, 0.5, True), (6, 0.5), (8, 0.5, True), (8, 0.5), (6, 0.5),
    (5, 1), (5, 0.5), (6, 0.5), (5, 2),

    #  好一朵美丽的茉莉花
    (3, 1), (3, 0.5), (5, 0.5, True),  (6, 0.5), (8, 0.5, True), (8, 0.5), (6, 0.5),
    (5, 1), (5, 0.5), (6, 0.5), (5, 2),

    # 芬芳美丽满枝丫
    (5, 1), (5, 1), (5, 1), (3, 0.5), (5, 0.5),
    (6, 1), (6, 1), (5, 1),
    
    # 又香又白人人夸
    (3, 1), (2, 0.5), (3, 0.5), (5, 1), (3, 0.5), (2, 0.5),
    (1, 1), (1, 0.5), (2, 0.5), (1, 2),

    # 让我来将你摘下
    (3, 0.5), (2, 0.5, True), (1, 0.5), (3, 0.5),(2, 1, True), (3, 0.5),
    (5, 1), (6, 0.5), (8, 0.5), (5, 2),

    # 送给别人家
    (2, 1), (3, 0.5), (10, 0.5), (2, 0.5), (3, 0.5), (1, 0.5), (11, 0.5),  # 低八度6
    (10, 2),   # 低八度5


    # 茉莉花呀茉莉花
    (11, 1), (11, 0.5), (1, 0.5),  # 低八度6
    (2, 1, True), (3, 0.5),
    (1, 0.5), (2, 0.5, True), (1, 0.5), (11, 0.5),  # 低八度6
    (5, 1, True),   # 低八度5

    # 茉莉花呀茉莉花
    (11, 1), (11, 0.5), (1, 0.5),  # 低八度6
    (2, 2, True), (3, 1),

    # 茉莉花
    (8, 1), (9, 1), (8, 1), (6, 1),  # 高八度1、高八度2、高八度6
    (5, 2, True), (5, 2, True)



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


def move_hand_to(motion, robot, target_pose, joint_group,
                    speed_rad_s=HAND_MOVE_SPEED_RAD_S):
    """移动单手到绝对目标位姿（速度控制版）。

    与原版（motion_plan_multi_waypoints 路径规划）的区别：
      - 原版：路径规划后走直线轨迹，速度由 SDK 内部决定
      - 本版：先求逆运动学（IK），再用 set_joint_positions 直接驱动关节
        速度可控（弧度/秒），可预测

    调用方仍以 time.sleep(HAND_MOVE_DELAY) 等手物理到位
    （is_blocking=False，与原版一致保持非阻塞）。
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
        is_blocking=False,
    )
    if status != ControlStatus.SUCCESS:
        print(f"[移位] {joint_group} set_joint_positions 失败: {status}")
        return False
    return True


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


def hit_next(robot: GalbotRobot, motion: GalbotMotion):
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
    shift_failed = False

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
        timer = threading.Timer(delay, hit_next, args=(robot, motion))
        timer.daemon = True
        timer.start()

def main():
    """程序入口：连接机器人、播放、完整清理后退出。

    流程：
      1) 创建 GalbotRobot 并 init（连真机）
      2) 等待 1s 让灵巧手完成上电复位
      3) 把双手摆到张开状态
      4) 手动调度：直接调用 hit_next（hit_next 在处理完毕后会自动 Timer 下一次）
      5) 等待播放完成或用户按 Ctrl+C 中断
      6) 弹奏完毕后恢复弹奏前姿势
      7) finally：重置手部 + 释放机器人资源（try/except 保证不卡死）
    """
    global idx, left_origin_pose, right_origin_pose, left_hand_moved, right_hand_moved
    global current_left_stop, current_right_stop
    robot = GalbotRobot()
    robot.init()
    motion = GalbotMotion()
    motion.init()
    # motion 服务比灵巧手上电稍晚就绪；首次位姿读取前留出稳定时间。
    time.sleep(MOTION_STARTUP_DELAY)

    try:
        # 弹奏前的初始姿势：双手全张开
        init_cmds = generateDexhandCommands(0, 1000)
        hit(robot, "left_dexhand",  init_cmds)
        hit(robot, "right_dexhand", init_cmds)
        time.sleep(0.5)

        # 捕获当前左右手末端位姿作为原始位姿；SDK 首次查询可能暂未有数据。
        left_origin_pose, right_origin_pose = get_origin_poses_with_retry(motion)
        print(f"[初始化] 左手原位: {left_origin_pose}")
        print(f"[初始化] 右手原位: {right_origin_pose}")

        # 重置状态
        idx = 0
        left_hand_moved = False
        right_hand_moved = False
        current_left_stop = "BASE"
        current_right_stop = "BASE"
        finished.clear()
        shutdown_event.clear()

        # 手动调度：直接调用 hit_next，它在完成后自动 Timer 下一次
        hit_next(robot, motion)

        # 等待播放完成或被中断
        while not finished.is_set() and not shutdown_event.is_set():
            time.sleep(0.1)

        # 弹奏完毕后恢复弹奏前姿势
        if not shutdown_event.is_set():
            if left_hand_moved:
                if move_hand_to(motion, robot, list(left_origin_pose), G1JointGroup.left_arm):
                    current_left_stop = "BASE"
            if right_hand_moved:
                if move_hand_to(motion, robot, list(right_origin_pose), G1JointGroup.right_arm):
                    current_right_stop = "BASE"
            if left_hand_moved or right_hand_moved:
                time.sleep(HAND_MOVE_DELAY)
            reset_hands(robot)
            time.sleep(0.3)  # 等待手指物理上完成归位

    except KeyboardInterrupt:
        print("\n[中断] 检测到 Ctrl+C，正在安全退出...")
        shutdown_event.set()
    finally:
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
