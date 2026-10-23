# Lightener curves in Adaptive Lighting

This fork embeds [Lightener](https://github.com/fredck/lightener) brightness mapping inside Adaptive Lighting so you keep AL’s colour-temperature adaptation, intercept logic, and device workarounds, while balancing a room with per-light response curves.

## Behaviour

1. Adaptive Lighting computes **room** brightness and colour from the sun (and sleep mode), as before.
2. Each managed light’s **brightness** is remapped through its curve: virtual/room % → real light %.
3. Colour temperature / RGB still come from Adaptive Lighting and are applied through the existing adaptation pipeline.
4. A **virtual room light** entity is created per config entry so you can also drive the room manually.
5. **Binary (on/off) lights** use a single threshold: off below it, fully on at/above it. In the editor they are vertical lines you drag sideways (not freeform curves).

Curve point format matches Lightener (`60: 0` means “at 60% room brightness this light is still off”). Points are stored in config entry options:

```yaml
brightness_curves:
  light.ceiling:
    "60": 0
    "100": 100
  light.sofa_lamp:
    "20": 0
    "60": 100
```

## UI

- Options flow: `min_brightness` / `max_brightness` moved to **Advanced** (room sun-curve bounds only).
- Sidebar panel **AL Curves** (`/adaptive-lighting-curves`): interactive X/Y plot of all lights, HA-style light list under the graph, point editing on the selected curve.

## Demo

```bash
python curve-demo/server.py --port 3847
```

Open http://127.0.0.1:3847/index.html for the editor with sample living-room curves (no Home Assistant required).

## Credit

Brightness interpolation is adapted from [fredck/lightener](https://github.com/fredck/lightener) (MIT). Upstream Adaptive Lighting remains Apache-2.0.
