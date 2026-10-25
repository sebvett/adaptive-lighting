/**
 * Adaptive Lighting — Axis Playhead curve editor (Option A).
 *
 * Room brightness = playhead on the X axis + scrub strip (live, throttled).
 * Curves-UI groups share one series line (not HA light groups).
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
  const LIVE_THROTTLE_MS = 160;

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
    if (!curve) return 0;
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
    return preparePoints(points)
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

  function newGroupId() {
    return `cg_${Math.random().toString(36).slice(2, 12)}`;
  }

  const DEMO = {
    entry_id: "demo",
    title: "Living Room",
    lights: [
      "light.ceiling",
      "light.sofa_lamp",
      "light.closet_switch",
      "light.floor_lamp",
    ],
    names: {
      "light.ceiling": "Ceiling",
      "light.sofa_lamp": "Sofa lamp",
      "light.closet_switch": "Closet (on/off)",
      "light.floor_lamp": "Floor lamp",
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
      "light.closet_switch": {
        binary: true,
        threshold: 45,
        points: [{ virtual: 45, real: 100 }],
      },
      "light.floor_lamp": {
        points: [
          { virtual: 40, real: 30 },
          { virtual: 100, real: 100 },
        ],
      },
    },
    curve_groups: {
      cg_demo1: {
        name: "Lounge pair",
        members: ["light.sofa_lamp", "light.floor_lamp"],
      },
    },
    room: {
      entity_id: "light.living_room_room",
      state: "on",
      brightness: 140,
      brightness_pct: 55,
    },
    light_states: {
      "light.ceiling": {
        state: "off",
        brightness: 0,
        brightness_pct: 0,
        binary: false,
        is_ha_light_group: false,
      },
      "light.sofa_lamp": {
        state: "on",
        brightness: 180,
        brightness_pct: 71,
        binary: false,
        is_ha_light_group: false,
      },
      "light.closet_switch": {
        state: "on",
        brightness: null,
        brightness_pct: 100,
        binary: true,
        is_ha_light_group: false,
      },
      "light.floor_lamp": {
        state: "on",
        brightness: 90,
        brightness_pct: 35,
        binary: false,
        is_ha_light_group: false,
      },
    },
    available_lights: [{ entity_id: "light.hallway", name: "Hallway" }],
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
        curveGroups: {},
        selectedSeries: null, // light entity_id OR group id
        selectedPointVirtual: null,
        dirty: false,
        roomPct: 55,
        lightStates: {},
        availableLights: [],
        status: "",
        undo: null,
      };
      this.drag = null;
      this._liveTimer = null;
      this._livePending = null;
      this._pollTimer = null;
      this._toastTimer = null;
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
              <p>Scrub the room playhead under the plot — it drives room brightness live. Curves-UI groups share one line (separate from Home Assistant light groups).</p>
            </div>
            <div class="al-actions">
              <select class="al-select" data-el="instance" aria-label="Adaptive Lighting instance"></select>
              <button type="button" class="al-btn" data-el="reset">Reset series</button>
              <button type="button" class="al-btn primary" data-el="save" disabled>Save</button>
            </div>
          </header>
          <div class="al-plot-card">
            <div class="al-plot-meta">
              <span>Room → light response</span>
              <span>Room <strong data-el="room-pct">55%</strong> · drag scrub strip or playhead</span>
            </div>
            <svg id="al-plot" viewBox="0 0 ${VB.w} ${VB.h}" role="img" aria-label="Brightness curve plot"></svg>
            <div class="al-scrub-strip" data-el="scrub" role="slider" aria-valuemin="0" aria-valuemax="100" aria-label="Room brightness">
              <div class="al-scrub-fill" data-el="scrub-fill"></div>
              <div class="al-scrub-grab" data-el="scrub-grab"></div>
              <span class="al-scrub-label" data-el="scrub-label">55%</span>
            </div>
            <p class="al-hint">Select a point, then Delete/Backspace (or right-click → Delete). Endpoints at 0% / 100% stay locked. Undo appears after delete.</p>
          </div>
          <div class="al-lights">
            <div class="al-lights-title">
              <span>Lights &amp; curve groups</span>
            </div>
            <div class="al-add-row">
              <select class="al-select" data-el="add-select" aria-label="Add light"></select>
              <button type="button" class="al-btn" data-el="add-btn">Add light</button>
            </div>
            <div class="al-group-row">
              <input class="al-input" data-el="group-name" placeholder="Group name" aria-label="New curve group name" />
              <button type="button" class="al-btn" data-el="group-btn" title="Group the checked lights under one shared curve">Group checked</button>
            </div>
            <div data-el="list"></div>
          </div>
          <div class="al-status" data-el="status"></div>
          <div data-el="toast-host"></div>
          <div data-el="menu-host"></div>
        </div>
      `;
      this.els = {
        instance: this.root.querySelector('[data-el="instance"]'),
        reset: this.root.querySelector('[data-el="reset"]'),
        save: this.root.querySelector('[data-el="save"]'),
        list: this.root.querySelector('[data-el="list"]'),
        status: this.root.querySelector('[data-el="status"]'),
        roomPct: this.root.querySelector('[data-el="room-pct"]'),
        scrub: this.root.querySelector('[data-el="scrub"]'),
        scrubFill: this.root.querySelector('[data-el="scrub-fill"]'),
        scrubGrab: this.root.querySelector('[data-el="scrub-grab"]'),
        scrubLabel: this.root.querySelector('[data-el="scrub-label"]'),
        addSelect: this.root.querySelector('[data-el="add-select"]'),
        addBtn: this.root.querySelector('[data-el="add-btn"]'),
        groupName: this.root.querySelector('[data-el="group-name"]'),
        groupBtn: this.root.querySelector('[data-el="group-btn"]'),
        toastHost: this.root.querySelector('[data-el="toast-host"]'),
        menuHost: this.root.querySelector('[data-el="menu-host"]'),
        svg: this.root.querySelector("#al-plot"),
      };
    }

    _bind() {
      this.els.instance.addEventListener("change", () => {
        this.loadEntry(this.els.instance.value);
      });
      this.els.save.addEventListener("click", () => this.save());
      this.els.reset.addEventListener("click", () => this.resetSelected());
      this.els.addBtn.addEventListener("click", () => this.addSelectedLight());
      this.els.groupBtn.addEventListener("click", () => this.groupChecked());

      this.els.scrub.addEventListener("pointerdown", (e) => this.onScrubDown(e));
      this.els.svg.addEventListener("pointerdown", (e) => this.onPlotDown(e));
      this.els.svg.addEventListener("contextmenu", (e) => this.onContextMenu(e));
      window.addEventListener("pointermove", (e) => this.onPointerMove(e));
      window.addEventListener("pointerup", () => this.onPointerUp());
      window.addEventListener("keydown", (e) => this.onKeyDown(e));
      window.addEventListener("click", () => this.hideMenu());
    }

    async init() {
      if (this.demo) {
        this.state.instances = [{ entry_id: "demo", title: DEMO.title }];
        this.fillInstances();
        this.applyPayload(DEMO);
        this.setStatus("Demo mode — scrub strip drives the playhead; Group checked creates a UI curve group.", "");
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
        if (result.room && document.activeElement !== this.els.scrub) {
          const pct = Number(result.room.brightness_pct) || 0;
          this.state.roomPct = pct;
        }
        this.fillAddSelect();
        this.render();
      } catch (_) {
        /* ignore */
      }
    }

    applyPayload(payload) {
      this.state.entryId = payload.entry_id;
      this.state.title = payload.title;
      this.state.lights = payload.lights || [];
      this.state.names = payload.names || {};
      this.state.lightStates = payload.light_states || {};
      this.state.availableLights = payload.available_lights || [];
      this.state.curveGroups = payload.curve_groups || {};
      const roomPct = Number(payload.room?.brightness_pct);
      this.state.roomPct = Number.isFinite(roomPct) ? roomPct : 55;
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
      const series = this.seriesList();
      this.state.selectedSeries = series[0]?.id || null;
      this.state.selectedPointVirtual = null;
      this.state.dirty = false;
      this.els.save.disabled = true;
      this.fillAddSelect();
      this.render();
    }

    /** Series shown on the plot: one per ungrouped light + one per curve group. */
    seriesList() {
      const grouped = new Set();
      const series = [];
      for (const [gid, g] of Object.entries(this.state.curveGroups || {})) {
        const members = (g.members || []).filter((m) =>
          this.state.lights.includes(m)
        );
        if (members.length < 2) continue;
        members.forEach((m) => grouped.add(m));
        series.push({
          id: gid,
          kind: "group",
          name: g.name || "Group",
          members,
          primary: members[0],
        });
      }
      for (const id of this.state.lights) {
        if (grouped.has(id)) continue;
        series.push({
          id,
          kind: "light",
          name: this.state.names[id] || id,
          members: [id],
          primary: id,
        });
      }
      return series;
    }

    selectedSeriesObj() {
      return this.seriesList().find((s) => s.id === this.state.selectedSeries) || null;
    }

    selectedCurve() {
      const s = this.selectedSeriesObj();
      return s ? this.state.curves[s.primary] : null;
    }

    /** When editing a group curve, keep all members' curve objects in sync. */
    syncSelectedGroupCurves() {
      const s = this.selectedSeriesObj();
      if (!s || s.kind !== "group") return;
      const primaryCurve = this.state.curves[s.primary];
      if (!primaryCurve) return;
      for (const m of s.members) {
        if (m === s.primary) continue;
        this.state.curves[m] = primaryCurve.binary
          ? {
              binary: true,
              threshold: primaryCurve.threshold,
              points: [{ virtual: primaryCurve.threshold, real: 100 }],
            }
          : {
              binary: false,
              threshold: DEFAULT_BINARY_THRESHOLD,
              points: preparePoints(primaryCurve.points),
            };
      }
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
        .map((l) => {
          const tag = l.is_ha_light_group ? " · HA group" : "";
          return `<option value="${escapeAttr(l.entity_id)}">${escapeHtml(l.name)}${tag} (${escapeHtml(l.entity_id)})</option>`;
        })
        .join("");
      this.els.addBtn.disabled = false;
    }

    setStatus(text, kind) {
      this.state.status = text;
      this.els.status.textContent = text;
      this.els.status.className = `al-status ${kind || ""}`;
    }

    markDirty() {
      this.state.dirty = true;
      this.els.save.disabled = false;
      this.syncSelectedGroupCurves();
      this.setStatus("Unsaved curve changes", "");
    }

    setRoomPct(pct, { live = true } = {}) {
      this.state.roomPct = clamp(Math.round(pct), 0, 100);
      this.updateScrubChrome();
      this.renderPlot();
      this.renderList();
      if (live) this.queueLiveRoom(this.state.roomPct);
    }

    updateScrubChrome() {
      const pct = this.state.roomPct;
      this.els.roomPct.textContent = `${pct}%`;
      this.els.scrubLabel.textContent = `${pct}%`;
      this.els.scrubFill.style.width = `${pct}%`;
      this.els.scrubGrab.style.left = `${pct}%`;
      this.els.scrub.setAttribute("aria-valuenow", String(pct));
    }

    queueLiveRoom(pct) {
      this._livePending = pct;
      if (this._liveTimer) return;
      this._liveTimer = setTimeout(() => {
        this._liveTimer = null;
        const next = this._livePending;
        this._livePending = null;
        if (next != null) this.applyRoomBrightness(next, { silent: true });
      }, LIVE_THROTTLE_MS);
    }

    async applyRoomBrightness(pct, { silent = false } = {}) {
      if (this.demo) {
        if (!silent) this.setStatus(`Demo room set to ${pct}%.`, "ok");
        return;
      }
      try {
        await this.ws("adaptive_lighting/set_room_brightness", {
          entry_id: this.state.entryId,
          brightness_pct: Math.round(pct),
        });
        if (!silent) this.setStatus(`Room brightness set to ${pct}%.`, "ok");
        setTimeout(() => this.refreshLiveState(), 500);
      } catch (err) {
        this.setStatus(`Could not set room brightness: ${err}`, "error");
      }
    }

    resetSelected() {
      const curve = this.selectedCurve();
      if (!curve) return;
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
        this.state.availableLights = this.state.availableLights.filter(
          (l) => l.entity_id !== entityId
        );
        this.state.selectedSeries = entityId;
        this.fillAddSelect();
        this.markDirty();
        this.render();
        return;
      }
      try {
        const result = await this.ws("adaptive_lighting/update_lights", {
          entry_id: this.state.entryId,
          lights: [...this.state.lights, entityId],
        });
        this.applyPayload(result);
        this.setStatus(`Added ${entityId}.`, "ok");
      } catch (err) {
        this.setStatus(`Could not add light: ${err}`, "error");
      }
    }

    groupChecked() {
      const boxes = [
        ...this.els.list.querySelectorAll('input[data-group-check]:checked'),
      ];
      const members = boxes.map((b) => b.value).filter(Boolean);
      if (members.length < 2) {
        this.setStatus("Check at least two lights to form a Curves-UI group.", "error");
        return;
      }
      // Refuse mixing binary + dimmable.
      const bins = members.map((m) => Boolean(this.state.curves[m]?.binary));
      if (bins.some((b) => b) && bins.some((b) => !b)) {
        this.setStatus("Cannot mix on/off and dimmable lights in one curve group.", "error");
        return;
      }
      // Remove members from any existing groups.
      for (const [gid, g] of Object.entries(this.state.curveGroups)) {
        g.members = (g.members || []).filter((m) => !members.includes(m));
        if (g.members.length < 2) delete this.state.curveGroups[gid];
      }
      const id = newGroupId();
      const name =
        (this.els.groupName.value || "").trim() ||
        `Group (${members.length})`;
      this.state.curveGroups[id] = { name, members };
      // Sync curves to first member.
      const primary = members[0];
      for (const m of members.slice(1)) {
        const src = this.state.curves[primary];
        this.state.curves[m] = src.binary
          ? {
              binary: true,
              threshold: src.threshold,
              points: [{ virtual: src.threshold, real: 100 }],
            }
          : {
              binary: false,
              threshold: DEFAULT_BINARY_THRESHOLD,
              points: preparePoints(src.points),
            };
      }
      this.state.selectedSeries = id;
      this.els.groupName.value = "";
      this.markDirty();
      this.render();
      this.setStatus(`Created curve group “${name}”. Save to persist.`, "ok");
    }

    ungroup(groupId) {
      if (!this.state.curveGroups[groupId]) return;
      const name = this.state.curveGroups[groupId].name;
      delete this.state.curveGroups[groupId];
      const series = this.seriesList();
      this.state.selectedSeries = series[0]?.id || null;
      this.markDirty();
      this.render();
      this.setStatus(`Ungrouped “${name}”. Members keep their curves.`, "ok");
    }

    async save() {
      this.syncSelectedGroupCurves();
      if (this.demo) {
        this.state.dirty = false;
        this.els.save.disabled = true;
        this.setStatus("Saved in demo memory.", "ok");
        return;
      }
      const curves = {};
      for (const id of this.state.lights) {
        const curve = this.state.curves[id];
        if (!curve) continue;
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
          curve_groups: this.state.curveGroups,
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
      this.updateScrubChrome();
      this.renderPlot();
      this.renderList();
    }

    curvePctAtPlayhead(primaryId) {
      return Math.round(sampleCurve(this.state.curves[primaryId], this.state.roomPct));
    }

    renderList() {
      const series = this.seriesList();
      if (!series.length) {
        this.els.list.innerHTML =
          '<div class="al-empty">Add lights below, then optionally group them for a shared curve.</div>';
        return;
      }
      const groupedMembers = new Set();
      Object.values(this.state.curveGroups || {}).forEach((g) =>
        (g.members || []).forEach((m) => groupedMembers.add(m))
      );

      let html = "";
      series.forEach((s, idx) => {
        const color = COLORS[idx % COLORS.length];
        const selected = s.id === this.state.selectedSeries ? "selected" : "";
        const pct = this.curvePctAtPlayhead(s.primary);
        const curve = this.state.curves[s.primary];
        const badge = curve?.binary
          ? `<span class="al-badge">On/off · ${curve.threshold}%</span>`
          : s.kind === "group"
            ? `<span class="al-badge">Curve group · ${s.members.length}</span>`
            : "";
        if (s.kind === "group") {
          html += `
            <div class="al-series" data-series="${escapeAttr(s.id)}">
              <div class="al-group-header ${selected}" data-series="${escapeAttr(s.id)}" role="button" tabindex="0">
                <span class="al-swatch" style="background:${color}"></span>
                <span>
                  <span class="al-light-name">${escapeHtml(s.name)} ${badge}</span>
                  <span class="al-light-id">${s.members.map(escapeHtml).join(", ")}</span>
                </span>
                <span class="al-light-value" style="display:flex;gap:0.5rem;align-items:center">
                  ${curve?.binary ? (pct >= 100 ? "On" : "Off") : `${pct}%`}
                  <button type="button" class="al-btn" data-ungroup="${escapeAttr(s.id)}" style="padding:0.2rem 0.5rem;font-size:0.75rem">Ungroup</button>
                </span>
              </div>
              ${s.members
                .map((m) => {
                  const st = this.state.lightStates[m];
                  const live =
                    st?.binary
                      ? st.state === "on"
                        ? "On"
                        : "Off"
                      : st?.state === "on" && st.brightness_pct != null
                        ? `${st.brightness_pct}%`
                        : "Off";
                  return `
                  <div class="al-light-row member" data-id="${escapeAttr(m)}">
                    <span class="al-swatch" style="background:${color};opacity:.5"></span>
                    <span>
                      <span class="al-light-name">${escapeHtml(this.state.names[m] || m)}</span>
                      <span class="al-light-id">${escapeHtml(m)}</span>
                    </span>
                    <span class="al-light-value">${escapeHtml(live)}</span>
                  </div>`;
                })
                .join("")}
            </div>`;
        } else {
          const id = s.primary;
          const st = this.state.lightStates[id];
          const haBadge = st?.is_ha_light_group
            ? `<span class="al-badge">HA light group</span>`
            : "";
          const checkable = !groupedMembers.has(id)
            ? `<input type="checkbox" data-group-check value="${escapeAttr(id)}" aria-label="Include in new curve group" onclick="event.stopPropagation()" />`
            : "";
          html += `
            <div class="al-series">
              <div class="al-light-row ${selected}" data-series="${escapeAttr(id)}" role="button" tabindex="0">
                <span class="al-swatch" style="background:${color}"></span>
                <span>
                  <span class="al-light-name">${checkable} ${escapeHtml(s.name)} ${badge}${haBadge}</span>
                  <span class="al-light-id">${escapeHtml(id)}</span>
                </span>
                <span class="al-light-value">${curve?.binary ? (pct >= 100 ? "On" : "Off") : `${pct}%`}</span>
              </div>
            </div>`;
        }
      });
      this.els.list.innerHTML = html;

      this.els.list.querySelectorAll("[data-series]").forEach((el) => {
        const activate = () => {
          this.state.selectedSeries = el.dataset.series;
          this.state.selectedPointVirtual = null;
          this.hideMenu();
          this.render();
        };
        el.addEventListener("click", (e) => {
          if (e.target.closest("[data-ungroup], input")) return;
          activate();
        });
        el.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            activate();
          }
        });
      });
      this.els.list.querySelectorAll("[data-ungroup]").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          this.ungroup(btn.dataset.ungroup);
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
        `<text class="al-axis-label" x="${VB.w / 2}" y="${VB.h - 2}" text-anchor="middle">Virtual / room brightness %</text>`
      );
      grid.push(
        `<text class="al-axis-label" x="14" y="${VB.h / 2}" text-anchor="middle" transform="rotate(-90 14 ${VB.h / 2})">Real light brightness %</text>`
      );

      const series = this.seriesList();
      const curves = series
        .map((s, idx) => {
          const active = s.id === this.state.selectedSeries ? "active" : "";
          const color = COLORS[idx % COLORS.length];
          const curve = this.state.curves[s.primary];
          if (!curve) return "";
          if (curve.binary) {
            const x = toSvgX(curve.threshold);
            return `<line class="al-binary-line ${active}" data-series="${escapeAttr(s.id)}" data-binary="1" stroke="${color}" x1="${x}" y1="${PAD.t}" x2="${x}" y2="${VB.h - PAD.b}" />`;
          }
          return `<path class="al-curve ${active}" data-series="${escapeAttr(s.id)}" stroke="${color}" d="${polyline(curve.points)}" />`;
        })
        .join("");

      const roomPct = this.state.roomPct;
      const playX = toSvgX(roomPct);
      let readouts = "";
      series.forEach((s, idx) => {
        const y = sampleCurve(this.state.curves[s.primary], roomPct);
        const color = COLORS[idx % COLORS.length];
        const label = s.kind === "group" ? s.name : s.name;
        const short = escapeHtml(String(label).slice(0, 12));
        const val = this.state.curves[s.primary]?.binary
          ? y >= 100
            ? "on"
            : "off"
          : `${Math.round(y)}%`;
        readouts += `<circle cx="${playX}" cy="${toSvgY(y)}" r="3.5" fill="${color}" />`;
        readouts += `<text class="al-playhead-readout" x="${playX + 6}" y="${toSvgY(y) + 3}" fill="${color}">${short} ${val}</text>`;
      });
      const playhead = `<line class="al-playhead" x1="${playX}" y1="${PAD.t}" x2="${playX}" y2="${VB.h - PAD.b}" />`;

      let points = "";
      const selected = this.selectedCurve();
      const selSeries = this.selectedSeriesObj();
      if (selected && selSeries) {
        const color =
          COLORS[
            series.findIndex((s) => s.id === selSeries.id) % COLORS.length
          ];
        if (selected.binary) {
          const x = toSvgX(selected.threshold);
          const yMid = (PAD.t + (VB.h - PAD.b)) / 2;
          points = `<circle class="al-point al-binary-handle" data-index="threshold" cx="${x}" cy="${yMid}" r="7" fill="${color}" />`;
        } else {
          const editable = selected.points.filter(
            (p) => p.virtual > 0 && p.virtual < 100
          );
          points = editable
            .map((p, i) => {
              const sel =
                this.state.selectedPointVirtual === p.virtual ? "1" : "0";
              return `<circle class="al-point" data-index="${i}" data-virtual="${p.virtual}" data-selected="${sel}" cx="${toSvgX(p.virtual)}" cy="${toSvgY(p.real)}" r="${sel === "1" ? 7 : 5.5}" fill="${color}" />`;
            })
            .join("");
          points += selected.points
            .filter((p) => p.virtual === 100)
            .map(
              (p) =>
                `<circle class="al-point" data-index="end100" cx="${toSvgX(100)}" cy="${toSvgY(p.real)}" r="5.5" fill="${color}" />`
            )
            .join("");
        }
      }

      svg.innerHTML = `${grid.join("")}${curves}${playhead}${readouts}${points}`;
      svg.querySelectorAll(".al-curve, .al-binary-line").forEach((el) => {
        el.addEventListener("pointerdown", (e) => {
          e.stopPropagation();
          this.state.selectedSeries = el.dataset.series;
          this.state.selectedPointVirtual = null;
          if (el.dataset.binary) {
            this.drag = { kind: "threshold", moved: false };
            e.preventDefault();
          } else {
            this.maybeAddPoint(e);
          }
          this.render();
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
      return pt.matrixTransform(ctm.inverse());
    }

    scrubPctFromEvent(evt) {
      const rect = this.els.scrub.getBoundingClientRect();
      if (!rect.width) return this.state.roomPct;
      return clamp(((evt.clientX - rect.left) / rect.width) * 100, 0, 100);
    }

    onScrubDown(evt) {
      this.hideMenu();
      this.drag = { kind: "scrub" };
      this.els.scrub.setPointerCapture?.(evt.pointerId);
      this.setRoomPct(this.scrubPctFromEvent(evt), { live: true });
      evt.preventDefault();
    }

    onPlotDown(evt) {
      this.hideMenu();
      const target = evt.target;
      if (target.classList.contains("al-point")) {
        const idx = target.dataset.index;
        this.drag = {
          kind: target.classList.contains("al-binary-handle")
            ? "threshold"
            : "point",
          index: idx,
          moved: false,
        };
        if (target.dataset.virtual != null) {
          this.state.selectedPointVirtual = Number(target.dataset.virtual);
        } else {
          this.state.selectedPointVirtual = null;
        }
        target.classList.add("dragging");
        evt.preventDefault();
        this.renderPlot();
        return;
      }
      // Click empty plot: move playhead (live).
      const { x } = this.svgPoint(evt);
      this.drag = { kind: "scrub-plot" };
      this.setRoomPct(fromSvgX(x), { live: true });
      const selected = this.selectedCurve();
      if (selected && !selected.binary) this.maybeAddPoint(evt);
      evt.preventDefault();
    }

    maybeAddPoint(evt) {
      const selected = this.selectedCurve();
      if (!selected || selected.binary) return;
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
      if (Math.abs(toSvgY(onCurve) - y) > 14 && Math.abs(onCurve - real) > 8) {
        return;
      }
      points.push({ virtual, real });
      selected.points = preparePoints(points);
      this.state.selectedPointVirtual = virtual;
      this.markDirty();
    }

    onPointerMove(evt) {
      if (!this.drag) return;
      if (this.drag.kind === "scrub" || this.drag.kind === "scrub-plot") {
        const pct =
          this.drag.kind === "scrub"
            ? this.scrubPctFromEvent(evt)
            : fromSvgX(this.svgPoint(evt).x);
        this.setRoomPct(pct, { live: true });
        return;
      }
      const curve = this.selectedCurve();
      if (!curve) return;
      const { x, y } = this.svgPoint(evt);
      if (this.drag.kind === "threshold" || curve.binary) {
        curve.threshold = clamp(Math.round(fromSvgX(x)), 1, 100);
        curve.points = [{ virtual: curve.threshold, real: 100 }];
        this.drag.moved = true;
        this.markDirty();
        this.render();
        return;
      }
      let virtual = Math.round(fromSvgX(x));
      const real = Math.round(fromSvgY(y));
      const points = [...curve.points];
      if (this.drag.index === "end100") {
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
      if (this.drag?.kind === "scrub" || this.drag?.kind === "scrub-plot") {
        // Flush pending live apply immediately on release.
        if (this._liveTimer) {
          clearTimeout(this._liveTimer);
          this._liveTimer = null;
        }
        if (this._livePending != null) {
          const pct = this._livePending;
          this._livePending = null;
          this.applyRoomBrightness(pct, { silent: true });
        }
      }
      this.drag = null;
      this.els.svg
        .querySelectorAll(".al-point.dragging")
        .forEach((n) => n.classList.remove("dragging"));
    }

    onKeyDown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (this.state.dirty) this.save();
        return;
      }
      const tag = (e.target && e.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        this.deleteSelectedPoint();
      }
      const curve = this.selectedCurve();
      if (
        curve?.binary &&
        (e.key === "ArrowLeft" || e.key === "ArrowRight")
      ) {
        e.preventDefault();
        const delta = e.key === "ArrowLeft" ? -1 : 1;
        curve.threshold = clamp(curve.threshold + delta, 1, 100);
        curve.points = [{ virtual: curve.threshold, real: 100 }];
        this.markDirty();
        this.render();
      }
    }

    onContextMenu(evt) {
      const target = evt.target;
      if (!target.classList?.contains("al-point") || target.dataset.virtual == null) {
        return;
      }
      evt.preventDefault();
      this.state.selectedPointVirtual = Number(target.dataset.virtual);
      this.renderPlot();
      this.showMenu(evt.clientX, evt.clientY, [
        {
          label: "Delete point",
          danger: true,
          action: () => this.deleteSelectedPoint(),
        },
      ]);
    }

    showMenu(x, y, items) {
      this.hideMenu();
      const menu = document.createElement("div");
      menu.className = "al-menu";
      menu.style.left = `${x}px`;
      menu.style.top = `${y}px`;
      items.forEach((item) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = item.label;
        if (item.danger) btn.classList.add("danger");
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          this.hideMenu();
          item.action();
        });
        menu.appendChild(btn);
      });
      this.els.menuHost.appendChild(menu);
    }

    hideMenu() {
      this.els.menuHost.innerHTML = "";
    }

    deleteSelectedPoint() {
      const curve = this.selectedCurve();
      if (!curve || curve.binary) return;
      const v = this.state.selectedPointVirtual;
      if (v == null || v <= 0 || v >= 100) {
        this.setStatus("Select an editable point first (not 0% / 100%).", "");
        return;
      }
      const before = preparePoints(curve.points);
      const removed = before.find((p) => p.virtual === v);
      if (!removed) return;
      curve.points = preparePoints(before.filter((p) => p.virtual !== v));
      this.state.selectedPointVirtual = null;
      this.state.undo = {
        seriesId: this.state.selectedSeries,
        points: before,
        message: `Deleted point at ${v}%`,
      };
      this.markDirty();
      this.render();
      this.showUndoToast(this.state.undo.message);
    }

    showUndoToast(message) {
      if (this._toastTimer) clearTimeout(this._toastTimer);
      this.els.toastHost.innerHTML = `
        <div class="al-toast" role="status">
          <span>${escapeHtml(message)}</span>
          <button type="button" data-el="undo">Undo</button>
        </div>`;
      this.els.toastHost.querySelector("[data-el='undo']").addEventListener("click", () => {
        this.undoDelete();
      });
      this._toastTimer = setTimeout(() => {
        this.els.toastHost.innerHTML = "";
        this.state.undo = null;
      }, 6000);
    }

    undoDelete() {
      if (!this.state.undo) return;
      const { seriesId, points } = this.state.undo;
      this.state.selectedSeries = seriesId;
      const curve = this.selectedCurve();
      if (curve && !curve.binary) {
        curve.points = preparePoints(points);
        this.markDirty();
        this.render();
        this.setStatus("Delete undone.", "ok");
      }
      this.state.undo = null;
      this.els.toastHost.innerHTML = "";
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
