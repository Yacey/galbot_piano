import math
import threading
import time
from pathlib import Path
from typing import Generator, List, Sequence, Tuple

import mujoco
import mujoco.viewer
import numpy as np

# MuJoCo 会根据 XML 路径解析 meshes/ 等相对资源。MJCF 与本模块同在 test/mjcf；
# 兼容旧部署中模块上一级目录放置、MJCF 与模块同级的两种目录结构。
_MODULE_DIR = Path(__file__).resolve().parent
_MJCF_FILENAME = "galbot_g1_v2_2_1_guader.xml"
_MJCF_CANDIDATES = (
    _MODULE_DIR / "mjcf" / _MJCF_FILENAME,
    _MODULE_DIR.parent / "mjcf" / _MJCF_FILENAME,
)
MJCF_PATH = next((path for path in _MJCF_CANDIDATES if path.exists()), _MJCF_CANDIDATES[0])

# 当前摆臂动作同时控制左右臂。顺序只用于依次建立两只手臂的 IK 状态。
SIDES = ["left", "right"]

# 左右臂是镜像结构。第 3 关节使用相反的偏移方向，可以让两个手肘同时向外展开。
SWIVEL_SIGN_BY_SIDE = {
    "left": 1.0,
    "right": -1.0,
}

# Python 下标 2 对应 left/right_arm_joint3，也就是当前用于引导肘部外摆的上臂
# roll 关节。这个目标只作为 null-space 姿态偏好，不是强制关节位置约束。
SWIVEL_INDEX = 2

# 手肘只在初始位姿和最大外扩位姿之间往复。两个值的单位都是 degree，当前最大
# 外扩偏好为 15 度，不会越过初始位姿进入内夹侧。
OUTWARD_MIN_DEG = 0.0
OUTWARD_MAX_DEG = 15.0

# 末端位置误差阈值，单位是 m。超过该值时表示手的位置没有被足够固定住。
POSITION_TOLERANCE_M = 1e-5

# 末端姿态误差阈值，单位是 rad。超过该值时表示手的方向没有被足够固定住。
ORIENTATION_TOLERANCE_RAD = 1e-4

# null-space 姿态偏好的单步增量阈值，单位是 rad。低于该值时表示当前偏好已经
# 没有明显可执行的冗余空间运动。
POSTURE_STEP_TOLERANCE_RAD = 1e-6

# MuJoCo Viewer 每显示一帧后的固定等待时间，单位是 s。这个值只控制可视化播放
# 节奏，不参与关节角生成；实际相邻帧间隔还会叠加下一帧 IK 的计算时间。
VIEWER_FRAME_WAIT_SECONDS = 0.02


def get_arm_joint_names(side: str) -> List[str]:
    """
    按 MJCF 中的命名规则生成一只手臂的 7 个关节名。
    """

    return [f"{side}_arm_joint{i}" for i in range(1, 8)]


def get_arm_qpos_indices(
    model: mujoco.MjModel,
    joint_names: List[str],
) -> np.ndarray:
    """
    把关节名解析成 qpos 下标。

    当前手臂的 7 个关节都是 hinge joint，所以每个关节对应一个 qpos 标量。
    """

    indices = []

    # 逐个解析关节，保证返回下标的顺序和 joint1..7 完全一致。
    for name in joint_names:
        joint_id = mujoco.mj_name2id(
            model,
            mujoco.mjtObj.mjOBJ_JOINT,
            name,
        )

        # 缺少任意关节都无法建立完整的 7 自由度 IK，因此直接终止本次调用。
        if joint_id < 0:
            raise RuntimeError(f"joint not found: {name}")

        indices.append(int(model.jnt_qposadr[joint_id]))

    return np.asarray(indices, dtype=np.int32)


def get_arm_dof_indices(
    model: mujoco.MjModel,
    joint_names: List[str],
) -> np.ndarray:
    """
    把关节名解析成 dof 下标。

    Jacobian 的列使用 dof 下标，不是 qpos 下标。对 hinge joint 来说二者数量
    相同，但两个索引空间不能混用。
    """

    indices = []

    # 按 joint1..7 的顺序保存 dof 下标，后续用它截取当前手臂的 Jacobian 列。
    for name in joint_names:
        joint_id = mujoco.mj_name2id(
            model,
            mujoco.mjtObj.mjOBJ_JOINT,
            name,
        )

        # 找不到关节时继续计算会得到错误的 Jacobian，因此立即抛出异常。
        if joint_id < 0:
            raise RuntimeError(f"joint not found: {name}")

        indices.append(int(model.jnt_dofadr[joint_id]))

    return np.asarray(indices, dtype=np.int32)


def get_body_pose(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    body_name: str,
) -> Tuple[int, np.ndarray, np.ndarray]:
    """
    读取 body 当前的世界系 pose。

    返回 body id、世界坐标位置和 3x3 世界系旋转矩阵。
    """

    body_id = mujoco.mj_name2id(
        model,
        mujoco.mjtObj.mjOBJ_BODY,
        body_name,
    )

    # 末端 body 是固定双手位姿的基准，找不到时不能继续执行摆臂动作。
    if body_id < 0:
        raise RuntimeError(f"body not found: {body_name}")

    # MuJoCo 的 xpos 和 xmat 会在下一次 mj_forward 时更新，因此这里保存副本作为
    # 整个摆动过程都不变的末端目标。
    pos = data.xpos[body_id].copy()
    mat = data.xmat[body_id].reshape(3, 3).copy()

    return body_id, pos, mat


def rotation_error(
    current_R: np.ndarray,
    target_R: np.ndarray,
) -> np.ndarray:
    """
    返回 current_R 到 target_R 的轴角旋转误差，单位是 rad。
    """

    # 先计算从当前方向旋转到目标方向所需的相对旋转矩阵。
    error_matrix = target_R @ current_R.T
    quat = np.empty(4)

    # MuJoCo 的姿态误差辅助函数使用 [w, x, y, z] 顺序的 quaternion。
    mujoco.mju_mat2Quat(
        quat,
        error_matrix.reshape(-1),
    )

    # 把相对 quaternion 转成可以直接用于 3 维旋转 Jacobian 的轴角向量。
    axis_angle = np.empty(3)
    mujoco.mju_quat2Vel(
        axis_angle,
        quat,
        1.0,
    )

    return axis_angle


def clamp_arm_to_joint_range(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    joint_names: List[str],
    arm_qpos_indices: np.ndarray,
) -> None:
    """
    把一只手臂的关节角限制在 MJCF joint range 内。

    这个限制会在写入初始角度后和每一步 IK 后执行，避免运动学循环把关节推到
    MJCF 定义的物理范围之外。
    """

    # 每个关节的范围可能不同，因此需要按名称分别读取并限制对应 qpos。
    for i, name in enumerate(joint_names):
        joint_id = mujoco.mj_name2id(
            model,
            mujoco.mjtObj.mjOBJ_JOINT,
            name,
        )
        low, high = model.jnt_range[joint_id]
        qpos_index = arm_qpos_indices[i]

        data.qpos[qpos_index] = np.clip(
            data.qpos[qpos_index],
            low,
            high,
        )


def calculate_outward_offset(
    frame_index: int,
    n_positions: int,
) -> float:
    """
    计算当前帧位于外扩侧的关节偏好偏移，返回值单位是 rad。

    frame_index 会持续递增，通过对 n_positions 取模得到当前周期内的帧。
    一个周期按照余弦曲线完成 OUTWARD_MIN_DEG -> OUTWARD_MAX_DEG ->
    OUTWARD_MIN_DEG，并在下一周期无跳变地重新开始。
    """

    # 配置使用角度便于调节，参与 IK 前统一转换成弧度。
    outward_min = math.radians(OUTWARD_MIN_DEG)
    outward_max = math.radians(OUTWARD_MAX_DEG)

    # 周期第 0 帧位于最小外扩位置；周期中点位于最大外扩位置。周期末尾不额外
    # 复制第 0 帧，下一周期的第 0 帧自然承担回到最小值的边界采样。
    cycle_frame_index = frame_index % n_positions
    phase = 2.0 * math.pi * cycle_frame_index / n_positions
    swing_ratio = 0.5 - 0.5 * math.cos(phase)

    return outward_min + (outward_max - outward_min) * swing_ratio


def solve_ik(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    body_id: int,
    joint_names: List[str],
    arm_qpos_indices: np.ndarray,
    arm_dof_indices: np.ndarray,
    target_pos: np.ndarray,
    target_R: np.ndarray,
    preferred_q: np.ndarray,
    *,
    max_iterations: int = 100,
    position_gain: float = 1.0,
    orientation_gain: float = 1.0,
    damping: float = 1e-3,
    preference_gain: float = 0.05,
) -> Tuple[bool, float, float]:
    """
    固定一只手的 6D 末端 pose，同时在 null-space 中偏向 preferred_q。

    preferred_q 是软偏好，不是硬约束。返回值中的 bool 只表示末端 pose 是否
    保持在误差阈值内，不表示偏好关节是否已经完全到达目标角度。
    """

    # Jacobian 缓冲区覆盖完整模型的 nv 列，计算后只截取当前手臂的 7 列。
    jac_pos = np.zeros((3, model.nv))
    jac_rot = np.zeros((3, model.nv))
    final_pos_error_norm = math.inf
    final_rot_error_norm = math.inf

    # 每一帧最多执行固定次数的局部线性 IK，使末端任务和姿态偏好共同收敛。
    for _ in range(max_iterations):
        # qpos 每轮都会变化，先做正运动学以刷新末端 pose 和 Jacobian 所需状态。
        mujoco.mj_forward(model, data)

        current_pos = data.xpos[body_id].copy()
        current_R = data.xmat[body_id].reshape(3, 3).copy()

        # 位置和姿态误差共同构成 6 维末端任务误差。
        pos_error = target_pos - current_pos
        rot_error = rotation_error(
            current_R,
            target_R,
        )
        final_pos_error_norm = np.linalg.norm(pos_error)
        final_rot_error_norm = np.linalg.norm(rot_error)
        error = np.concatenate(
            [
                position_gain * pos_error,
                orientation_gain * rot_error,
            ]
        )

        # 只有位置和方向同时满足阈值，才认为双手仍被固定在初始 pose。
        task_converged = (
            final_pos_error_norm < POSITION_TOLERANCE_M
            and final_rot_error_norm < ORIENTATION_TOLERANCE_RAD
        )

        # MuJoCo 返回全模型 Jacobian，当前 IK 只允许对应手臂的 7 个关节变化。
        mujoco.mj_jacBody(
            model,
            data,
            jac_pos,
            jac_rot,
            body_id,
        )
        J = np.vstack(
            [
                jac_pos[:, arm_dof_indices],
                jac_rot[:, arm_dof_indices],
            ]
        )

        # 阻尼最小二乘用于求解末端任务增量。1e-3 的阻尼用于降低奇异位姿附近
        # 的数值抖动，同时保持本地可视化中的末端跟踪精度。
        A = J @ J.T + damping**2 * np.eye(6)
        dq_task = J.T @ np.linalg.solve(
            A,
            error,
        )

        # 姿态偏好经过 null-space 投影后再叠加，避免第 3 关节的外摆偏好直接
        # 破坏末端位置和方向任务。
        current_q = data.qpos[arm_qpos_indices]
        posture_error = preferred_q - current_q
        J_pinv = J.T @ np.linalg.solve(
            A,
            np.eye(6),
        )
        null_space = np.eye(7) - J_pinv @ J
        dq_posture = preference_gain * null_space @ posture_error
        dq = dq_task + dq_posture

        # 末端已对齐且姿态偏好没有明显可执行增量时，本帧的 IK 已经完成。
        if task_converged and np.linalg.norm(dq_posture) < POSTURE_STEP_TOLERANCE_RAD:
            return True, float(final_pos_error_norm), float(final_rot_error_norm)

        # 单步关节增量限制在 0.05 rad 内，避免线性化 IK 单次变化过大产生抖动。
        max_step = 0.05
        dq = np.clip(
            dq,
            -max_step,
            max_step,
        )
        data.qpos[arm_qpos_indices] += dq

        # IK 更新后立即执行关节限位，再基于受限结果开始下一轮正运动学。
        clamp_arm_to_joint_range(
            model,
            data,
            joint_names,
            arm_qpos_indices,
        )

    # 达到最大迭代次数后重新计算最终 pose，确保返回的误差对应最后一次 qpos 更新。
    mujoco.mj_forward(model, data)
    current_pos = data.xpos[body_id].copy()
    current_R = data.xmat[body_id].reshape(3, 3).copy()
    pos_error = target_pos - current_pos
    rot_error = rotation_error(
        current_R,
        target_R,
    )
    final_pos_error_norm = np.linalg.norm(pos_error)
    final_rot_error_norm = np.linalg.norm(rot_error)

    # 最终状态仍只按照固定末端 pose 的两个阈值判断是否成功。
    task_converged = (
        final_pos_error_norm < POSITION_TOLERANCE_M
        and final_rot_error_norm < ORIENTATION_TOLERANCE_RAD
    )

    return (
        task_converged,
        float(final_pos_error_norm),
        float(final_rot_error_norm),
    )


def create_runtime(
    left_arm_q: Sequence[float],
    right_arm_q: Sequence[float],
) -> Tuple[mujoco.MjModel, mujoco.MjData, List[dict]]:
    """
    创建 MuJoCo model/data，并设置调用方传入的左右臂初始关节角。

    两组关节角的顺序必须分别对应 left/right_arm_joint1..7，单位是 rad。
    """

    # 关节角来自调用方，是当前函数的输入边界。这里统一转换成 float64 数组，并
    # 明确要求每只手臂恰好包含 7 个标量。
    initial_q_by_side = {
        "left": np.asarray(left_arm_q, dtype=np.float64),
        "right": np.asarray(right_arm_q, dtype=np.float64),
    }

    for side in SIDES:
        if initial_q_by_side[side].shape != (7,):
            raise ValueError(f"{side}_arm_q must contain exactly 7 joint positions")

        if not np.all(np.isfinite(initial_q_by_side[side])):
            raise ValueError(f"{side}_arm_q must contain only finite joint positions")

    # 每次调用都创建独立的 model/data，使停止后再次调用会从新的输入姿态重新开始。
    model = mujoco.MjModel.from_xml_path(str(MJCF_PATH))
    data = mujoco.MjData(model)
    arm_states = []

    # 先写入两只手臂的初始关节角。两只手都完成后再统一做正运动学，保证随后保存
    # 的两个末端目标来自同一个完整双臂姿态。
    for side in SIDES:
        joint_names = get_arm_joint_names(side)
        arm_qpos_indices = get_arm_qpos_indices(
            model,
            joint_names,
        )
        arm_dof_indices = get_arm_dof_indices(
            model,
            joint_names,
        )

        data.qpos[arm_qpos_indices] = initial_q_by_side[side]
        clamp_arm_to_joint_range(
            model,
            data,
            joint_names,
            arm_qpos_indices,
        )

        arm_states.append(
            {
                "side": side,
                "joint_names": joint_names,
                "arm_qpos_indices": arm_qpos_indices,
                "arm_dof_indices": arm_dof_indices,
                "body_name": f"{side}_arm_end_effector_mount_link",
            }
        )

    # 首次正运动学把传入关节角转换成左右手末端的固定世界系 pose。
    mujoco.mj_forward(model, data)

    for arm_state in arm_states:
        body_id, target_pos, target_R = get_body_pose(
            model,
            data,
            arm_state["body_name"],
        )
        initial_q = data.qpos[arm_state["arm_qpos_indices"]].copy()

        # 每只手臂独立保存末端目标、第 3 关节中心和实际应用限位后的初始角度。
        arm_state["body_id"] = body_id
        arm_state["target_pos"] = target_pos
        arm_state["target_R"] = target_R
        arm_state["center"] = initial_q[SWIVEL_INDEX]
        arm_state["initial_q"] = initial_q

    return model, data, arm_states


def generate_arm_swing_frames(
    left_arm_q: Sequence[float],
    right_arm_q: Sequence[float],
    n_positions: int,
) -> Generator[
    Tuple[List[float], List[float]],
    None,
    None,
]:
    """
    从传入的双臂姿态开始，逐帧生成持续往复的双臂关节角。

    n_positions 表示一个完整 0 度 -> 15 度 -> 0 度周期包含的采样点数。每个帧
    都是 (左臂 7 关节角, 右臂 7 关节角)。生成器不主动等待，也不关心播放停止
    信号；调用方负责控制消费节拍，并在停止消费后 close 生成器。
    """

    # 一个周期至少需要两个采样点才能表达关节角变化。明确要求 int，避免浮点数
    # 参与取模后产生不符合调用方预期的周期长度。
    if not isinstance(n_positions, int) or n_positions < 2:
        raise ValueError("n_positions must be an integer >= 2")

    # 每次创建生成器都建立独立的 IK 状态，以本次传入的关节角固定左右手末端 pose。
    model, data, arm_states = create_runtime(
        left_arm_q=left_arm_q,
        right_arm_q=right_arm_q,
    )
    frame_index = 0

    while True:
        # frame_index 持续递增，calculate_outward_offset 内部通过取模重复完整周期。
        offset = calculate_outward_offset(
            frame_index=frame_index,
            n_positions=n_positions,
        )

        # 左右臂使用相反符号的第 3 关节偏好，分别求解各自固定末端的 IK。
        for arm_state in arm_states:
            side = arm_state["side"]
            side_offset = SWIVEL_SIGN_BY_SIDE[side] * offset

            # 当前姿态作为偏好基底，只覆盖第 3 关节目标。其余关节由 null-space
            # IK 根据固定末端任务自动补偿，不强制回到初始关节角。
            preferred_q = data.qpos[arm_state["arm_qpos_indices"]].copy()
            preferred_q[SWIVEL_INDEX] = arm_state["center"] + side_offset

            ok, pos_error_norm, rot_error_norm = solve_ik(
                model=model,
                data=data,
                body_id=arm_state["body_id"],
                joint_names=arm_state["joint_names"],
                arm_qpos_indices=arm_state["arm_qpos_indices"],
                arm_dof_indices=arm_state["arm_dof_indices"],
                target_pos=arm_state["target_pos"],
                target_R=arm_state["target_R"],
                preferred_q=preferred_q,
            )

            # 任一帧无法保持末端 pose 时不输出可能用于实机执行的超差关节角。
            if not ok:
                raise RuntimeError(
                    f"{side} IK end-effector error is above tolerance at frame "
                    f"{frame_index}: "
                    f"position={pos_error_norm:.6e} m, "
                    f"orientation={rot_error_norm:.6e} rad"
                )

        # 两只手臂完成本帧 IK 后统一刷新正运动学，再从共享的 data.qpos 中分别
        # 提取固定顺序的 7 个关节角。
        mujoco.mj_forward(
            model,
            data,
        )
        joint_positions_by_side = {}

        for arm_state in arm_states:
            side = arm_state["side"]
            joint_positions_by_side[side] = [
                float(value)
                for value in data.qpos[arm_state["arm_qpos_indices"]].tolist()
            ]

        # tuple 第 0 项固定为左臂，第 1 项固定为右臂；每项都包含 7 个关节角。
        yield (
            joint_positions_by_side["left"],
            joint_positions_by_side["right"],
        )
        frame_index += 1


def generate_arm_swing_trajectory(
    left_arm_q: Sequence[float],
    right_arm_q: Sequence[float],
    n_positions: int,
) -> List[List[float]]:
    """
    一次性生成一个外摆再收回周期的双臂关节轨迹。

    返回列表包含 n_positions 个轨迹点。每个轨迹点固定包含 14 个关节角，顺序是
    左臂 joint1..7，随后是右臂 joint1..7，单位都是 rad。
    """

    # 有限轨迹需要起点、至少一个外摆点和终点，因此至少包含 3 个采样点。
    if not isinstance(n_positions, int) or n_positions < 3:
        raise ValueError("n_positions must be an integer >= 3")

    # 本函数拥有独立的 MuJoCo IK 状态，只计算指定数量的轨迹点，不持续循环。
    model, data, arm_states = create_runtime(
        left_arm_q=left_arm_q,
        right_arm_q=right_arm_q,
    )
    trajectory = []

    # 有限轨迹显式包含起点和终点，因此相位分母使用 n_positions - 1。第 0 帧
    # 严格位于 0 度偏移，最后一帧也严格回到 0 度偏移。
    for frame_index in range(n_positions):
        phase_ratio = frame_index / (n_positions - 1)
        phase = 2.0 * math.pi * phase_ratio
        swing_ratio = 0.5 - 0.5 * math.cos(phase)
        outward_min = math.radians(OUTWARD_MIN_DEG)
        outward_max = math.radians(OUTWARD_MAX_DEG)
        offset = outward_min + (outward_max - outward_min) * swing_ratio

        # 左右臂使用相反符号的第 3 关节偏好，并分别固定各自的初始末端 pose。
        for arm_state in arm_states:
            side = arm_state["side"]
            side_offset = SWIVEL_SIGN_BY_SIDE[side] * offset
            preferred_q = data.qpos[arm_state["arm_qpos_indices"]].copy()
            preferred_q[SWIVEL_INDEX] = arm_state["center"] + side_offset

            ok, pos_error_norm, rot_error_norm = solve_ik(
                model=model,
                data=data,
                body_id=arm_state["body_id"],
                joint_names=arm_state["joint_names"],
                arm_qpos_indices=arm_state["arm_qpos_indices"],
                arm_dof_indices=arm_state["arm_dof_indices"],
                target_pos=arm_state["target_pos"],
                target_R=arm_state["target_R"],
                preferred_q=preferred_q,
            )

            # 轨迹后续会直接用于机器人执行，任一帧末端超差时不返回不完整数据。
            if not ok:
                raise RuntimeError(
                    f"{side} IK end-effector error is above tolerance at frame "
                    f"{frame_index}: "
                    f"position={pos_error_norm:.6e} m, "
                    f"orientation={rot_error_norm:.6e} rad"
                )

        # 两只手臂完成本帧 IK 后刷新正运动学，再按左臂 7 个、右臂 7 个的固定
        # 顺序拼成一个可以转换为 SDK TrajectoryPoint 的 14 关节数组。
        mujoco.mj_forward(
            model,
            data,
        )
        joint_positions = []

        for arm_state in arm_states:
            joint_positions.extend(
                float(value)
                for value in data.qpos[arm_state["arm_qpos_indices"]].tolist()
            )

        trajectory.append(joint_positions)

    return trajectory


def visualize_arm_swing_trajectory(
    trajectory: Sequence[Sequence[float]],
) -> None:
    """
    在 MuJoCo Viewer 中按固定等待时间播放一次 14 关节双臂轨迹。

    每个轨迹点的顺序必须是左臂 joint1..7，随后是右臂 joint1..7。播放完成或
    Viewer 被手动关闭后函数返回，并由 Viewer 上下文自动关闭窗口。
    """

    # 轨迹是函数输入边界。一次性转换并校验每帧，避免 Viewer 打开后才因长度或
    # 非有限数值错误停在中间姿态。
    if len(trajectory) == 0:
        raise ValueError("trajectory must contain at least one position")

    trajectory_arrays = []

    for frame_index, joint_positions in enumerate(trajectory):
        frame = np.asarray(joint_positions, dtype=np.float64)

        if frame.shape != (14,):
            raise ValueError(
                f"trajectory frame {frame_index} must contain exactly 14 positions"
            )

        if not np.all(np.isfinite(frame)):
            raise ValueError(
                f"trajectory frame {frame_index} must contain only finite positions"
            )

        trajectory_arrays.append(frame)

    # 可视化只需要左右臂 qpos 下标，不建立新的末端 IK 目标，也不重新计算轨迹。
    model = mujoco.MjModel.from_xml_path(str(MJCF_PATH))
    data = mujoco.MjData(model)
    left_arm_qpos_indices = get_arm_qpos_indices(
        model,
        get_arm_joint_names("left"),
    )
    right_arm_qpos_indices = get_arm_qpos_indices(
        model,
        get_arm_joint_names("right"),
    )

    # Viewer 打开前先写入第一帧，避免窗口启动瞬间显示 MJCF 默认手臂姿态。
    data.qpos[left_arm_qpos_indices] = trajectory_arrays[0][:7]
    data.qpos[right_arm_qpos_indices] = trajectory_arrays[0][7:]
    mujoco.mj_forward(model, data)

    with mujoco.viewer.launch_passive(
        model,
        data,
    ) as viewer:
        for frame in trajectory_arrays:
            # 用户提前关闭 Viewer 时立即停止消费剩余轨迹点。
            if not viewer.is_running():
                break

            data.qpos[left_arm_qpos_indices] = frame[:7]
            data.qpos[right_arm_qpos_indices] = frame[7:]
            mujoco.mj_forward(
                model,
                data,
            )
            viewer.sync()

            # 每个轨迹点显示后固定等待同一时长，保持这个检查工具的播放逻辑简单。
            time.sleep(VIEWER_FRAME_WAIT_SECONDS)


def run_arm_swing(
    left_arm_q: Sequence[float],
    right_arm_q: Sequence[float],
    stop_event: threading.Event,
    n_positions: int,
) -> None:
    """
    在 MuJoCo Viewer 中按固定等待时间播放逐帧生成的双臂摆动关节角。

    这个函数是阻塞调用。主线程应当把它放入独立线程执行，并通过
    stop_event.set() 请求停止。消费循环发现事件后不再请求新帧，关闭生成器和
    Viewer 后返回；手动关闭 Viewer 也会执行相同的资源释放。
    """

    # 生成器逐帧执行 IK。消费者会在第一次 next 前检查停止事件，避免事件已经
    # 置位时触发生成器初始化和第一帧 IK。
    frame_generator = generate_arm_swing_frames(
        left_arm_q=left_arm_q,
        right_arm_q=right_arm_q,
        n_positions=n_positions,
    )

    try:
        # 消费方独立管理停止事件。调用前事件已经置位时，不触发生成器初始化和
        # 第一帧 IK，直接进入 finally 关闭尚未启动的生成器。
        if stop_event.is_set():
            return

        frame = next(frame_generator)

        # Viewer 使用独立的 MuJoCo data，只消费生成器关节角；IK 状态仍由生成器
        # 自己维护，方便后续实机消费者直接替换这里的 qpos 写入操作。
        model, data, arm_states = create_runtime(
            left_arm_q=left_arm_q,
            right_arm_q=right_arm_q,
        )
        arm_state_by_side = {arm_state["side"]: arm_state for arm_state in arm_states}
        # launch_passive 返回的 Viewer 由当前函数独占，退出 with 块时自动关闭窗口。
        with mujoco.viewer.launch_passive(
            model,
            data,
        ) as viewer:
            while viewer.is_running() and not stop_event.is_set():
                left_frame_q, right_frame_q = frame
                data.qpos[arm_state_by_side["left"]["arm_qpos_indices"]] = left_frame_q
                data.qpos[arm_state_by_side["right"]["arm_qpos_indices"]] = (
                    right_frame_q
                )

                # Viewer 只播放当前帧；下一帧的 IK 在本帧等待结束后由生成器计算。
                mujoco.mj_forward(
                    model,
                    data,
                )
                viewer.sync()

                # Viewer 使用固定等待时间控制播放速度。Event.wait 等价于可被停止
                # 信号立即打断的 sleep，事件置位后不会再请求下一帧。
                if stop_event.wait(VIEWER_FRAME_WAIT_SECONDS):
                    break

                if not viewer.is_running():
                    break

                frame = next(frame_generator)
    except StopIteration:
        # 生成器意外自然结束时，Viewer 消费函数正常返回并释放资源。
        return
    finally:
        # 手动关闭 Viewer 时生成器仍可能停在一次 yield 上，显式关闭以释放其
        # MuJoCo model/data 引用。
        frame_generator.close()
