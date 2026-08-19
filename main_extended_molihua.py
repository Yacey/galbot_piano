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
from galbot_sdk.g1 import ControlStatus, GalbotRobot, JointCommand


# ===== 调速参数 =====
INTERVAL = 0.7          # 原始节奏：1 拍 = 0.7 秒（~86 BPM）
RESET_DELAY = 0.55     # 按下后多久归位（需大于手指物理完成松开动作的时间）


# ===== 音符到关节的映射（与 Twinkle 一致） =====
# 高八度（数字后加点如 "1."）沿用原指纲+位置；
# 实际音高由手校准决定，这里只是触发位置相同
NOTE_MAP = {
    # 低八度（base）
    1: ("left_dexhand",  5, 700),   # 1 = C4  (加深弯曲，使中质服务器更容易响应)
    2: ("left_dexhand",  4, 830),   # 2 = D4  (加深弯曲)
    3: ("left_dexhand",  3, 850),   # 3 = E4
    4: ("left_dexhand",  2, 830),   # 4 = F4
    5: ("right_dexhand", 2, 830),   # 5 = G4
    6: ("right_dexhand", 3, 850),   # 6 = A4
    7: ("right_dexhand", 4, 830),   # 7 = B4
    # 高八度（暂只处理 Do，其他后续通过移动手解决）
    8: ("right_dexhand", 5, 600),  # 1· = C5（右手小拇指）
}


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
    (2, 1), (3, 0.5), (5, 0.5), (2, 0.5), (3, 0.5),(1, 0.5), (6, 0.5),  # 低八度6
    (5, 2),   # 低八度5
    
    # 茉莉花 茉莉花
    (6, 1), (1, 1), 
    (2, 0.5,True), (3, 0.5), (1, 0.5), (2, 0.5), (1, 0.5), (6, 0.5),
    (5, 1, True), 

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
    (2, 1), (3, 0.5), (5, 0.5), (2, 0.5), (3, 0.5), (1, 0.5), (6, 0.5),  # 低八度6
    (5, 2),   # 低八度5

    # --------茉莉花哈茉莉花（【I.】）---------
    # 仅保留主旋律：6 61 2 3 12 16 5 - -
    (6, 1), (6, 0.5), (1, 0.5),  # 低八度6
    (2, 1, True), (3, 0.5),
    (1, 0.5), (2, 0.5, True), (1, 0.5), (6, 0.5),  # 低八度6
    (5, 2),   # 低八度5
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
    (3, 0.5), (2, 0.5, True), (1, 0.5), (3, 0.5),(2, 1, True), (3, 0.5),
    (5, 1), (6, 0.5), (8, 0.5), (5, 2),

    # 送给别人家
    (2, 1), (3, 0.5), (5, 0.5), (2, 0.5), (3, 0.5), (1, 0.5), (6, 0.5),  # 低八度6
    (5, 2),   # 低八度5


    # 茉莉花呀茉莉花
    (6, 1), (6, 0.5), (1, 0.5),  # 低八度6
    (2, 1, True), (3, 0.5),
    (1, 0.5), (2, 0.5, True), (1, 0.5), (6, 0.5),  # 低八度6
    (5, 1, True),   # 低八度5

    # 茉莉花呀茉莉花
    (6, 1), (6, 0.5), (1, 0.5),  # 低八度6
    (2, 2, True), (3, 1),

    # 茉莉花
    (8, 1), (2, 1), (8, 1), (6, 1),  # 高八度1、2
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


# ===== 调度状态 =====
idx = 0
MAX = len(SCORE)
finished = threading.Event()
shutdown_event = threading.Event()  # 用于跨线程传递关闭信号（Ctrl+C 、异常等）


def hit_next(robot: GalbotRobot):
    """处理一个 SCORE 事件。

    调度原理：
    - 事件本身占用的时间 = actual_time（不包含 RESET_DELAY 补足部分）
    - Timer delay = max(0.001, duration * INTERVAL - actual_time)
      · 0.5 拍事件间隔 0.5*0.7 = 0.35s → 连音
      · 1   拍事件间隔 1*0.7   = 0.7s  → 原始节奏
      · 2   拍事件间隔 2*0.7   = 1.4s
    """
    global idx
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

    target_press = duration * RESET_DELAY
    actual_time = target_press  # 不再“补足”到 RESET_DELAY

    if note != 0:
        end_effector, joint_idx, position = NOTE_MAP[note]
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
        timer = threading.Timer(delay, hit_next, args=(robot,))
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
    global idx
    robot = GalbotRobot()
    robot.init()
    time.sleep(1)

    try:
        # 弹奏前的初始姿势：双手全张开
        init_cmds = generateDexhandCommands(0, 1000)
        hit(robot, "left_dexhand",  init_cmds)
        hit(robot, "right_dexhand", init_cmds)

        # 重置状态
        idx = 0
        finished.clear()
        shutdown_event.clear()

        # 手动调度：直接调用 hit_next，它在完成后自动 Timer 下一次
        hit_next(robot)

        # 等待播放完成或被中断
        while not finished.is_set() and not shutdown_event.is_set():
            time.sleep(0.1)

        # 弹奏完毕后恢复弹奏前姿势
        if not shutdown_event.is_set():
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