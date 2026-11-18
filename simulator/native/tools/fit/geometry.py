"""Landmarks on native visual parts, in the 640 x 480 run 6 camera."""
import json
import subprocess
from pathlib import Path

import numpy as np

NATIVE = Path(__file__).resolve().parents[2]
ROOT = NATIVE.parents[1]
MAP = NATIVE / 'maps/room-run6.json'
BEFORE = '1807d284'
CENTER = np.array([319.5, 239.5])
HALF_SIZE = np.array([320., 240.])


def document(path, revision=None):
    if revision is None:
        return json.loads(path.read_text())
    return json.loads(subprocess.check_output(
        ['git', 'show', f'{revision}:{path.relative_to(ROOT)}'], cwd=ROOT))


def rotate_xz(x, z, yaw):
    c, s = np.cos(yaw), np.sin(yaw)
    return c * x + s * z, -s * x + c * z


def part_point(part, fraction):
    x, y, z = np.array(fraction) * [part['width'], part['height'], part['depth']]
    x, y = rotate_xz(x, y, -part.get('rollRadians', 0))
    y, z = rotate_xz(y, z, -part.get('pitchRadians', 0))
    x, z = rotate_xz(x, z, part['rotationRadians'])
    return [part['x'] + x, part['y'] + y, part['z'] + z]


class Scene:
    def __init__(self, marks, revision=None):
        self.map = document(MAP, revision)
        self.objects = self.map['map']['instances']
        self.names = [obj['id'] for obj in self.objects]
        self.placements = np.array([[obj['x'], obj['z'], obj['rotationRadians']]
                                    for obj in self.objects])
        assets = {name: document(NATIVE / 'assets/objects' / f'{name}.json', revision)
                  for name in {obj['asset'] for obj in self.objects}}
        self.footprints = np.array([
            [shape['width'], shape['depth']] if shape else [0, 0]
            for obj in self.objects for shape in [assets[obj['asset']]['collision']]])
        points, owners = [], []
        for obj, index, *fraction in marks['anchors'].values():
            owner = self.names.index(obj)
            part = assets[self.objects[owner]['asset']]['visuals'][index]
            points.append(part_point(part, fraction))
            owners.append(owner)
        self.local = np.array(points)
        self.owners = owners

    def points(self, placements):
        ox, oz, yaw = placements[self.owners].T
        x, z = rotate_xz(self.local[:, 0], self.local[:, 2], yaw)
        return np.column_stack([ox + x, self.local[:, 1], oz + z])

    def clearance(self, poses, placements):
        """Signed distance to the padded boxes used by validate_vehicle_pose."""
        ox, oz, yaw = placements.T
        x, z = rotate_xz(poses[:, 0:1] - ox, poses[:, 1:2] - oz, -yaw)
        radius = self.map['vehicle']['radius']
        gaps = np.maximum(np.abs(x) - self.footprints[:, 0] / 2,
                          np.abs(z) - self.footprints[:, 1] / 2) - radius
        gaps[:, self.footprints[:, 0] == 0] = np.inf
        return gaps


class Observations:
    def __init__(self, marks):
        frames = marks['frames']
        self.ids = [int(i) for i in frames]
        self.support = [i for i, item in enumerate(frames.values()) if item.get('support')]
        self.seeds = np.array([f['seed'] for f in frames.values()])
        self.contacts = [(i, f['contact']) for i, f in enumerate(frames.values()) if 'contact' in f]
        anchors = list(marks['anchors'])
        self.outside_right = [(i, anchors.index(name)) for i, item in enumerate(frames.values())
                              for name in item.get('outsideRight', [])]
        frame, endpoints, pixels = [], [], []
        for i, item in enumerate(frames.values()):
            for name, uv in item['marks'].items():
                ends = marks['segments'].get(name, [name, name])
                endpoints.append([anchors.index(end) for end in ends])
                pixels.append(uv)
                frame.append(i)
        self.frame = np.array(frame)
        self.ends = np.array(endpoints)
        self.pixels = np.array(pixels)

    def camera_coordinates(self, camera, poses, points):
        p = poses[self.frame]
        xyz = points[self.ends].copy()
        xyz[:, :, 0] -= p[:, 0:1]
        xyz[:, :, 1] -= camera['height']
        xyz[:, :, 2] -= p[:, 1:2]
        ahead, side = rotate_xz(xyz[:, :, 0], xyz[:, :, 2], p[:, 2:3])
        depth, up = rotate_xz(ahead, xyz[:, :, 1], -camera['pitchDownRadians'])
        return depth, np.stack([side, -up], axis=2)

    def project(self, camera, poses, points):
        depth, xy = self.camera_coordinates(camera, poses, points)
        near = camera['near']
        xy = clip_and_project(xy, depth, near, camera['verticalFovDegrees'])
        barrel = camera['barrelDistortion']
        ray = self.pixels - CENTER
        target = ray * (1 + barrel * np.sum((ray / HALF_SIZE)**2, axis=1, keepdims=True))
        pinhole = closest_on_edges(xy, target)
        in_front = depth.max(axis=1) >= near
        in_raster = in_front & (abs(pinhole) <= CENTER).all(axis=1)
        return distort(pinhole, barrel), in_raster, in_front


def clip_and_project(xy, depth, near, vertical_fov):
    # Clip a crossing segment at the near plane before perspective division.
    clipped = np.maximum(depth, near)
    denom = depth[:, ::-1] - depth
    t = np.clip((clipped - depth) / np.where(abs(denom) > 1e-9, denom, 1), 0, 1)
    xy = xy + t[:, :, None] * (xy[:, ::-1] - xy)
    return xy * (240 / np.tan(np.deg2rad(vertical_fov) / 2)) / clipped[:, :, None]


def closest_on_edges(xy, target):
    edge = xy[:, 1] - xy[:, 0]
    along = np.sum((target - xy[:, 0]) * edge, axis=1) / np.maximum(np.sum(edge**2, axis=1), 1e-9)
    return xy[:, 0] + np.clip(along, 0, 1)[:, None] * edge


def distort(pinhole, barrel):
    """Invert the native sensor's output-to-source radial sampling."""
    radius = np.linalg.norm(pinhole / HALF_SIZE, axis=1)
    out = np.cbrt(radius / barrel) if barrel else radius.copy()
    for _ in range(9):
        out -= (out + barrel * out**3 - radius) / (1 + 3 * barrel * out**2)
    return CENTER + pinhole / (1 + barrel * out[:, None]**2)

def vanishing_fov(lines, barrel):
    """Vertical FOV from two orthogonal bed directions, each traced at two heights."""
    pixels = np.asarray(lines) - CENTER
    source = pixels * (1 + barrel * np.sum((pixels / HALF_SIZE)**2, axis=2, keepdims=True))
    homogeneous = np.concatenate([source, np.ones((*source.shape[:2], 1))], axis=2)
    edges = np.cross(homogeneous[:, 0], homogeneous[:, 1])
    vanishing = np.cross(edges[::2], edges[1::2])
    vp = vanishing[:, :2] / vanishing[:, 2:3]
    focal = np.sqrt(-np.dot(vp[0], vp[1]))
    return np.rad2deg(2 * np.arctan(240 / focal))
