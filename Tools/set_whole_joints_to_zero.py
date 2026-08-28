# 本代码功能：让机器人回到归零位姿

from galbot_sdk.g1 import GalbotRobot, GalbotMotion
import time


def main():
    robot = GalbotRobot()
    robot.init()
    motion = GalbotMotion()
    motion.init()
    time.sleep(1)

    status = motion.move_whole_body_joint_zero()
    print(f"status: {status}")

    robot.request_shutdown()
    robot.wait_for_shutdown()
    robot.destroy()


if __name__ == "__main__":
    main()
