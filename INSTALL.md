# Install on Home Assistant

Paths like `/opt/cursor/artifacts/...` exist only on the cloud agent — not on your PC or Home Assistant. Use one of the options below.

## Option A — HACS (best)

Needs a public GitHub repo. Add a `GITHUB_TOKEN` secret for `@sebvett` in this chat so the agent can publish `https://github.com/sebvett/adaptive-lighting`, then:

1. HACS → ⋮ → **Custom repositories**
2. URL: `https://github.com/sebvett/adaptive-lighting`
3. Type: **Integration** → Download → **restart Home Assistant**
4. Settings → Devices & Services → Add **Adaptive Lighting**

## Option B — Manual zip (works today)

1. Download **[adaptive_lighting.zip](http://127.0.0.1:3851/adaptive_lighting.zip)** (or take `releases/adaptive_lighting.zip` from this project in Cursor).
2. On your Home Assistant machine, ensure this folder exists:
   ```text
   /config/custom_components/
   ```
   (Create `custom_components` under your HA config directory if it is missing.)
3. Unzip so you get:
   ```text
   /config/custom_components/adaptive_lighting/manifest.json
   ```
   Example:
   ```bash
   cd /config/custom_components
   unzip adaptive_lighting.zip
   ```
4. **Restart Home Assistant**
5. Settings → Devices & Services → Add Integration → **Adaptive Lighting**

## Option C — Copy from this repo

If you can clone/pull this Origin project onto a machine that can reach HA:

```bash
cp -R custom_components/adaptive_lighting /config/custom_components/
```

Then restart Home Assistant.
