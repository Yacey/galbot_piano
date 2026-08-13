# -*- coding: utf-8 -*-
"""灵巧手弹奏《小星星》—— 含延长音支持

6 个自由度的手指映射（reference）：
  1 ——> ("left_dexhand",  5, 760)   小拇指 = C4
  2 ——> ("left_dexhand",  4, 830)   无名指 = D4
  3 ——> ("left_dexhand",  3, 850)   中指   = E4
  4 ——> ("left_dexhand",  2, 830)   食指   = F4
  5 ——> ("right_dexhand", 2, 830)   食指   = G4
  6 ——> ("right_dexhand", 3, 850)   中指   = A4
  7 ——> ("right_dexhand", 4, 830)   无名指 = D5

EVENTS 格式：
  3-tuple: (end_effector, joint_idx, joint_position)         普通音（按 1 拍）
  4-tuple: (end_effector, joint_idx, joint_position, True)   延长音（按 2 拍）
"""
import threading
import time
from typing import List

from apscheduler.schedulers.background import BackgroundScheduler
from galbot_sdk.g1 import ControlStatus, GalbotRobot, JointCommand


# ===== 调速参数 =====
INTERVAL = 0.7                  # 一拍时长（秒）
RESET_DELAY = 0.60            # 按下后多久归位；需大于手指完成松开动作的时间
RESET_ON_EVERY_HIT = True       # True=每次都归位；False=智能判断（同一手指连按时才归位）

# ===== 小星星曲谱（6 段，约 34.3 秒） =====
EVENTS = [
    # 第 1 段：1 1 5 5 | 6 6 5 -
    ("left_dexhand",  5, 760),
    ("left_dexhand",  5, 760),
    ("right_dexhand", 2, 830),
    ("right_dexhand", 2, 830),
    ("right_dexhand", 3, 850),
    ("right_dexhand", 3, 850),
    ("right_dexhand", 2, 830, True),  # 延长音：G4

    # 第 2 段：4 4 3 3 | 2 2 1 -
    ("left_dexhand",  2, 830),
    ("left_dexhand",  2, 830),
    ("left_dexhand",  3, 850),
    ("left_dexhand",  3, 850),
    ("left_dexhand",  4, 830),
    ("left_dexhand",  4, 830),
    ("left_dexhand",  5, 760, True),  # 延长音：C4

    # 第 3 段：高八度 5 5 4 4 | 3 3 2 -
    ("right_dexhand", 2, 830),
    ("right_dexhand", 2, 830),
    ("left_dexhand",  2, 830),
    ("left_dexhand",  2, 830),
    ("left_dexhand",  3, 850),
    ("left_dexhand",  3, 850),
    ("left_dexhand",  4, 830, True),  # 延长音：D5

    # 第 4 段：5 5 4 4 | 3 3 2 -（注意：原版 main.py 漏了 1 个 C5）
    ("right_dexhand", 2, 830),
    ("right_dexhand", 2, 830),
    ("left_dexhand",  2, 830),
    ("left_dexhand",  2, 830),
    ("left_dexhand",  3, 850),
    ("left_dexhand",  3, 850),
    ("left_dexhand",  4, 830, True),         # D4（无延长音）

    # 第 5 段：A' 重复 1 1 5 5 | 6 6 5 -
    ("left_dexhand",  5, 760),
    ("left_dexhand",  5, 760),
    ("right_dexhand", 2, 830),
    ("right_dexhand", 2, 830),
    ("right_dexhand", 3, 850),
    ("right_dexhand", 3, 850),
    ("right_dexhand", 2, 830, True),  # 延长音：G4

    # 第 6 段：A' 重复 4 4 3 3 | 2 2 1 -（终止）
    ("left_dexhand",  2, 830),
    ("left_dexhand",  2, 830),
    ("left_dexhand",  3, 850),
    ("left_dexhand",  3, 850),
    ("left_dexhand",  4, 830),
    ("left_dexhand",  4, 830),
    ("left_dexhand",  5, 760, True),  # 延长音：C4（终止）
]


def generateDexhandCommands(joint_idx: int, joint_position: int) -> List[JointCommand]:
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


def hit(robot: GalbotRobot, end_effector: str, commands: List[JointCommand]):
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
MAX = len(EVENTS)
finished = threading.Event()


def hit_next(robot: GalbotRobot):
    """apscheduler 回调：消费 EVENTS 中的一条事件，让一根手指按下/归位。

    处理步骤：
      1) 取 EVENTS[idx]，支持 3-tuple 或 4-tuple
         - 3-tuple：普通音（按 1 拍，默认归位）
         - 4-tuple：末尾 True 表示延长音（按 2 拍，不归位）
      2) 调用 hit() 按下对应手指
      3) 判断是否归位
      4) 延长音：多 sleep 2*INTERVAL 拍，把下一个事件推迟 1 拍到达
      5) idx 自增
    """
    global idx

    if idx >= MAX:
        finished.set()
        return

    # 1) 解析事件
    event = EVENTS[idx]
    end_effector = event[0]
    joint_idx = event[1]
    joint_position = event[2]
    is_extended = len(event) >= 4 and event[3]

    # 2) 按下
    hit(robot, end_effector, generateDexhandCommands(joint_idx, joint_position))

    # 3) 是否归位
    should_reset = RESET_ON_EVERY_HIT
    if is_extended:
        should_reset = False  # 延长音：按住不归位
    elif not should_reset and idx < MAX - 1:
        # 同手同指连按时强制归位（避免连续 press 变成空指令）
        # 不归位则舵机已在目标位置，下一次 press 转不动 = 无效指令
        next_event = EVENTS[idx + 1]
        if next_event[0] == end_effector and next_event[1] == joint_idx:
            should_reset = True

    if should_reset:
        time.sleep(RESET_DELAY)
        hit(robot, end_effector, generateDexhandCommands(0, 1000))

    # 4) 延长音：推迟下一个事件 1 拍（按住 2 拍的总时长）
    # 为什么是 2*INTERVAL 而不是 INTERVAL：
    # apscheduler 在 hit_next 阻塞期间不会触发 fire，sleep 1 拍只会被 scheduler
    # 立即"吃掉"（下一次 fire 仍在 T+INTERVAL 触发）。必须 sleep 整 2 拍才能让
    # 下一个 fire 在 T+2*INTERVAL 触发，达到真正按住 2 拍的效果。
    if is_extended:
        time.sleep(INTERVAL)
        # 延长音 hold 结束后显式归位，避免跨手"幽灵按下"：
        # 下事件若是另一只手，则原本这只手的指纲会始终
        # 保持上一个延长音的按下状态，造成"第一个 5 才不动"这种纯路。
        hit(robot, end_effector, generateDexhandCommands(0, 1000))

    # 5) 自增
    idx += 1


def main():
    """程序入口：连接机器人、调度器、播放、清理。

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
