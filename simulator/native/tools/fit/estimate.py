"""Register the key poses using labelled pixels and native command replay."""
import json
import subprocess

import numpy as np

from geometry import MAP, NATIVE, document, rotate_xz


def command_intervals(frames, ids, fps):
    reports = NATIVE / 'build/run6/reports'
    records = [document(reports / (frame.stem.removesuffix('_front_camera') + '.json'))['report']
               for frame in frames[:ids[-1] + 1]]
    intervals = []
    for start, end in zip(ids[:-1], ids[1:], strict=True):
        commands = []
        for index in range(start, end):
            record = records[index]
            output = record['cycle']['action']['authority']['authorized_output']
            elapsed = (records[index + 1]['timestamp_ms'] - record['timestamp_ms']) / 1000
            if elapsed <= 0:
                raise ValueError(f'Non-increasing timestamp at {frames[index].name}')
            commands.append(dict(throttle=output['throttle'], steering=output['steering'],
                                 steps=round(elapsed * fps)))
        intervals.append(commands)
    return intervals


def replay(intervals, starts, gain):
    data = [dict(start=p.tolist(), commands=[dict(cmd, throttle=cmd['throttle'] * gain)
                                            for cmd in commands])
            for p, commands in zip(starts, intervals, strict=True)]
    output = subprocess.check_output([NATIVE / 'build/native-fit-motion', MAP],
                                     input=json.dumps(data), text=True)
    return np.array([json.loads(line) for line in output.splitlines()])


def wrapped_delta(poses, other):
    delta = poses - other
    delta[:, 2] = np.arctan2(np.sin(delta[:, 2]), np.cos(delta[:, 2]))
    return delta


def speed_gain(intervals, poses):
    # The camera and the real motor have no shared speed calibration. Compare
    # short native predictions to visual displacements, resetting at each key.
    gains = (.05, .08, .12, .18, .26, .38, .55, .8, 1.)

    def error(gain):
        delta = wrapped_delta(replay(intervals, poses[:-1], gain), poses[1:])
        return np.mean(np.minimum(np.linalg.norm(delta * [1, 1, .30], axis=1), .6))

    return min(gains, key=error)


def least_squares(residual, initial):
    """Small damped Gauss-Newton fit. Every accepted step reduces the objective."""
    values = initial.copy()
    steps = np.eye(values.size).reshape(-1, *values.shape) * 1e-5
    damping = .01
    for _ in range(55):
        error = residual(values)
        jac = np.column_stack([(residual(values + step) - error) / 1e-5 for step in steps])
        hessian = jac.T @ jac
        change = np.linalg.solve(hessian + np.diag(damping * (1 + hessian.diagonal())),
                                 -jac.T @ error).reshape(values.shape)
        proposal = values + change
        if np.linalg.norm(residual(proposal)) < np.linalg.norm(error):
            values = proposal
            damping = max(1e-6, damping / 2)
        else:
            damping *= 4
        if np.max(np.abs(change)) < 2e-6:
            break
    return values


class Registration:
    def __init__(self, scene, obs):
        self.scene = scene
        self.obs = obs
        self.points = scene.points(scene.placements)
        self.contacts = {i: (scene.names.index(face['object']), face['zFace'])
                         for i, face in obs.contacts}
        bounds = scene.map['map']
        self.limits = np.array([bounds['columns'], bounds['rows']])/2 - scene.map['vehicle']['radius']

    def projection(self, poses):
        return self.obs.project(self.scene.map['camera'], poses, self.points)

    def contact_point(self, index, pose):
        target, sign = self.contacts[index]
        x, z, yaw = self.scene.placements[target]
        local_x, _ = rotate_xz(pose[0] - x, pose[1] - z, -yaw)
        half = self.scene.footprints[target]/2 + self.scene.map['vehicle']['radius']
        along = np.clip(local_x, -half[0], half[0])
        return np.array([x, z]) + rotate_xz(along, sign * half[1], yaw)

    def outside_errors(self, poses):
        """A cropped-out target must clear the pinhole raster, including its repeated edge."""
        camera = self.scene.map['camera']
        focal = 240 / np.tan(np.deg2rad(camera['verticalFovDegrees']) / 2)
        errors = np.zeros(len(poses))
        for index, anchor in self.obs.outside_right:
            x, y, z = self.points[anchor]
            px, pz, yaw = poses[index]
            ahead, side = rotate_xz(x - px, z - pz, yaw)
            depth, _ = rotate_xz(ahead, y - camera['height'], -camera['pitchDownRadians'])
            if depth > camera['near']:
                errors[index] += max(0., 321 - focal * side / depth)
        return errors

    def constraints(self, poses):
        clearance = self.scene.clearance(poses, self.scene.placements)
        return np.concatenate([np.minimum(clearance, 0).ravel(),
                               np.maximum(abs(poses[:, :2]) - self.limits, 0).ravel(),
                               np.array([poses[i, :2] - self.contact_point(i, poses[i])
                                         for i in self.contacts]).ravel()])

    def fit(self, initial, prior, prior_weights):
        def residual(poses):
            uv = self.projection(poses)[0]
            delta = uv - self.obs.pixels
            return np.concatenate([(delta / np.sqrt(1 + abs(delta) / 32)).ravel(),
                                   (wrapped_delta(poses, prior) * prior_weights).ravel(),
                                   self.constraints(poses) * 2200, self.outside_errors(poses) * 3])
        return least_squares(residual, initial)

    def feasible(self, index, pose):
        """Put a contact exactly on the swept-box face, including native padding."""
        result = pose.copy()
        if index in self.contacts:
            result[:2] = self.contact_point(index, pose)
        gap = self.scene.clearance(result[None], self.scene.placements)
        if gap.min() < -1e-12 or (abs(result[:2]) > self.limits).any():
            return None
        return result


def estimate(reg, frames):
    obs = reg.obs
    fps = reg.scene.map['fps']
    intervals = command_intervals(frames, obs.ids, fps)
    # First register visually from coarse top-view guesses. The recorded commands
    # then constrain each successive camera interval, with more slack across gaps.
    initial = obs.seeds.copy()
    for i in reg.contacts:
        initial[i, :2] = reg.contact_point(i, initial[i])
    poses = reg.fit(initial, obs.seeds, np.array([20., 20., 14.]))
    gain = speed_gain(intervals, poses)
    seconds = np.array([sum(cmd['steps'] for cmd in group)/fps for group in intervals])
    weights = np.array([60., 60., 35.]) / (1 + np.r_[0, seconds][:, None] * .45)
    for _ in range(3):
        prior = np.vstack([obs.seeds[0], replay(intervals, poses[:-1], gain)])
        poses = reg.fit(poses, prior, weights)
    return poses, prior, weights, gain
