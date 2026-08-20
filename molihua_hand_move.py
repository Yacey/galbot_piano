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
    Parameter,
    PoseState,
)


# ===== 调速参数 =====
INTERVAL = 0.7
RESET_DELAY = 0.55

# ===== 手部位姿管理 =====
LEFT_HAND_OFFSET_LEFT   = 0.07
RIGHT_HAND_OFFSET_RIGHT = -0.045
HAND_MOVE_DELAY         = 0.1


# ===== 音符到关节的映射（与 Twinkle 一致） =====
# 高八度（数字后加点如 "1."）沿用原指纲+位置；
# 实际音高由手校准决定，这里只是触发位置相同
NOTE_MAP = {
    # 低八度（base）
    1: ("left_dexhand",  5, 700),   # 1 = C4
    2: ("left_dexhand",  4, 830),   # 2 = D4
    3: ("left_dexhand",  3, 850),   # 3 = E4
    4: ("left_dexhand",  2, 830),   # 4 = F4
    5: ("right_dexhand", 2, 830),   # 5 = G4  (原位右手食指)
    6: ("right_dexhand", 3, 850),   # 6 = A4  (原位右手中指)
    7: ("right_dexhand", 4, 830),   # 7 = B4  (原位右手无名指)
    # 高八度：Do + Re 通过移动手解决
    8: ("right_dexhand", 5, 600),  # 1· = C5（右手小拇指，原位）
    9: ("right_dexhand", 4, 830),  # 2· = D5（右手无名指，需右移 4.5cm）
    # 低八度：5、6 通过移动左手解决
    10: ("left_dexhand",  5, 700),  # 低5· = G3（左手小拇指，需左移 7cm）
    11: ("left_dexhand",  4, 830),  # 低6· = A3（左手无名指，需左移 7cm）
}


def get_command_for_note(note):
    """直接从 NOTE_MAP 查表。手部移位与否由 hit_next 中的 trigger 逻辑控制。"""
    return NOTE_MAP.get(note)


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
    (10, 1), (1, 1), 
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
    (3, 0.5), (2, 0.5, True), (1, 0.5), (3, 0.5),(9, 1, True), (3, 0.5),
    (5, 1), (6, 0.5), (8, 0.5), (5, 2),

    # 送给别人家
    (2, 1), (3, 0.5), (10, 0.5), (2, 0.5), (3, 0.5), (1, 0.5), (11, 0.5),  # 低八度6
    (10, 2),   # 低八度5

    # --------茉莉花哈茉莉花（【I.】）---------
    # 仅保留主旋律：6 61 2 3 12 16 5 - -
    (6, 1), (11, 0.5), (1, 0.5),  # 低八度6
    (9, 1, True), (3, 0.5),
    (1, 0.5), (2, 0.5, True), (1, 0.5), (11, 0.5),  # 低八度6
    (10, 2),   # 低八度5
    (0, 1),    # 休止符
    
    # -----------合奏------------
    #  好一朵美丽的茉莉花
    (3, 1), (3, 0.5), (5, 0.5, True), (6, 0.5), (8, 0.5, True), (8, 0.5), (6, 0.5),
    (5, 1), (5, 0.5), (6, 0.5), (5, 2),

    #  好一朵美丽的茉莉花
    (3, 1), (3, 0.5), (5, 0.5, True), (6, 0.5), (8, 0.5, True), (8, 0.5), (6, 0.5),
    (5, 1), (5, 0.5), (6, 0.5), (5, 2),

    # 芬芳美丽满枝丫
    (5, 1), (5, 1), (5, 1), (3, 0.5), (5, 0.5),
    (6, 1), (6, 1), (5, 1),
    
    # 又香又白人人夸
    (3, 1), (2, 0.5), (3, 0.5), (5, 1), (3, 0.5), (2, 0.5),
    (1, 1), (1, 0.5), (2, 0.5), (1, 2),

    # 让我来将你摘下
    (3, 0.5), (2, 0.5, True), (1, 0.5), (3, 0.5),(9, 1, True), (3, 0.5),
    (5, 1), (6, 0.5), (8, 0.5), (5, 2),

    # 送给别人家
    (2, 1), (3, 0.5), (10, 0.5), (2, 0.5), (3, 0.5), (1, 0.5), (11, 0.5),  # 低八度6
    (10, 2),   # 低八度5


    # 茉莉花呀茉莉花
    (6, 1), (6, 0.5), (1, 0.5),  # 低八度6
    (9, 1, True), (3, 0.5),
    (1, 0.5), (2, 0.5, True), (1, 0.5), (6, 0.5),  # 低八度6
    (5, 1, True),   # 低八度5

    # 茉莉花呀茉莉花
    (6, 1), (6, 0.5), (1, 0.5),  # 低八度6
    (9, 2, True), (3, 1),

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


def move_hand_to(motion, target_pose, joint_group):
    """移动单手到绝对目标位姿"""
    pose_state = PoseState()
    pose_state.chain_name = joint_group
    params = Parameter()
    params.set_direct_execute(True)
    params.set_move_line(True)
    params.is_blocking = False
    motion.motion_plan_multi_waypoints(
        {pose_state: [target_pose]},
        enable_collision_check=False, params=params,
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
right_hand_moved = False  # 用于跨线程传递关闭信号（Ctrl+C 、异常等）


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

    # ===== 手部移位管理 =====
    cur_needs_left = note in [10, 11]  # 低8度5/6才触发左移
    cur_needs_right = note == 9
    next_note = SCORE[idx + 1][0] if idx + 1 < MAX else None
    next_needs_left = (next_note in [10, 11]) if next_note is not None else False
    next_needs_right = (next_note == 9) if next_note is not None else False

    if cur_needs_left and not left_hand_moved:
        target = list(left_origin_pose); target[1] += LEFT_HAND_OFFSET_LEFT
        print(f"[手部] 左手左移 {LEFT_HAND_OFFSET_LEFT*100:.0f}cm")
        move_hand_to(motion, target, G1JointGroup.left_arm); time.sleep(HAND_MOVE_DELAY)
        left_hand_moved = True
    elif not cur_needs_left and not next_needs_left and left_hand_moved:
        print("[手部] 左手回原位")
        move_hand_to(motion, list(left_origin_pose), G1JointGroup.left_arm); time.sleep(HAND_MOVE_DELAY)
        left_hand_moved = False

    if cur_needs_right and not right_hand_moved:
        target = list(right_origin_pose); target[1] += RIGHT_HAND_OFFSET_RIGHT
        print(f"[手部] 右手右移 {-RIGHT_HAND_OFFSET_RIGHT*100:.0f}cm")
        move_hand_to(motion, target, G1JointGroup.right_arm); time.sleep(HAND_MOVE_DELAY)
        right_hand_moved = True
    elif not cur_needs_right and not next_needs_right and right_hand_moved:
        print("[手部] 右手回原位")
        move_hand_to(motion, list(right_origin_pose), G1JointGroup.right_arm); time.sleep(HAND_MOVE_DELAY)
        right_hand_moved = False

    target_press = duration * RESET_DELAY
    actual_time = target_press  # 不再“补足”到 RESET_DELAY

    if note != 0:
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
    robot = GalbotRobot()
    robot.init()
    motion = GalbotMotion()
    motion.init()
    time.sleep(1)

    try:
        # 弹奏前的初始姿势：双手全张开
        init_cmds = generateDexhandCommands(0, 1000)
        hit(robot, "left_dexhand",  init_cmds)
        hit(robot, "right_dexhand", init_cmds)
        time.sleep(0.5)

        # 捕获当前左右手末端位姿作为原始位姿
        status, l = motion.get_end_effector_pose_on_chain(G1JointGroup.left_arm)
        if status != MotionStatus.SUCCESS:
            raise RuntimeError(f"get left origin pose failed: {status}")
        status, r = motion.get_end_effector_pose_on_chain(G1JointGroup.right_arm)
        if status != MotionStatus.SUCCESS:
            raise RuntimeError(f"get right origin pose failed: {status}")
        left_origin_pose = list(l); right_origin_pose = list(r)
        print(f"[初始化] 左手原位: {left_origin_pose}")
        print(f"[初始化] 右手原位: {right_origin_pose}")

        # 重置状态
        idx = 0
        left_hand_moved = False
        right_hand_moved = False
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
                move_hand_to(motion, list(left_origin_pose), G1JointGroup.left_arm)
            if right_hand_moved:
                move_hand_to(motion, list(right_origin_pose), G1JointGroup.right_arm)
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