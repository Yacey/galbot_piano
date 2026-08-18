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
INTERVAL = 0.7          # 一拍 = 0.7 秒
RESET_DELAY = 0.55     # 按下后多久归位（需大于手指物理完成松开动作的时间）


# ===== 音符到关节的映射（与 Twinkle 一致） =====
# 高八度（数字后加点如 "1."）沿用原指纲+位置；
# 实际音高由手校准决定，这里只是触发位置相同
NOTE_MAP = {
    1: ("left_dexhand",  5, 760),   # 1 = C
    2: ("left_dexhand",  4, 830),   # 2 = D
    3: ("left_dexhand",  3, 850),   # 3 = E
    4: ("left_dexhand",  2, 830),   # 4 = F
    5: ("right_dexhand", 2, 830),   # 5 = G
    6: ("right_dexhand", 3, 850),   # 6 = A
    7: ("right_dexhand", 4, 830),   # 7 = B
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
    (0, 1), (0, 1), (5, 2), (2, 2), (0, 1), (0, 1),
    (1, 2), (0, 1), (0, 1), (2, 4),
    # 前奏 2
    (5, 4), (5, 4), (5, 4), (5, 4),
    # 好一朵美丽的茉莉花
    (3, 1), (3, 1), (5, 1), (6, 1),
    (1, 1, True), (1, 1), (6, 1),
    (5, 1), (5, 1), (6, 1), (5, 2),
    # 芬芳美丽满枝丫
    (3, 1), (3, 1), (5, 1), (6, 1),
    (1, 1, True), (1, 1), (6, 1),
    (5, 1), (5, 1), (5, 1), (3, 1), (5, 1),
    (6, 1), (6, 1), (5, 2),
    # 又香又白人人夸
    (3, 1), (2, 1), (3, 1), (5, 1), (3, 1), (2, 1),
    (1, 1), (1, 1), (2, 1), (1, 2),
    # 让我来将你摘下
    (3, 1), (2, 1), (1, 1), (3, 1),
    (2, 1, True), (3, 1),
    (5, 1), (6, 1), (1, 1), (5, 2),
    (2, 1), (3, 1), (5, 1), (2, 1), (3, 1),
    (1, 1), (6, 1), (1, 2),
    # 送给别人家
    (1, 1), (6, 1), (1, 1),
    (7, 1, True), (7, 1),
    (1, 1), (2, 1), (3, 2),
    (2, 1), (1, 1), (6, 1), (1, 1), (6, 1),
    (5, 2), (6, 1), (1, 1),
    (2, 1), (1, 1), (6, 1), (1, 1), (6, 1),
    (5, 4),
    # 间奏
    (3, 1), (3, 1), (5, 1), (6, 1),
    (1, 1, True), (1, 1), (6, 1),
    (5, 1), (5, 1), (6, 1), (5, 1), (0, 1),
    (3, 1), (3, 1), (5, 1), (6, 1),
    (1, 1, True), (1, 1), (6, 1),
    (5, 1), (5, 1), (6, 1), (5, 1), (0, 1),
    (5, 1), (5, 1), (5, 1), (3, 1), (5, 1),
    (6, 1), (6, 1), (5, 1), (0, 1),
    # 主歌 2
    (3, 1), (2, 1), (3, 1), (5, 1), (3, 1), (2, 1),
    (1, 1), (1, 1), (2, 1), (1, 2),
    (3, 1), (2, 1), (1, 1), (3, 1),
    (2, 1, True), (3, 1),
    (5, 1), (6, 1), (1, 1), (5, 2),
    (2, 1), (3, 1), (5, 1), (2, 1), (3, 1),
    (1, 1), (6, 1), (1, 2),
    (2, 1, True), (3, 1), (1, 1), (2, 1), (1, 1), (6, 1),
    (5, 2), (6, 1), (1, 1), (2, 2), (3, 1),
    (1, 1), (2, 1), (1, 1), (6, 1),
    (5, 4),
    # 终曲
    (3, 1), (3, 1), (5, 1), (6, 1),
    (1, 1, True), (1, 1), (6, 1),
    (5, 1), (5, 1), (6, 1), (5, 2),
    (3, 1), (3, 1), (5, 1), (6, 1),
    (1, 1, True), (1, 1), (6, 1),
    (5, 1), (5, 1), (5, 1), (3, 1), (5, 1),
    (6, 1), (6, 1), (5, 2),
    (3, 1), (2, 1), (3, 1), (5, 1), (3, 1), (2, 1),
    (1, 1), (1, 1), (2, 1), (1, 2),
    (3, 1), (2, 1), (1, 1), (3, 1),
    (2, 1, True), (3, 1),
    (5, 1), (6, 1), (1, 1), (5, 2),
    (2, 1), (3, 1), (5, 1), (2, 1), (3, 1),
    (1, 1), (6, 1), (1, 2),
    (2, 1, True), (3, 1), (1, 1), (2, 1), (1, 1), (6, 1),
    (5, 2), (6, 1), (1, 1), (2, 2), (3, 1),
    (1, 1), (2, 1), (1, 1), (6, 1),
    (5, 4),
]


def generateDexhandCommands(joint_idx: int, joint_position: int) -> list:
    """构造一帧 6 个 JointCommand 的指令列表。

    除指定的 joint_idx 手指外，其他 5 个手指的 position 都设为 1000（张开），
    目标手指的 position 设为 joint_position（弯曲）。
    一次性发送完整 6-DoF 角度数据，避免手指在多次发送间产生不一致的中间状态。
    """
    cmds = [JointCommand() for _ in range(6)]
    for cmd in cmds:
        cmd.position = 1000
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
      1) 取 SCORE[idx]，支持 (note, duration) 或 (note, duration, True)
         - note=0：休止符
         - note=1-7：音符
         - 末尾 True：表示这是延音/连音线的第一个音（duration 自动 +1 拍）
      2) 音符则按下并归位；休止符则只等待
      3) 按 duration 拍数阻塞（每拍 = INTERVAL）
         - 音符 1 拍 = RESET_DELAY（归位物理时间）
         - 音符 N 拍 = RESET_DELAY + (N-1) * INTERVAL
         - 休止 N 拍 = N * INTERVAL（无操作）
      4) 跨手自动归位：延长音结束后显式 release，避免下个事件在
         另一只手时出现幽灵按下
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
    else:
        note, duration, _ = event

    # 2) 按下（休止符不操作）
    if note != 0:
        end_effector, joint_idx, position = NOTE_MAP[note]
        hit(robot, end_effector, generateDexhandCommands(joint_idx, position))
        time.sleep(RESET_DELAY)

    # 3) 按拍数阻塞
    if note != 0:
        extra_beats = duration - 1
    else:
        extra_beats = duration
    if extra_beats > 0:
        time.sleep(extra_beats * INTERVAL)

    # 4) 归位（休止符不操作）
    if note != 0:
        hit(robot, end_effector, generateDexhandCommands(0, 1000))

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