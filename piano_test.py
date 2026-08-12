import threading
import time
from typing import List

from apscheduler.schedulers.background import BackgroundScheduler
from galbot_sdk.g1 import ControlStatus, GalbotRobot, JointCommand

INTERVAL = 0.7  # 按键间隔
VELOCITY = 1000
RESET_ON_EVERY_HIT = True  # 每弹一下手指都归位，关掉可以减少多余动作
RESET_DELAY = INTERVAL / 2  # 按下后什么时候归位


EVENTS = [

    # 1 ——>   ("left_dexhand", 5, 760),  小拇指
    # 2 ——>   ("left_dexhand", 4, 830),  无名指
    # 3 ——>   ("left_dexhand", 3, 850),  中指
    # 4 ——>   ("left_dexhand", 2, 830),  食指
    # 5 ——>   ("right_dexhand", 2, 830), 食指
    # 6 ——>   ("right_dexhand", 3, 850), 中指
    # 7 ——>   ("right_dexhand", 4, 830), 无名指

    # ( end_effector, joint_idx, joint_position )
    #   1   1   5   5  |  6   6   5   -  |
    ("left_dexhand", 5, 760),
    ("left_dexhand", 5, 760),
    ("right_dexhand", 2, 830),
    ("right_dexhand", 2, 830),
    ("right_dexhand", 3, 850),
    ("right_dexhand", 3, 850),
    ("right_dexhand", 2, 830),



    #   4   4   3   3  |  2   2   1   -  |
    ("left_dexhand", 2, 830),
    ("left_dexhand", 2, 830),
    ("left_dexhand", 3, 850),
    ("left_dexhand", 3, 850),
    ("left_dexhand", 4, 830),
    ("left_dexhand", 4, 830),
    ("left_dexhand", 5, 760),

    #   5̇   5̇   4̇   4̇  |  3̇   3̇   2̇   -  |
    ("right_dexhand", 2, 830),
    ("right_dexhand", 2, 830),
    ("left_dexhand", 2, 830),
    ("left_dexhand", 2, 830),
    ("left_dexhand", 3, 850),
    ("left_dexhand", 3, 850),
    ("left_dexhand", 4, 830),


    #   5   5   4   4  |  3   3   2  - |
    ("right_dexhand", 2, 830),
    ("right_dexhand", 2, 830),
    ("left_dexhand", 2, 830),
    ("left_dexhand", 2, 830),
    ("left_dexhand", 3, 850),
    ("left_dexhand", 3, 850),
    ("left_dexhand", 4, 830),


    #   1   1   5   5  |  6   6   5   -  |  (A 段重复)
    ("left_dexhand", 5, 760),
    ("left_dexhand", 5, 760),
    ("right_dexhand", 2, 830),
    ("right_dexhand", 2, 830),
    ("right_dexhand", 3, 850),
    ("right_dexhand", 3, 850),
    ("right_dexhand", 2, 830),


    #   4   4   3   3  |  2   2   1   -  |
    ("left_dexhand", 2, 830),
    ("left_dexhand", 2, 830),
    ("left_dexhand", 3, 850),
    ("left_dexhand", 3, 850),
    ("left_dexhand", 4, 830),
    ("left_dexhand", 4, 830),
    ("left_dexhand", 5, 760),
    
]


def generateDexhandCommands(joint_idx: int, joint_position: int) -> List[JointCommand]:
    cmds = []
    for _ in range(6):
        cmd = JointCommand()
        cmd.position = 1000
        cmds.append(cmd)
    cmds[joint_idx].position = joint_position
    return cmds


def hit(robot: GalbotRobot, end_effector: str, commands: List[JointCommand]):
    print(f"hit: {end_effector}, {commands}")
    status = robot.set_dexhand_command(
        end_effector=end_effector,
        dexhand_command=commands,
        is_blocking=False,
    )
    if status != ControlStatus.SUCCESS:
        print(f"bad status: {status}")


idx = 0
MAX = len(EVENTS)
finished = threading.Event()


def hit_next(robot: GalbotRobot):
    global idx

    if idx >= MAX:
        finished.set()
        return

    # 按下去
    event = EVENTS[idx]
    end_effector, joint_idx, joint_position = event
    commands = generateDexhandCommands(joint_idx, joint_position)
    hit(robot=robot, end_effector=end_effector, commands=commands)

    # 检查是否应该让手指归位
    should_reset = RESET_ON_EVERY_HIT
    if not should_reset and idx < MAX - 1:
        # 当前用的手指和后面是同一个时，按下去后必须立即弹回
        next = EVENTS[idx + 1]
        if next[0] == event[0] and next[1] == event[1]:
            should_reset = True

    # 重置手指
    if should_reset:
        time.sleep(RESET_DELAY)
        cmds = generateDexhandCommands(0, 1000)
        hit(robot, end_effector, cmds)

    idx = idx + 1


def main():
    robot = GalbotRobot()
    robot.init()
    time.sleep(1)
    scheduler = BackgroundScheduler()

    try:
        # 左右手指归位
        hit(robot, "left_dexhand", generateDexhandCommands(0, 1000))
        hit(robot, "right_dexhand", generateDexhandCommands(0, 1000))

        # scheduler = AsyncIOScheduler()
        scheduler.add_job(
            func=hit_next,
            args=(robot,),
            trigger="interval",
            seconds=INTERVAL,
            coalesce=True,
        )
        scheduler.start()
        finished.wait()

    finally:
        scheduler.shutdown(wait=True)
        robot.request_shutdown()
        robot.wait_for_shutdown()
        robot.destroy()


if __name__ == "__main__":
    main()
