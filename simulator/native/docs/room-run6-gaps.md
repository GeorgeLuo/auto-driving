# Room run 6: looks the current renderer cannot match

These are the places where the run 6 frames need a feature the software renderer does not have. The map approximates each one with the existing parts, materials, and lights.

## Wide-angle lens (frames 0, 9, 52)

The real camera is a low, very wide lens. The simulator's barrel term is a radial resample with strength at most 0.5, then a vignette. It does not reproduce the fisheye stretch at the corners or the way the carpet bows across the bottom of frame 0. A lens model with a larger field and a stronger radial curve would.

## See-through chair mesh (frames 0, 12, 29)

The office chair back is a set of opaque slats. The real back is a mesh you can see the window through. An alpha or cutout material on a textured card would do that.

## Window bloom (frame 0)

Daylight is a directional light plus ambient, and the panes are a flat white material. The walls around the windows go bright, but the frame does not bloom into the wall the way frame 0 does. An emissive material, or a bloom pass after lighting, would.

## Leather highlight (frames 31, 42)

`leather` adds a grazing sheen. The ottoman in those frames has a hot, blooming streak along the top edge. A specular lobe, or the same bloom pass, would.

## One image per part (frame 58)

A box has one material, so a printed face is a second thin part rather than a per-face texture. The number, curve, and label sit on that front card. Per-face materials would let the flaps carry the number without a second part.

## Grazing carpet (frames 0, 9, 58)

The carpet shader evaluates procedural mottling and cell noise once per primary ray. Close views expose the cell pattern, and distant oblique views can undersample it. Filtering the procedural pattern over each pixel's footprint would reduce aliasing.

## Hinges (frames 52, 58)

A part rotates around its local base center. A static door or flap can meet its hinge by offsetting the part after rotation. Changing the angle requires recomputing that offset because the asset format has no hinge pivot.
