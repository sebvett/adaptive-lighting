/**
 * Adaptive Lighting — Lightener-style brightness curve editor.
 *
 * X axis: virtual / room brightness (0–100%)
 * Y axis: real light brightness (0–100%)
 *
 * Binary (on/off) lights are vertical threshold lines that drag horizontally only.
 */
(() => {
  const COLORS = [
    "#047857",
    "#1d4ed8",
    "#b45309",
    "#be123c",
    "#6d28d9",
    "#0f766e",
    "#a16207",
    "#334155",
  ];

  const PAD = { l: 44, r: 16, t: 20, b: 36 };
  const VB = { w: 720, h: 420 };
  const DEFAULT_BINARY_THRESHOLD = 50;

  function clamp(n, lo, hi) {
    return Math.max(lo, Math.min(hi, n));
  }

  function scaleRanged(src, dst, value) {
    const [a, b] = src;
    const [c, d] = dst;
    if (a === b) return c;
    return c + ((value - a) * (d - c)) / (b - a);
  }

  function preparePoints(points) {
    const map = new Map([[0, 0]]);
    for (const p of points || []) {
      const v = clamp(Math.round(Number(p.virtual)), 0, 100);
      const r = clamp(Math.round(Number(p.real)), 0, 100);
      if (v === 0) continue;
      map.set(v, r);
    }
    if (!map.has(100)) {
      let last = 100;
      for (const [v, r] of map) {
        if (v > 0) last = r;
      }
      map.set(100, last);
    }
    return [...map.entries()]
      .map(([virtual, real]) => ({ virtual, real }))
      .sort((a, b) => a.virtual - b.virtual);
  }

  function sampleCurve(curve, x) {
    if (curve.binary) {
      return x >= curve.threshold ? 100 : 0;
    }
    const prepared = preparePoints(curve.points);
    const clamped = clamp(x, 0, 100);
    for (let i = 1; i < prepared.length; i++) {
      const a = prepared[i - 1];
      const b = prepared[i];
      if (clamped <= b.virtual) {
        return scaleRanged([a.virtual, b.virtual], [a.real, b.real], clamped);
      }
    }
    return prepared[prepared.length - 1].real;
  }

  function toSvgX(pct) {
    return PAD.l + (pct / 100) * (VB.w - PAD.l - PAD.r);
  }

  function toSvgY(pct) {
    return PAD.t + ((100 - pct) / 100) * (VB.h - PAD.t - PAD.b);
  }

  function fromSvgX(x) {
    return clamp(((x - PAD.l) / (VB.w - PAD.l - PAD.r)) * 100, 0, 100);
  }

  function fromSvgY(y) {
    return clamp((1 - (y - PAD.t) / (VB.h - PAD.t - PAD.b)) * 100, 0, 100);
  }

  function polyline(points) {
    const prepared = preparePoints(points);
    return prepared
      .map(
        (p, i) =>
          `${i ? "L" : "M"}${toSvgX(p.virtual).toFixed(1)},${toSvgY(p.real).toFixed(1)}`
      )
      .join(" ");
  }

  function thresholdFromPoints(points) {
    const prepared = preparePoints(points);
    const on = prepared.find((p) => p.real > 0 && p.virtual > 0);
    if (on) return clamp(on.virtual, 1, 100);
    return DEFAULT_BINARY_THRESHOLD;
  }

  const DEMO = {
    entry_id: "demo",
    title: "Living Room",
    lights: [
      "light.ceiling",
      "light.sofa_lamp",
      "light.led_strip",
      "light.floor_lamp",
      "light.closet_switch",
    ],
    names: {
      "light.ceiling": "Ceiling",
      "light.sofa_lamp": "Sofa lamp",
      "light.led_strip": "LED strip",
      "light.floor_lamp": "Floor lamp",
      "light.closet_switch": "Closet (on/off)",
    },
    binary_lights: ["light.closet_switch"],
    curves: {
      "light.ceiling": {
        points: [
          { virtual: 60, real: 0 },
          { virtual: 100, real: 100 },
        ],
      },
      "light.sofa_lamp": {
        points: [
          { virtual: 20, real: 0 },
          { virtual: 60, real: 100 },
          { virtual: 100, real: 40 },
        ],
      },
      "light.led_strip": {
        points: [{ virtual: 80, real: 100 }],
      },
      "light.floor_lamp": {
        points: [
          { virtual: 40, real: 30 },
          { virtual: 100, real: 100 },
        ],
      },
      "light.closet_switch": {
        binary: true,
        threshold: 45,
        points: [{ virtual: 45, real: 100 }],
      },
    },
    room: { entity_id: "light.living_room_room", state: "on", brightness: 140, brightness_pct: 55 },
    light_states: {
      "light.ceiling": { state: "off", brightness: 0, brightness_pct: 0, binary: false },
      "light.sofa_lamp": { state: "on", brightness: 180, brightness_pct: 71, binary: false },
      "light.led_strip": { state: "off", brightness: 0, brightness_pct: 0, binary: false },
      "light.floor_lamp": { state: "on", brightness: 90, brightness_pct: 35, binary: false },
      "light.closet_switch": { state: "on", brightness: null, brightness_pct: 100, binary: true },
    },
    available_lights: [
      { entity_id: "light.hallway", name: "Hallway" },
      { entity_id: "light.desk", name: "Desk lamp" },
    ],
  };

  class CurveEditor {
    constructor(root) {
      this.root = root;
      this.demo = root.dataset.mode === "demo" || !window.hassConnection;
      this.state = {
        instances: [],
        entryId: null,
        title: "",
        lights: [],
        names: {},
        curves: {},
        selected: null,
        dirty: false,
        scrub: 55,
        roomPct: 55,
        roomEntity: null,
        lightStates: {},
        availableLights: [],
        status: "",
        statusKind: "",
        selectedPointVirtual: null,
      };
      this.drag = null;
      this._roomSliderTimer = null;
      this._pollTimer = null;
      this._build();
      this._bind();
      this.init();
    }

    _build() {
      this.root.innerHTML = `
        <div class="al-shell">
          <header class="al-header">
            <div>
              <h1>Brightness curves</h1>
              <p>Virtual room brightness on the X axis, each real light on the Y axis — Lightener mapping inside Adaptive Lighting. On/off lights are vertical thresholds you drag sideways.</p>
            </div>
            <div class="al-actions">
              <select class="al-select" data-el="instance" aria-label="Adaptive Lighting instance"></select>
              <button type="button" class="al-btn" data-el="reset" title="Reset selected light">Reset light</button>
              <button type="button" class="al-btn danger" data-el="delete-point" title="Remove selected curve point" disabled>Remove point</button>
              <button type="button" class="al-btn primary" data-el="save" disabled>Save</button>
            </div>
          </header>
          <div class="al-plot-card">
            <div class="al-plot-meta">
              <span>Room → light response</span>
              <div class="al-room-control">
                <label for="al-room-slider">Room brightness</label>
                <input id="al-room-slider" type="range" min="0" max="100" step="1" data-el="room-slider" />
                <span class="al-room-pct" data-el="room-pct">55%</span>
              </div>
            </div>
            <svg id="al-plot" viewBox="0 0 ${VB.w} ${VB.h}" role="img" aria-label="Brightness curve plot"></svg>
            <p class="al-hint">Coloured bar shows current room %. Click a point then Remove point (or Delete / double-click) to delete it. Drag the scrub line to preview curve outputs.</p>
          </div>
          <div class="al-lights">
            <div class="al-lights-title">
              <span>Lights in this room</span>
            </div>
            <div class="al-add-row" data-el="add-row">
              <select class="al-select" data-el="add-select" aria-label="Add light"></select>
              <button type="button" class="al-btn" data-el="add-btn">Add light</button>
            </div>
            <div data-el="list"></div>
          </div>
          <div class="al-status" data-el="status"></div>
        </div>
      `;
      this.els = {
        instance: this.root.querySelector('[data-el="instance"]'),
        reset: this.root.querySelector('[data-el="reset"]'),
        deletePoint: this.root.querySelector('[data-el="delete-point"]'),
        save: this.root.querySelector('[data-el="save"]'),
        list: this.root.querySelector('[data-el="list"]'),
        status: this.root.querySelector('[data-el="status"]'),
        roomSlider: this.root.querySelector('[data-el="room-slider"]'),
        roomPct: this.root.querySelector('[data-el="room-pct"]'),
        addSelect: this.root.querySelector('[data-el="add-select"]'),
        addBtn: this.root.querySelector('[data-el="add-btn"]'),
        svg: this.root.querySelector("#al-plot"),
      };
    }

    _bind() {
      this.els.instance.addEventListener("change", () => {
        this.loadEntry(this.els.instance.value);
      });
      this.els.save.addEventListener("click", () => this.save());
      this.els.reset.addEventListener("click", () => this.resetSelected());
      this.els.deletePoint.addEventListener("click", () => this.deleteSelectedPoint());
      this.els.addBtn.addEventListener("click", () => this.addSelectedLight());
      this.els.roomSlider.addEventListener("input", () => {
        const pct = Number(this.els.roomSlider.value);
        this.state.roomPct = pct;
        this.state.scrub = pct;
        this.els.roomPct.textContent = `${Math.round(pct)}%`;
        this.renderPlot();
        this.renderList();
      });
      this.els.roomSlider.addEventListener("change", () => {
        this.applyRoomBrightness(Number(this.els.roomSlider.value));
      });
      this.els.svg.addEventListener("pointerdown", (e) => this.onPointerDown(e));
      this.els.svg.addEventListener("dblclick", (e) => this.onDoubleClick(e));
      window.addEventListener("pointermove", (e) => this.onPointerMove(e));
      window.addEventListener("pointerup", () => this.onPointerUp());
      window.addEventListener("keydown", (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
          e.preventDefault();
          if (this.state.dirty) this.save();
        }
        if (e.key === "Delete" || e.key === "Backspace") {
          const tag = (e.target && e.target.tagName) || "";
          if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
          e.preventDefault();
          this.deleteSelectedPoint();
        }
        if (
          this.state.selected &&
          this.state.curves[this.state.selected]?.binary &&
          (e.key === "ArrowLeft" || e.key === "ArrowRight")
        ) {
          e.preventDefault();
          const delta = e.key === "ArrowLeft" ? -1 : 1;
          const curve = this.state.curves[this.state.selected];
          curve.threshold = clamp(curve.threshold + delta, 1, 100);
          this.markDirty();
          this.render();
        }
      });
    }

    async init() {
      if (this.demo) {
        this.state.instances = [{ entry_id: "demo", title: DEMO.title }];
        this.fillInstances();
        this.applyPayload(DEMO);
        this.setStatus(
          "Demo mode — drag the closet’s vertical line sideways; dimmable lights keep freeform curves.",
          ""
        );
        return;
      }
      try {
        const result = await this.ws("adaptive_lighting/list_instances");
        this.state.instances = result.instances || [];
        this.fillInstances();
        if (this.state.instances.length) {
          await this.loadEntry(this.state.instances[0].entry_id);
        } else {
          this.setStatus("No Adaptive Lighting instances found.", "error");
        }
        this._pollTimer = setInterval(() => this.refreshLiveState(), 2500);
      } catch (err) {
        this.setStatus(`Could not load instances: ${err}`, "error");
      }
    }

    fillInstances() {
      this.els.instance.innerHTML = this.state.instances
        .map(
          (i) =>
            `<option value="${i.entry_id}">${escapeHtml(i.title || i.entry_id)}</option>`
        )
        .join("");
    }

    async loadEntry(entryId) {
      if (this.demo) {
        this.applyPayload(DEMO);
        return;
      }
      const result = await this.ws("adaptive_lighting/get_curves", {
        entry_id: entryId,
      });
      this.applyPayload(result);
    }

    async refreshLiveState() {
      if (this.demo || !this.state.entryId || this.drag) return;
      try {
        const result = await this.ws("adaptive_lighting/get_curves", {
          entry_id: this.state.entryId,
        });
        this.state.lightStates = result.light_states || {};
        this.state.availableLights = result.available_lights || [];
        if (result.room) {
          const pct = Number(result.room.brightness_pct) || 0;
          // Don't fight the user while they are dragging the slider.
          if (document.activeElement !== this.els.roomSlider) {
            this.state.roomPct = pct;
            this.state.scrub = pct;
            this.els.roomSlider.value = String(pct);
            this.els.roomPct.textContent = `${Math.round(pct)}%`;
          }
        }
        this.fillAddSelect();
        this.renderPlot();
        this.renderList();
      } catch (_) {
        /* transient */
      }
    }

    applyPayload(payload) {
      this.state.entryId = payload.entry_id;
      this.state.title = payload.title;
      this.state.lights = payload.lights || [];
      this.state.names = payload.names || {};
      this.state.lightStates = payload.light_states || {};
      this.state.availableLights = payload.available_lights || [];
      this.state.roomEntity = payload.room?.entity_id || null;
      const roomPct = Number(payload.room?.brightness_pct);
      this.state.roomPct = Number.isFinite(roomPct) ? roomPct : 55;
      this.state.scrub = this.state.roomPct;
      this.els.roomSlider.value = String(this.state.roomPct);
      this.els.roomPct.textContent = `${Math.round(this.state.roomPct)}%`;
      const binarySet = new Set(payload.binary_lights || []);
      this.state.curves = {};
      for (const id of this.state.lights) {
        const raw = (payload.curves && payload.curves[id]) || {};
        const binary = Boolean(raw.binary) || binarySet.has(id);
        if (binary) {
          const threshold = clamp(
            Number(raw.threshold ?? thresholdFromPoints(raw.points || [])),
            1,
            100
          );
          this.state.curves[id] = {
            binary: true,
            threshold,
            points: [{ virtual: threshold, real: 100 }],
          };
        } else {
          this.state.curves[id] = {
            binary: false,
            threshold: DEFAULT_BINARY_THRESHOLD,
            points: preparePoints(
              raw.points || raw || [{ virtual: 100, real: 100 }]
            ),
          };
        }
      }
      this.state.selected = this.state.lights[0] || null;
      this.state.selectedPointVirtual = null;
      this.state.dirty = false;
      this.els.save.disabled = true;
      this.els.deletePoint.disabled = true;
      this.fillAddSelect();
      this.render();
    }

    fillAddSelect() {
      const options = this.state.availableLights || [];
      if (!options.length) {
        this.els.addSelect.innerHTML =
          '<option value="">No other lights available</option>';
        this.els.addBtn.disabled = true;
        return;
      }
      this.els.addSelect.innerHTML = options
        .map(
          (l) =>
            `<option value="${escapeAttr(l.entity_id)}">${escapeHtml(l.name)} (${escapeHtml(l.entity_id)})</option>`
        )
        .join("");
      this.els.addBtn.disabled = false;
    }

    setStatus(text, kind) {
      this.state.status = text;
      this.state.statusKind = kind || "";
      this.els.status.textContent = text;
      this.els.status.className = `al-status ${kind || ""}`;
    }

    markDirty() {
      this.state.dirty = true;
      this.els.save.disabled = false;
      this.setStatus("Unsaved curve changes", "");
    }

    selectedCurve() {
      return this.state.selected ? this.state.curves[this.state.selected] : null;
    }

    updateDeleteButton() {
      const curve = this.selectedCurve();
      const can =
        curve &&
        !curve.binary &&
        this.state.selectedPointVirtual != null &&
        this.state.selectedPointVirtual > 0 &&
        this.state.selectedPointVirtual < 100;
      this.els.deletePoint.disabled = !can;
    }

    resetSelected() {
      if (!this.state.selected) return;
      const curve = this.state.curves[this.state.selected];
      if (curve.binary) {
        curve.threshold = DEFAULT_BINARY_THRESHOLD;
        curve.points = [{ virtual: DEFAULT_BINARY_THRESHOLD, real: 100 }];
      } else {
        curve.points = preparePoints([{ virtual: 100, real: 100 }]);
      }
      this.state.selectedPointVirtual = null;
      this.markDirty();
      this.render();
    }

    async applyRoomBrightness(pct) {
      if (this.demo) {
        this.state.roomPct = pct;
        this.state.scrub = pct;
        this.setStatus(`Demo room set to ${Math.round(pct)}%.`, "ok");
        this.render();
        return;
      }
      try {
        await this.ws("adaptive_lighting/set_room_brightness", {
          entry_id: this.state.entryId,
          brightness_pct: Math.round(pct),
        });
        this.state.roomPct = pct;
        this.state.scrub = pct;
        this.setStatus(`Room brightness set to ${Math.round(pct)}%.`, "ok");
        // Give HA a moment, then refresh live light levels.
        setTimeout(() => this.refreshLiveState(), 600);
      } catch (err) {
        this.setStatus(`Could not set room brightness: ${err}`, "error");
      }
    }

    async addSelectedLight() {
      const entityId = this.els.addSelect.value;
      if (!entityId) return;
      if (this.demo) {
        const avail = this.state.availableLights.find((l) => l.entity_id === entityId);
        this.state.lights.push(entityId);
        this.state.names[entityId] = avail?.name || entityId;
        this.state.curves[entityId] = {
          binary: false,
          threshold: DEFAULT_BINARY_THRESHOLD,
          points: preparePoints([{ virtual: 100, real: 100 }]),
        };
        this.state.lightStates[entityId] = {
          state: "off",
          brightness: 0,
          brightness_pct: 0,
          binary: false,
        };
        this.state.availableLights = this.state.availableLights.filter(
          (l) => l.entity_id !== entityId
        );
        this.state.selected = entityId;
        this.fillAddSelect();
        this.markDirty();
        this.render();
        return;
      }
      try {
        const lights = [...this.state.lights, entityId];
        const result = await this.ws("adaptive_lighting/update_lights", {
          entry_id: this.state.entryId,
          lights,
        });
        this.applyPayload(result);
        this.setStatus(`Added ${entityId}.`, "ok");
      } catch (err) {
        this.setStatus(`Could not add light: ${err}`, "error");
      }
    }

    async save() {
      if (this.demo) {
        this.state.dirty = false;
        this.els.save.disabled = true;
        this.setStatus("Saved in demo memory.", "ok");
        return;
      }
      const curves = {};
      for (const id of this.state.lights) {
        const curve = this.state.curves[id];
        if (curve.binary) {
          curves[id] = {
            binary: true,
            threshold: curve.threshold,
            points: [{ virtual: curve.threshold, real: 100 }],
          };
        } else {
          curves[id] = {
            binary: false,
            points: curve.points.filter((p) => p.virtual > 0),
          };
        }
      }
      try {
        await this.ws("adaptive_lighting/save_curves", {
          entry_id: this.state.entryId,
          curves,
        });
        this.state.dirty = false;
        this.els.save.disabled = true;
        this.setStatus("Curves saved.", "ok");
      } catch (err) {
        this.setStatus(`Save failed: ${err}`, "error");
      }
    }

    ws(type, payload = {}) {
      return new Promise((resolve, reject) => {
        if (!window.hassConnection || !window.hassConnection.conn) {
          reject(new Error("No Home Assistant connection"));
          return;
        }
        window.hassConnection.conn
          .sendMessagePromise({ type, ...payload })
          .then(resolve)
          .catch(reject);
      });
    }

    render() {
      this.renderPlot();
      this.renderList();
      this.updateDeleteButton();
      this.els.roomPct.textContent = `${Math.round(this.state.roomPct)}%`;
    }

    liveBrightnessLabel(id) {
      const st = this.state.lightStates[id];
      if (!st) return "—";
      if (st.state === "unavailable") return "Unavailable";
      if (st.binary) {
        return st.state === "on" ? "On" : "Off";
      }
      if (st.state !== "on" || st.brightness_pct == null) return "Off";
      return `${st.brightness_pct}%`;
    }

    renderList() {
      if (!this.state.lights.length) {
        this.els.list.innerHTML =
          '<div class="al-empty">Add lights below (or in Adaptive Lighting options), then edit their curves.</div>';
        return;
      }
      this.els.list.innerHTML = this.state.lights
        .map((id, idx) => {
          const color = COLORS[idx % COLORS.length];
          const name = this.state.names[id] || id;
          const curve = this.state.curves[id];
          const preview = Math.round(sampleCurve(curve, this.state.scrub));
          const live = this.liveBrightnessLabel(id);
          const selected = id === this.state.selected ? "selected" : "";
          const previewLabel = curve.binary
            ? preview >= 100
              ? "On"
              : "Off"
            : `${preview}%`;
          const badge = curve.binary
            ? `<span class="al-badge">On/off · ${curve.threshold}%</span>`
            : "";
          return `
            <div class="al-light-row ${selected}" data-id="${escapeAttr(id)}" role="button" tabindex="0">
              <span class="al-swatch" style="background:${color}"></span>
              <span>
                <span class="al-light-name">${escapeHtml(name)} ${badge}</span>
                <span class="al-light-id">${escapeHtml(id)}</span>
              </span>
              <span class="al-light-value"><strong>${escapeHtml(live)}</strong>at room ${Math.round(this.state.scrub)}% → ${previewLabel}</span>
            </div>`;
        })
        .join("");
      this.els.list.querySelectorAll(".al-light-row").forEach((row) => {
        const activate = () => {
          this.state.selected = row.dataset.id;
          this.state.selectedPointVirtual = null;
          this.render();
        };
        row.addEventListener("click", activate);
        row.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            activate();
          }
        });
      });
    }

    renderPlot() {
      const svg = this.els.svg;
      const grid = [];
      for (let i = 0; i <= 100; i += 25) {
        grid.push(
          `<line class="al-grid-line" x1="${toSvgX(i)}" y1="${PAD.t}" x2="${toSvgX(i)}" y2="${VB.h - PAD.b}" />`
        );
        grid.push(
          `<line class="al-grid-line" x1="${PAD.l}" y1="${toSvgY(i)}" x2="${VB.w - PAD.r}" y2="${toSvgY(i)}" />`
        );
        grid.push(
          `<text class="al-axis-label" x="${toSvgX(i)}" y="${VB.h - 12}" text-anchor="middle">${i}</text>`
        );
        grid.push(
          `<text class="al-axis-label" x="${PAD.l - 8}" y="${toSvgY(i) + 4}" text-anchor="end">${i}</text>`
        );
      }
      grid.push(
        `<text class="al-axis-label" x="${VB.w / 2}" y="${VB.h - 2}" text-anchor="middle">Virtual light brightness %</text>`
      );
      grid.push(
        `<text class="al-axis-label" x="14" y="${VB.h / 2}" text-anchor="middle" transform="rotate(-90 14 ${VB.h / 2})">Real light brightness %</text>`
      );

      // Current room brightness — filled bar along the X axis.
      const roomPct = clamp(this.state.roomPct, 0, 100);
      const barY = VB.h - PAD.b;
      const barH = 10;
      const roomBar = `
        <rect class="al-room-bar" x="${PAD.l}" y="${barY - barH}" width="${Math.max(0, toSvgX(roomPct) - PAD.l)}" height="${barH}" rx="2" />
        <line class="al-room-bar-edge" x1="${toSvgX(roomPct)}" y1="${PAD.t}" x2="${toSvgX(roomPct)}" y2="${VB.h - PAD.b}" />
      `;

      const curves = this.state.lights
        .map((id, idx) => {
          const active = id === this.state.selected ? "active" : "";
          const color = COLORS[idx % COLORS.length];
          const curve = this.state.curves[id];
          if (curve.binary) {
            const x = toSvgX(curve.threshold);
            return `<line class="al-binary-line ${active}" data-id="${escapeAttr(id)}" data-binary="1" stroke="${color}" x1="${x}" y1="${PAD.t}" x2="${x}" y2="${VB.h - PAD.b}" />`;
          }
          return `<path class="al-curve ${active}" data-id="${escapeAttr(id)}" stroke="${color}" d="${polyline(curve.points)}" />`;
        })
        .join("");

      const scrubX = toSvgX(this.state.scrub);
      const scrub = `<line x1="${scrubX}" y1="${PAD.t}" x2="${scrubX}" y2="${VB.h - PAD.b}" stroke="var(--al-muted)" stroke-dasharray="4 4" stroke-width="1.5" opacity="0.7" />`;

      let points = "";
      const selected = this.selectedCurve();
      if (selected && this.state.selected) {
        const color =
          COLORS[this.state.lights.indexOf(this.state.selected) % COLORS.length];
        if (selected.binary) {
          const x = toSvgX(selected.threshold);
          const yMid = (PAD.t + (VB.h - PAD.b)) / 2;
          points = `<circle class="al-point al-binary-handle" data-index="threshold" cx="${x}" cy="${yMid}" r="7" fill="${color}" />`;
          points += `<text class="al-axis-label" x="${x}" y="${PAD.t - 2}" text-anchor="middle">${selected.threshold}%</text>`;
        } else {
          const editable = selected.points.filter(
            (p) => p.virtual > 0 && p.virtual < 100
          );
          points = editable
            .map((p, i) => {
              const selectedPt =
                this.state.selectedPointVirtual === p.virtual ? "1" : "0";
              let html = `<circle class="al-point" data-index="${i}" data-virtual="${p.virtual}" data-selected="${selectedPt}" cx="${toSvgX(p.virtual)}" cy="${toSvgY(p.real)}" r="${selectedPt === "1" ? 7 : 5.5}" fill="${color}" />`;
              if (selectedPt === "1") {
                const cx = toSvgX(p.virtual) + 10;
                const cy = toSvgY(p.real) - 10;
                html += `
                  <circle class="al-point-delete" data-virtual="${p.virtual}" cx="${cx}" cy="${cy}" r="7" />
                  <path class="al-point-delete-x" d="M${cx - 3},${cy - 3} L${cx + 3},${cy + 3} M${cx + 3},${cy - 3} L${cx - 3},${cy + 3}" />
                `;
              }
              return html;
            })
            .join("");
          const ends = selected.points.filter((p) => p.virtual === 100);
          points += ends
            .map(
              (p) =>
                `<circle class="al-point" data-index="end100" cx="${toSvgX(100)}" cy="${toSvgY(p.real)}" r="5.5" fill="${color}" />`
            )
            .join("");
        }
      }

      svg.innerHTML = `${grid.join("")}${roomBar}${curves}${scrub}${points}`;
      svg.querySelectorAll(".al-curve, .al-binary-line").forEach((el) => {
        el.addEventListener("pointerdown", (e) => {
          e.stopPropagation();
          this.state.selected = el.dataset.id;
          this.state.selectedPointVirtual = null;
          if (el.dataset.binary) {
            this.drag = { index: "threshold", moved: false, binary: true };
            e.preventDefault();
          } else {
            this.maybeAddPoint(e);
          }
          this.render();
        });
      });
      svg.querySelectorAll(".al-point-delete").forEach((el) => {
        el.addEventListener("pointerdown", (e) => {
          e.stopPropagation();
          e.preventDefault();
          this.state.selectedPointVirtual = Number(el.dataset.virtual);
          this.deleteSelectedPoint();
        });
      });
    }

    svgPoint(evt) {
      const svg = this.els.svg;
      const pt = svg.createSVGPoint();
      pt.x = evt.clientX;
      pt.y = evt.clientY;
      const ctm = svg.getScreenCTM();
      if (!ctm) return { x: 0, y: 0 };
      const local = pt.matrixTransform(ctm.inverse());
      return { x: local.x, y: local.y };
    }

    onPointerDown(evt) {
      const target = evt.target;
      if (target.classList.contains("al-point-delete")) {
        return;
      }
      if (target.classList.contains("al-point")) {
        const idx = target.dataset.index;
        const binary = target.classList.contains("al-binary-handle");
        this.drag = { index: idx, moved: false, binary };
        if (target.dataset.virtual != null) {
          this.state.selectedPointVirtual = Number(target.dataset.virtual);
        } else if (idx === "end100") {
          this.state.selectedPointVirtual = null;
        }
        target.classList.add("dragging");
        this.updateDeleteButton();
        evt.preventDefault();
        return;
      }
      const { x } = this.svgPoint(evt);
      this.state.scrub = fromSvgX(x);
      const selected = this.selectedCurve();
      if (selected && !selected.binary) {
        this.maybeAddPoint(evt);
      }
      this.render();
    }

    onDoubleClick(evt) {
      const target = evt.target;
      if (target.classList.contains("al-point") && target.dataset.virtual != null) {
        this.state.selectedPointVirtual = Number(target.dataset.virtual);
        this.deleteSelectedPoint();
        evt.preventDefault();
      }
    }

    maybeAddPoint(evt) {
      const selected = this.selectedCurve();
      if (!selected || selected.binary || !this.state.selected) return;
      const { x, y } = this.svgPoint(evt);
      const virtual = Math.round(fromSvgX(x));
      const real = Math.round(fromSvgY(y));
      if (virtual <= 0 || virtual >= 100) return;
      const points = selected.points.filter((p) => p.virtual !== 0);
      const near = points.find((p) => Math.abs(p.virtual - virtual) <= 2);
      if (near) {
        this.state.selectedPointVirtual = near.virtual;
        return;
      }
      const onCurve = sampleCurve(selected, virtual);
      const dist = Math.abs(toSvgY(onCurve) - y);
      if (dist > 14 && Math.abs(onCurve - real) > 8) return;
      points.push({ virtual, real });
      selected.points = preparePoints(points);
      this.state.selectedPointVirtual = virtual;
      this.markDirty();
    }

    onPointerMove(evt) {
      if (!this.drag || !this.state.selected) {
        if (evt.buttons === 1 && evt.target === this.els.svg) {
          const { x } = this.svgPoint(evt);
          this.state.scrub = fromSvgX(x);
          this.renderList();
          this.renderPlot();
        }
        return;
      }

      const curve = this.state.curves[this.state.selected];
      const { x, y } = this.svgPoint(evt);

      if (this.drag.binary || curve.binary) {
        curve.threshold = clamp(Math.round(fromSvgX(x)), 1, 100);
        curve.points = [{ virtual: curve.threshold, real: 100 }];
        this.drag.moved = true;
        this.markDirty();
        this.render();
        return;
      }

      let virtual = Math.round(fromSvgX(x));
      let real = Math.round(fromSvgY(y));
      const points = [...curve.points];
      if (this.drag.index === "end100") {
        virtual = 100;
        const end = points.find((p) => p.virtual === 100);
        if (end) end.real = real;
        else points.push({ virtual: 100, real });
        this.state.selectedPointVirtual = null;
      } else {
        const editable = points.filter((p) => p.virtual > 0 && p.virtual < 100);
        const point = editable[Number(this.drag.index)];
        if (!point) return;
        virtual = clamp(virtual, 1, 99);
        while (
          editable.some(
            (p, i) => i !== Number(this.drag.index) && p.virtual === virtual
          )
        ) {
          virtual = clamp(virtual + 1, 1, 99);
        }
        point.virtual = virtual;
        point.real = real;
        this.state.selectedPointVirtual = virtual;
      }
      curve.points = preparePoints(points);
      this.drag.moved = true;
      this.markDirty();
      this.render();
    }

    onPointerUp() {
      this.drag = null;
      this.els.svg
        .querySelectorAll(".al-point.dragging")
        .forEach((n) => n.classList.remove("dragging"));
    }

    deleteSelectedPoint() {
      const curve = this.selectedCurve();
      if (!curve || curve.binary || !this.state.selected) return;
      let removeVirtual = this.state.selectedPointVirtual;
      if (removeVirtual == null || removeVirtual <= 0 || removeVirtual >= 100) {
        // Fall back to point nearest the scrub line (legacy Delete behaviour).
        const points = curve.points.filter(
          (p) => p.virtual > 0 && p.virtual < 100
        );
        if (!points.length) return;
        let best = 0;
        let bestDist = Infinity;
        points.forEach((p, i) => {
          const d = Math.abs(p.virtual - this.state.scrub);
          if (d < bestDist) {
            bestDist = d;
            best = i;
          }
        });
        if (bestDist > 8) return;
        removeVirtual = points[best].virtual;
      }
      curve.points = preparePoints(
        curve.points.filter((p) => p.virtual !== removeVirtual)
      );
      this.state.selectedPointVirtual = null;
      this.markDirty();
      this.render();
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replaceAll("'", "&#39;");
  }

  function boot() {
    const mount = document.querySelector("#al-curve-editor");
    if (mount) new CurveEditor(mount);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
