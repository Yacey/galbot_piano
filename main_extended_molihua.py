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
INTERVAL = 0.35         # 基础调度间隔 = 0.35 秒（半拍）
                        # 1 拍 = 2 * INTERVAL = 0.7 秒
RESET_DELAY = 0.55     # 按下后多久归位（需大于手指物理完成松开动作的时间）


# ===== 音符到关节的映射（与 Twinkle 一致） =====
# 高八度（数字后加点如 "1."）沿用原指纲+位置；
# 实际音高由手校准决定，这里只是触发位置相同
NOTE_MAP = {
    # 低八度（base）
    1: ("left_dexhand",  5, 760),   # 1 = C4
    2: ("left_dexhand",  4, 830),   # 2 = D4
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
    # (3, 1), (3, 0.5), (5, 0.5), (6, 0.5), (8, 0.5), (8, 0.5), (6, 0.5),
    # (5, 1), (5, 0.5), (6, 0.5), (5, 2),

    # 好一朵美丽的茉莉花
    # (3, 1), (3, 0.5), (5, 0.5), (6, 0.5), (8, 0.5), (8, 0.5), (6, 0.5),
    # (5, 1), (5, 0.5), (6, 0.5), (5, 2),

    # # 芬芳美丽满枝丫
    # (5, 1), (5, 1), (5, 1), (3, 0.5), (5, 0.5),
    # (6, 1), (6, 1), (5, 2),

    # 又香又白人人夸
    (3, 1), (2, 0.5), (3, 0.5), (5, 1), (3, 0.5), (2, 0.5),
    (1, 1), (1, 0.5), (2, 0.5), (1, 2),
    # # 让我来将你摘下
    # (3, 1), (2, 1), (1, 1), (3, 1),
    # (2, 1, True), (3, 1),
    # (5, 1), (6, 1), (1, 1), (5, 2),
    # (2, 1), (3, 1), (5, 1), (2, 1), (3, 1),
    # (1, 1), (6, 1), (1, 2),
    # # 送给别人家
    # (1, 1), (6, 1), (1, 1),
    # (7, 1, True), (7, 1),
    # (1, 1), (2, 1), (3, 2),
    # (2, 1), (1, 1), (6, 1), (1, 1), (6, 1),
    # (5, 2), (6, 1), (1, 1),
    # (2, 1), (1, 1), (6, 1), (1, 1), (6, 1),
    # (5, 4),
    # # 间奏
    # (3, 1), (3, 1), (5, 1), (6, 1),
    # (1, 1, True), (1, 1), (6, 1),
    # (5, 1), (5, 1), (6, 1), (5, 1), (0, 1),
    # (3, 1), (3, 1), (5, 1), (6, 1),
    # (1, 1, True), (1, 1), (6, 1),
    # (5, 1), (5, 1), (6, 1), (5, 1), (0, 1),
    # (5, 1), (5, 1), (5, 1), (3, 1), (5, 1),
    # (6, 1), (6, 1), (5, 1), (0, 1),
    # # 主歌 2
    # (3, 1), (2, 1), (3, 1), (5, 1), (3, 1), (2, 1),
    # (1, 1), (1, 1), (2, 1), (1, 2),
    # (3, 1), (2, 1), (1, 1), (3, 1),
    # (2, 1, True), (3, 1),
    # (5, 1), (6, 1), (1, 1), (5, 2),
    # (2, 1), (3, 1), (5, 1), (2, 1), (3, 1),
    # (1, 1), (6, 1), (1, 2),
    # (2, 1, True), (3, 1), (1, 1), (2, 1), (1, 1), (6, 1),
    # (5, 2), (6, 1), (1, 1), (2, 2), (3, 1),
    # (1, 1), (2, 1), (1, 1), (6, 1),
    # (5, 4),
    # # 终曲
    # (3, 1), (3, 1), (5, 1), (6, 1),
    # (1, 1, True), (1, 1), (6, 1),
    # (5, 1), (5, 1), (6, 1), (5, 2),
    # (3, 1), (3, 1), (5, 1), (6, 1),
    # (1, 1, True), (1, 1), (6, 1),
    # (5, 1), (5, 1), (5, 1), (3, 1), (5, 1),
    # (6, 1), (6, 1), (5, 2),
    # (3, 1), (2, 1), (3, 1), (5, 1), (3, 1), (2, 1),
    # (1, 1), (1, 1), (2, 1), (1, 2),
    # (3, 1), (2, 1), (1, 1), (3, 1),
    # (2, 1, True), (3, 1),
    # (5, 1), (6, 1), (1, 1), (5, 2),
    # (2, 1), (3, 1), (5, 1), (2, 1), (3, 1),
    # (1, 1), (6, 1), (1, 2),
    # (2, 1, True), (3, 1), (1, 1), (2, 1), (1, 1), (6, 1),
    # (5, 2), (6, 1), (1, 1), (2, 2), (3, 1),
    # (1, 1), (2, 1), (1, 1), (6, 1),
    # (5, 4),
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
    SDK 返回非 SUCCESS 时输出警告（不抛异常）。
    """
    print(f"hit: {end_effector}, {commands}")
    status = robot.set_dexhand_command(
        end_effector=end_effector,
        dexhand_command=commands,
        is_blocking=False,
    )
    if status != ControlStatus.SUCCESS:
        print(f"bad status: {status}")


# ===== 调度状态 =====
idx = 0
MAX = len(SCORE)
finished = threading.Event()


def hit_next(robot: GalbotRobot):
    """apscheduler 回调：消费 SCORE 中的一条事件，控制手指按下/归位/休止。

    处理步骤：
      1) 解析 (note, duration) 或 (note, duration, is_extended)
         - note=0：休止符
         - note=1-7：音符
         - is_extended=布尔值：延音/连音，持续额外 1 拍
      2) 音符则按下；休止符则只等待
      3) 按 duration 时间按下并归位
         - 1 拍 = RESET_DELAY (0.55s)，保持原速
         - 短音符（duration < 1）：按 duration * RESET_DELAY 后立即归位
         - 长音符（duration > 1）：按下 RESET_DELAY + (duration-1) * RESET_DELAY
      4) 延音：再多压 1 拍（RESET_DELAY）后归位
      5) idx 自增
    """
    global idx

    if idx >= MAX:
        finished.set()
        return

    # 1) 解析事件
    event = SCORE[idx]
    if len(event) == 2:
        note, duration = event
        is_extended = False
    else:
        note, duration, is_extended = event

    # 1 拍 = RESET_DELAY (0.55s)，保持原速
    target_press = duration * RESET_DELAY

    if note != 0:
        end_effector, joint_idx, position = NOTE_MAP[note]

        # 2) 按下
        hit(robot, end_effector, generateDexhandCommands(joint_idx, position))

        # 3) 按 duration 时间按下
        if target_press < RESET_DELAY:
            # 短音符（减时线）：按 target_press 后立即归位
            time.sleep(target_press)
            hit(robot, end_effector, generateDexhandCommands(0, 1000))
        else:
            # 正常/长音符
            time.sleep(RESET_DELAY)
            if target_press > RESET_DELAY:
                time.sleep(target_press - RESET_DELAY)

            if is_extended:
                # 4) 延音：再多压 1 拍后归位
                time.sleep(RESET_DELAY)
                hit(robot, end_effector, generateDexhandCommands(0, 1000))
            else:
                # 标准归位
                hit(robot, end_effector, generateDexhandCommands(0, 1000))
    else:
        # 休止符
        time.sleep(target_press)

    # 5) 自增
    idx += 1

def main():
    """程序入口：连接机器人、调度器、播放《茉莉花》、清理。

    流程：
      1) 创建 GalbotRobot 并 init（连真机）
      2) 等待 1s 让灵巧手完成上电复位
      3) 把双手摆到张开状态
      4) 创建 BackgroundScheduler，注册 hit_next 任务（每 INTERVAL 触发）
      5) 启动 scheduler 并等待 finished 信号（所有事件消费完）
      6) 弹奏完毕后恢复弹奏前姿势（全部张开）
      7) finally：关闭 scheduler，释放机器人资源
    """
    robot = GalbotRobot()
    robot.init()
    time.sleep(1)
    scheduler = BackgroundScheduler()

    try:
        # 弹奏前的初始姿势：双手全张开
        init_cmds = generateDexhandCommands(0, 1000)
        hit(robot, "left_dexhand",  init_cmds)
        hit(robot, "right_dexhand", init_cmds)

        scheduler.add_job(
            func=hit_next,
            args=(robot,),
            trigger="interval",
            seconds=INTERVAL,
            coalesce=True,
        )
        scheduler.start()
        finished.wait()

        # 弹奏完毕后恢复弹奏前姿势
        hit(robot, "left_dexhand",  init_cmds)
        hit(robot, "right_dexhand", init_cmds)
        time.sleep(0.5)  # 等待手指物理上完成归位
    finally:
        scheduler.shutdown(wait=True)
        robot.request_shutdown()
        robot.wait_for_shutdown()
        robot.destroy()


if __name__ == "__main__":
    main()