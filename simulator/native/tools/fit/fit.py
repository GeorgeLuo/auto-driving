"""Fit run 6 key poses and render real / native / 50% blend evidence."""
import itertools
import json
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from estimate import Registration, estimate, replay
from geometry import BEFORE, NATIVE, Observations, Scene, document, vanishing_fov

FIT = Path(__file__).resolve().parent
OUTPUT = NATIVE / 'build/room-run6'


def render(config, pose, scratch):
    settings = dict(config, spawn=dict(zip(('x', 'z', 'headingRadians'), pose, strict=True)))
    (scratch / 'map.json').write_text(json.dumps(settings))
    target = scratch / 'camera.jpg'
    subprocess.run([NATIVE / 'build/automa-native-sim', '--config', scratch / 'map.json',
                    '--render', target], check=True)
    with Image.open(target) as image:
        return image.convert('RGB')


def edge_errors(image, annotated, projected):
    """Distance from each click to a rendered edge near its corresponding 3D feature."""
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 1.4), 24, 65)
    errors = []
    for click, point in zip(annotated, projected, strict=True):
        x, y = np.rint(point).astype(int)
        left, top = max(x - 15, 0), max(y - 15, 0)
        right, bottom = min(x + 16, image.width), min(y + 16, image.height)
        if right <= left or bottom <= top:
            errors.append(np.linalg.norm(click - point) + 15)
            continue
        ys, xs = np.nonzero(edges[top:bottom, left:right])
        distances = np.hypot(xs + left - click[0], ys + top - click[1])
        errors.append(distances.min(initial=np.linalg.norm(click - point) + 15))
    return np.array(errors)


def candidates(reg, index, pose):
    # Centimetre translations plus a small yaw bracket. Project contact poses
    # back onto the padded face so a better photograph cannot override physics.
    offsets = [[0, 0, 0]]
    for axis, sign in itertools.product(range(3), (-1, 1)):
        offset = np.zeros(3)
        offset[axis] = sign * (.025 if axis == 2 else .012)
        offsets.append(offset)
    seen = set()
    for offset in offsets:
        candidate = reg.feasible(index, pose + offset)
        if candidate is not None:
            key = tuple(np.round(candidate, 9))
            if key not in seen:
                seen.add(key)
                yield candidate


def choose(reg, index, poses, prior, *, weights, scratch):
    mask = reg.obs.frame == index
    clicks = reg.obs.pixels[mask]
    best = None
    for pose in candidates(reg, index, poses[index]):
        trial = poses.copy()
        trial[index] = pose
        uv = reg.projection(trial)[0][mask]
        image = render(reg.scene.map, pose, scratch)
        reprojection = np.linalg.norm(uv - clicks, axis=1)
        edge = edge_errors(image, clicks, uv)
        motion = np.linalg.norm((pose - prior[index]) * weights[index])
        score = np.mean(.65 * reprojection + .35 * edge) + .10 * motion + reg.outside_errors(trial)[index]
        if best is None or score < best[0]:
            best = score, pose, image
    if best is None:
        raise ValueError(f'No collision-free render at frame {reg.obs.ids[index]}')
    return best[1:]


def triptych(real_path, native, path, index):
    with Image.open(real_path) as source:
        real = source.convert('RGB')
    if real.size != native.size:
        raise ValueError(f'Camera dimensions differ for {real_path}')
    result = Image.new('RGB', (real.width * 3, real.height + 24), '#202020')
    draw = ImageDraw.Draw(result)
    panels = [('Real', real), ('Native', native), ('50/50', Image.blend(real, native, .5))]
    for i, (name, panel) in enumerate(panels):
        result.paste(panel, (i * real.width, 24))
        draw.text((i * real.width + 8, 5), f'{index:02}    {name}', fill='white')
    result.save(path, quality=92)


def mean_pixels(values):
    return f'{values.mean():.1f}' if values.size else '—'


def check_contacts(reg, poses):
    indices = list(reg.contacts)
    starts = poses[indices]
    steps = [[dict(throttle=1, steering=0, steps=1)] for _ in starts]
    stopped = replay(steps, starts, 1)
    distance = np.linalg.norm(stopped[:, :2] - starts[:, :2], axis=1)
    if distance.max() > 1e-9:
        raise ValueError(f'Native forward step advanced at contact: {distance}')
    return ', '.join(f'{reg.scene.names[reg.contacts[i][0]]} at frame {reg.obs.ids[i]}' for i in indices)


def layout_comparison(reg, before, poses, paired):
    old_points = before.points(before.placements)
    uv, _, front = reg.obs.project(reg.scene.map['camera'], poses, old_points)
    comparable = paired & front
    old = np.linalg.norm(uv - reg.obs.pixels, axis=1)
    new = np.linalg.norm(reg.projection(poses)[0] - reg.obs.pixels, axis=1)
    return (f'With the fitted camera applied to both layouts, the same-pose residual goes from '
            f'{old[comparable].mean():.1f} to {new[comparable].mean():.1f} px '
            f'over {sum(comparable)} marks.')


def table_rows(reg, poses, paired, errors, raster):
    old_errors, new_errors = errors
    old_raster, new_raster = raster
    rows = []
    for index, (key, pose) in enumerate(zip(reg.obs.ids, poses, strict=True)):
        subset = reg.obs.frame == index
        valid = subset & paired
        before_mean = mean_pixels(old_errors[valid])
        after_mean = mean_pixels(new_errors[valid])
        x, z, yaw = pose
        rows.append(f'| {key:02} | {x:.7f} | {z:.7f} | {yaw:.7f} | {before_mean} | '
                    f'{after_mean} | {sum(valid)}/{sum(subset)} | '
                    f'{new_errors[subset].mean():.1f} | {sum(old_raster[subset])} / '
                    f'{sum(new_raster[subset])} | [image](fit-{key:02}.jpg) |')
    return rows


def summarize(reg, before, poses, gain, fields):
    after = reg.projection(poses)
    original = reg.obs.project(before.map['camera'], poses, before.points(before.placements))
    paired = original[2] & after[2]
    old_errors = np.linalg.norm(original[0] - reg.obs.pixels, axis=1)
    new_errors = np.linalg.norm(after[0] - reg.obs.pixels, axis=1)
    rows = table_rows(reg, poses, paired, (old_errors, new_errors), (original[1], after[1]))
    paired &= ~np.isin(reg.obs.frame, reg.obs.support)
    text = [
        '# Run 6 camera registration', '',
        f'The two layouts use identical vehicle poses. Before uses map and assets at `{BEFORE}`. '
        'Distance is the mean Euclidean pixel residual of the labelled points and edges at 640 × 480. '
        'Off-screen projections retain their pixel coordinates. The paired means require the '
        'feature to be in front of both near planes. All-after includes every annotated mark. '
        'The raster counts record how many projections fall on the source sensor before edge '
        'clamping. Occlusion is assessed from the triptychs.', '',
        f'Across {sum(paired)} paired marks in the requested frames the mean goes from {old_errors[paired].mean():.1f} to '
        f'{new_errors[paired].mean():.1f} px. Frames 27 and 33 supply settled contact observations.',
        layout_comparison(reg, before, poses, paired), '',
        fields, '',
        f'One full forward native step at each padded face moved less than 1 nm ({check_contacts(reg, poses)}).',
        '', f'Native replay used {gain:g} × the configured speed during the camera intervals. '
        'Coordinates are metres and yaw is atan2(z, x) in radians.', '',
        'The fitted frame 0 spawn is', '',
        '```json', json.dumps(dict(zip(('x', 'z', 'headingRadians'), poses[0], strict=True))), '```', '',
        '| Frame | x | z | yaw | Before px | After px | Paired/marked | All-after px | In raster before / after | Evidence |',
        '|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|', *rows, '',
        'The bed quilt and the soft backpack straps have approximate silhouette correspondences. '
        'The rigid car and fixed camera cannot reproduce pitch changes on carpet or compression '
        'against the bag. The outer image strips repeat the source border. The padded door trim, '
        'rug ripples, loose cable bends and curved carton flap remain approximate. '
        'The baseboard angle near the open door remains shallow. The fixed mount also leaves '
        'a vertical offset at the far baseboard corner in the box-contact frame. '
        'These are calibration residuals on the fitted marks. Absolute room coordinates have '
        'no independent survey.', '']
    (OUTPUT / 'fit-summary.md').write_text('\n'.join(text))
    print('\n'.join(rows))


def main():
    marks = document(FIT / 'run6-marks.json')
    reg = Registration(Scene(marks), Observations(marks))
    frames = sorted((NATIVE / 'build/run6/frames').glob('*.jpg'))
    poses, prior, weights, gain = estimate(reg, frames)
    OUTPUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=OUTPUT) as folder:
        for index, key in enumerate(reg.obs.ids):
            poses[index], image = choose(reg, index, poses, prior, weights=weights, scratch=Path(folder))
            triptych(frames[key], image, OUTPUT / f'fit-{key:02}.jpg', key)
    fields = [f'{vanishing_fov(lines, reg.scene.map["camera"]["barrelDistortion"]):.1f}° at frame {key}'
              for key, lines in marks['vanishing_lines'].items()]
    summarize(reg, Scene(marks, BEFORE), poses, gain, 'Bed-line vertical FOV estimates were ' + ' and '.join(fields) + '.')


if __name__ == '__main__':
    main()
