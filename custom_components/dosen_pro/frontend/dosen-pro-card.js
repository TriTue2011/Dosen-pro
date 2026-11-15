/* Dosen Pro — thẻ cài đặt màn hình camera (một thẻ cho TOÀN BỘ cài đặt hiển thị).
 * Tự có sẵn khi cài tích hợp (không phải thêm tài nguyên): có trong danh sách thẻ khi thêm vào bảng điều khiển và có mục «Màn hình Dosen» ở thanh bên.
 * JavaScript thuần, không dùng thành phần nào của giao diện HA nên chạy ở mọi bản. Mọi thay đổi gọi API WebSocket `dosen_pro/*`. */
(() => {
  const TYPES = { clock: "Đồng hồ", card: "Thẻ thông tin", camera: "Camera trực tiếp", image: "Ảnh" };
  const MODES = { off: "Tắt", clock: "Đồng hồ", image: "Ảnh", video: "Video lặp", live: "Camera trực tiếp" };
  const QUALITIES = { standard: "Chuẩn", high: "Cao", max: "Cao nhất (thử)" };
  const FITS = { contain: "Vừa khung (có viền)", cover: "Lấp đầy (cắt rìa)" };
  const THEMES = { light: "Sáng", dark: "Tối" };
  const LAYOUTS = { list: "Số lớn + danh sách", tiles: "Lưới 2 cột" };
  const NAMED = { red: "#d61616", orange: "#e16e00", green: "#008232", blue: "#005ac8", black: "#000000" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const opts = (map, cur) => Object.entries(map).map(([k, v]) => `<option value="${esc(k)}"${String(cur) === k ? " selected" : ""}>${esc(v)}</option>`).join("");
  const hexOf = (c) => (c && c.startsWith("#") ? c : NAMED[c] || "#000000");

  const CSS = `
    :host { display:block; }
    * { box-sizing:border-box; }
    .wrap { padding:12px 16px 16px; color:var(--primary-text-color,#222); font-family:var(--paper-font-body1_-_font-family,Roboto,sans-serif); }
    h2 { margin:0 0 4px; font-size:20px; font-weight:500; }
    h3 { margin:18px 0 8px; font-size:15px; font-weight:600; border-bottom:1px solid var(--divider-color,#ddd); padding-bottom:4px; }
    .sub { color:var(--secondary-text-color,#666); font-size:13px; }
    .err { background:#fdecea; color:#b3261e; padding:8px 12px; border-radius:8px; margin:8px 0; font-size:13px; display:none; }
    .err.on { display:block; }
    .grid { display:grid; grid-template-columns:minmax(260px,300px) 1fr; gap:20px; align-items:start; }
    @media (max-width:760px) { .grid { grid-template-columns:1fr; } }
    .lcd { background:#222; border-radius:14px; padding:12px; width:fit-content; margin:0 auto; }
    .lcd img { display:block; width:240px; height:320px; background:#000; image-rendering:auto; }
    .lcd .empty { width:240px; height:320px; color:#aaa; display:flex; align-items:center; justify-content:center; text-align:center; padding:16px; font-size:13px; }
    .row { display:flex; gap:8px; align-items:center; margin:6px 0; flex-wrap:wrap; }
    .row > label { min-width:130px; font-size:13px; color:var(--secondary-text-color,#555); }
    input[type=text], input[type=number], select { padding:7px 9px; border:1px solid var(--divider-color,#bbb); border-radius:8px; background:var(--card-background-color,#fff); color:inherit; font:inherit; min-width:0; }
    input[type=text] { flex:1; min-width:140px; }
    input[type=number] { width:90px; }
    button { padding:7px 12px; border:1px solid var(--divider-color,#bbb); border-radius:8px; background:var(--card-background-color,#fff); color:inherit; font:inherit; cursor:pointer; }
    button.primary { background:var(--primary-color,#03a9f4); color:var(--text-primary-color,#fff); border-color:transparent; }
    button.small { padding:3px 8px; font-size:12px; }
    button:disabled { opacity:.45; cursor:default; }
    .list { border:1px solid var(--divider-color,#ddd); border-radius:10px; overflow:hidden; }
    .item { display:flex; gap:8px; align-items:center; padding:8px 10px; border-bottom:1px solid var(--divider-color,#eee); }
    .item:last-child { border-bottom:0; }
    .item.sel { background:var(--secondary-background-color,#f1f5f9); }
    .item .nm { flex:1; cursor:pointer; min-width:0; }
    .item .nm b { font-weight:500; }
    .item .nm span { display:block; font-size:12px; color:var(--secondary-text-color,#777); }
    .sw { display:inline-block; width:22px; height:22px; border-radius:50%; border:2px solid transparent; cursor:pointer; padding:0; }
    .sw.on { border-color:var(--primary-text-color,#000); }
    .sw.auto { background:repeating-linear-gradient(45deg,#ddd,#ddd 4px,#fff 4px,#fff 8px); }
    .colors { display:flex; gap:5px; align-items:center; }
    input[type=color] { width:30px; height:26px; padding:0; border:1px solid var(--divider-color,#bbb); border-radius:6px; background:none; }
    .rowitem { display:flex; gap:6px; align-items:center; flex-wrap:wrap; padding:6px 0; border-bottom:1px dashed var(--divider-color,#ddd); }
    .editor { border:1px solid var(--primary-color,#03a9f4); border-radius:10px; padding:10px 12px; margin-top:10px; }
    .hint { font-size:12px; color:var(--secondary-text-color,#777); }
    .ok { color:#1b7f3b; font-size:12px; }
  `;

  class DosenProCard extends HTMLElement {
    setConfig(config) {
      this._config = config || {};
      this._entry = this._config.entry_id;
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
      this._state = this._state || null;
      this._draft = this._draft || null;
      this._previewSeq = 0;
      this._build();
    }
    getCardSize() { return 10; }
    static getStubConfig() { return {}; }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      if (first) { this._buildLists(); this._load(); }
    }
    get hass() { return this._hass; }

    // ── API ──────────────────────────────────────────────────────────
    async _ws(type, extra = {}) {
      const msg = { type, ...(this._entry ? { entry_id: this._entry } : {}), ...extra };
      return this._hass.callWS(msg);
    }
    async _call(type, extra) {
      try { this._error(""); return await this._ws(type, extra); }
      catch (e) { this._error(e && (e.message || e.code) ? e.message || e.code : String(e)); return null; }
    }
    async _load() {
      const st = await this._call("dosen_pro/state");
      if (st) { this._state = st; this._entry = this._entry || st.entry_id; this._renderAll(); this._loadScreenInfo(); }
    }
    async _loadScreenInfo() {
      const info = await this._call("dosen_pro/screen_info");
      if (info) { this._screen = info; this._renderScreen(); }
    }
    _applyState(st) { if (st) { this._state = st; this._renderAll(); } }

    // ── khung ────────────────────────────────────────────────────────
    _build() {
      this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="wrap">
        <h2>Màn hình Dosen</h2><div class="sub" id="sub">Đang tải…</div><div class="err" id="err"></div>
        <div class="grid"><div id="left"></div><div id="right"></div></div>
        <datalist id="dl-all"></datalist><datalist id="dl-camera"></datalist><datalist id="dl-image"></datalist>
        <datalist id="dl-weather"></datalist><datalist id="dl-sensor"></datalist></div>`;
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
      this.shadowRoot.addEventListener("change", (e) => this._onChange(e));
      this.shadowRoot.addEventListener("input", (e) => this._onInput(e));
      if (this._hass) this._buildLists();
      if (this._state) this._renderAll();
    }
    _$(id) { return this.shadowRoot.getElementById(id); }
    _error(msg) { const el = this._$("err"); if (el) { el.textContent = msg; el.classList.toggle("on", !!msg); } }

    _buildLists() {
      if (!this.shadowRoot || !this._hass) return;
      const groups = { all: [], camera: [], image: [], weather: [], sensor: [] };
      for (const [id, st] of Object.entries(this._hass.states || {})) {
        const dom = id.split(".")[0];
        const label = `${st.attributes?.friendly_name || id}`;
        const opt = `<option value="${esc(id)}">${esc(label)}</option>`;
        groups.all.push(opt);
        if (groups[dom]) groups[dom].push(opt);
        if (dom === "binary_sensor") groups.sensor.push(opt);
      }
      for (const [g, list] of Object.entries(groups)) { const el = this._$("dl-" + g); if (el) el.innerHTML = list.join(""); }
    }

    // ── vẽ ───────────────────────────────────────────────────────────
    _renderAll() {
      if (!this._state || !this.shadowRoot || !this._$("sub")) return;
      const st = this._state, s = st.settings;
      this._$("sub").textContent = `${st.title || "Camera"} — màn mặc định: ${MODES[s.default_mode || "off"]}; luân phiên ${st.carousel.on ? "đang chạy" : "tắt"}`;
      this._$("left").innerHTML = `<div class="lcd" id="lcd">${this._previewHtml()}</div>
        <div class="row" style="justify-content:center;margin-top:10px">
          <button id="btn-prev" ${this._draft || this._sel ? "" : "disabled"}>Làm mới xem trước</button>
          <button id="btn-show" class="primary" ${this._draft || this._sel ? "" : "disabled"}>Gửi thử lên màn (30 giây)</button></div>
        <div class="hint" style="text-align:center">Hình xem trước vẽ bằng số liệu thật của HA, đúng như màn camera.</div>`;
      this._$("right").innerHTML = this._defaultHtml() + this._rotationHtml() + `<div id="editor">${this._editorHtml()}</div>` + this._displayHtml();
      this._renderScreen();
      if (!this._draft && this._sel) this._preview(this._sel);
    }
    _previewHtml() {
      if (this._png) return `<img id="pimg" alt="xem trước" src="data:image/png;base64,${this._png}">`;
      return `<div class="empty" id="pimg">${esc(this._note || "Chọn một thẻ trong danh sách hoặc bấm «Thêm thẻ» để xem trước.")}</div>`;
    }

    _defaultHtml() {
      const s = this._state.settings;
      const field = (label, key, dl, ph) => `<div class="row"><label>${label}</label><input type="text" data-set="${key}" list="${dl}" value="${esc(s[key])}" placeholder="${esc(ph)}"></div>`;
      return `<h3>Màn hình mặc định</h3>
        <div class="row"><label>Loại</label><select data-set="default_mode">${opts(MODES, s.default_mode || "off")}</select></div>
        ${s.default_mode === "live" ? field("Camera", "default_camera", "dl-camera", "camera.cua") : ""}
        ${s.default_mode === "image" ? field("Ảnh", "default_image", "dl-image", "image.thoi_tiet") : ""}
        ${["image", "video", "live"].includes(s.default_mode) ? field("Hoặc gõ địa chỉ", "default_text", "dl-none", "http://… hoặc rtsp://…") : ""}
        ${s.default_mode === "clock" ? field("Thời tiết trên đồng hồ", "default_weather", "dl-weather", "weather.nha (để trống = không hiện)") : ""}
        <div class="row"><label>Xoay hình</label><select data-set="default_rotate" data-num="1">${[0, 90, 180, 270].map((d) => `<option value="${d}"${s.default_rotate === d ? " selected" : ""}>${d}°</option>`).join("")}</select>
          <label style="min-width:0">Khung hình</label><select data-set="default_fit">${opts(FITS, s.default_fit)}</select></div>
        <div class="row"><label>Chất lượng hình</label><select data-set="default_quality">${opts(QUALITIES, s.default_quality)}</select>
          <span class="hint">Màn đen/chập chờn thì đặt «Chuẩn».</span></div>`;
    }

    _rotationHtml() {
      const c = this._state.carousel;
      const items = c.cards.map((card, i) => `<div class="item${this._sel && this._sel.id === card.id ? " sel" : ""}" data-id="${esc(card.id)}">
          <input type="checkbox" data-join="${esc(card.id)}" ${c.selected.includes(card.id) ? "checked" : ""} title="Tham gia luân phiên">
          <div class="nm" data-pick="${esc(card.id)}"><b>${esc(card.name)}</b><span>${esc(TYPES[card.type] || card.type)}</span></div>
          <button class="small" data-up="${esc(card.id)}" ${i === 0 ? "disabled" : ""} title="Lên">↑</button>
          <button class="small" data-down="${esc(card.id)}" ${i === c.cards.length - 1 ? "disabled" : ""} title="Xuống">↓</button>
          <button class="small" data-edit="${esc(card.id)}">Sửa</button>
          <button class="small" data-del="${esc(card.id)}" title="Xoá">×</button></div>`).join("");
      return `<h3>Luân phiên các màn</h3>
        <div class="row"><label>Chạy luân phiên</label><input type="checkbox" id="rot-on" ${c.on ? "checked" : ""}>
          <label style="min-width:0">mỗi màn</label><input type="number" id="rot-sec" min="3" max="3600" value="${esc(Math.round(c.seconds))}"> giây</div>
        <div class="list">${items || `<div class="item"><span class="hint">Chưa có thẻ. Bấm «Thêm thẻ».</span></div>`}</div>
        <div class="row"><select id="new-type">${opts(TYPES, "card")}</select><button id="btn-add" class="primary">+ Thêm thẻ</button>
          <span class="hint">Tích ô vuông để thẻ tham gia luân phiên; thứ tự = thứ tự trong danh sách.</span></div>`;
    }

    _displayHtml() {
      const s = this._state.settings, sc = this._screen || {};
      return `<h3>Màn hình camera</h3><div id="screen-box">${this._screenInner(sc)}</div>
        <div class="row"><label>Hiện cuộc gọi Zalo</label><input type="checkbox" data-set="zalo_call_screen" ${s.zalo_call_screen ? "checked" : ""}>
          <span class="hint">Tên người gọi, rồi hình video cuộc gọi lên màn.</span></div>`;
    }
    _screenInner(sc) {
      return `<div class="row"><label>Độ sáng</label><input type="number" id="scr-lum" min="1" max="100" value="${esc(sc.luminance ?? "")}"> %
          <label style="min-width:0">Sáng trong</label><input type="number" id="scr-sec" min="0" max="3600" value="${esc(sc.seconds ?? "")}"> giây <span class="hint">(0 = không tắt)</span>
          <button id="btn-scr">Lưu</button><button id="btn-on" class="small">Bật màn</button><button id="btn-off" class="small">Tắt màn</button></div>`;
    }
    _renderScreen() { const b = this._$("screen-box"); if (b) b.innerHTML = this._screenInner(this._screen || {}); }

    // ── trình soạn thẻ ───────────────────────────────────────────────
    _editorHtml() {
      const d = this._draft;
      if (!d) return "";
      const entRows = (d.entities || []).map((e, i) => `<div class="rowitem" data-row="${i}">
          <input type="text" data-ent="${i}" data-f="entity" list="dl-all" value="${esc(e.entity)}" placeholder="chọn thực thể" style="flex:1 1 100%">
          <input type="text" data-ent="${i}" data-f="name" value="${esc(e.name || "")}" placeholder="tên hiển thị (tuỳ chọn)" style="flex:1 1 150px;min-width:120px">
          ${this._colorHtml(e.color, `ent-${i}`, true)}
          <button class="small" data-rup="${i}" ${i === 0 ? "disabled" : ""}>↑</button><button class="small" data-rdown="${i}" ${i === d.entities.length - 1 ? "disabled" : ""}>↓</button>
          <button class="small" data-rdel="${i}" title="Xoá dòng">×</button></div>`).join("");
      const common = `<div class="row"><label>Tên thẻ</label><input type="text" data-d="name" value="${esc(d.name || "")}"></div>`;
      let body = "";
      if (d.type === "card") {
        body = `<div class="row"><label>Tiêu đề trên màn</label><input type="text" data-d="title" value="${esc(d.title || "")}"></div>
          <div class="row"><label>Giao diện</label><select data-d="theme">${opts(THEMES, d.theme || "light")}</select>
            <label style="min-width:0">Bố cục</label><select data-d="layout">${opts(LAYOUTS, d.layout || "list")}</select></div>
          <div class="row"><label>Cảm biến chính</label><input type="text" data-d="main_entity" list="dl-sensor" value="${esc(d.main_entity || "")}" placeholder="số lớn (tuỳ chọn)">
            <input type="text" data-d="caption" value="${esc(d.caption || "")}" placeholder="nhãn dưới số" style="max-width:180px"></div>
          <div class="row"><label>Màu số lớn</label>${this._colorHtml(d.color || "red", "color", false)}</div>
          <div class="row"><label>Màu tiêu đề</label>${this._colorHtml(d.title_color, "title_color", true)}</div>
          <div class="hint" style="margin-top:6px">Các dòng (thứ tự từ trên xuống — dùng ↑↓ để đổi vị trí):</div>${entRows}
          <div class="row"><button id="btn-addrow" class="small">+ Thêm dòng</button></div>`;
      } else if (d.type === "camera") {
        body = `<div class="row"><label>Camera</label><input type="text" data-d="camera_entity" list="dl-camera" value="${esc(d.camera_entity || "")}" placeholder="camera.cua"></div>`;
      } else if (d.type === "image") {
        body = `<div class="row"><label>Ảnh</label><input type="text" data-d="image_entity" list="dl-image" value="${esc(d.image_entity || "")}" placeholder="image.thoi_tiet"></div>
          <div class="row"><label>Hoặc địa chỉ</label><input type="text" data-d="source" value="${esc(d.source || "")}" placeholder="http://…"></div>`;
      } else {
        body = `<div class="hint">Đồng hồ đầy đủ (giờ, âm lịch, ngày lễ, thời tiết) — không có gì để chỉnh ở đây.</div>`;
      }
      return `<div class="editor"><b>${d.id ? "Sửa thẻ" : "Thẻ mới"} — ${esc(TYPES[d.type] || d.type)}</b>${common}${body}
        <div class="row" style="margin-top:10px"><button class="primary" id="btn-save">Lưu thẻ</button><button id="btn-cancel">Đóng</button></div></div>`;
    }
    _colorHtml(value, key, withAuto) {
      const sw = Object.keys(NAMED).map((n) => `<button class="sw${value === n ? " on" : ""}" style="background:${NAMED[n]}" data-color="${key}" data-val="${n}" title="${n}"></button>`).join("");
      const auto = withAuto ? `<button class="sw auto${!value ? " on" : ""}" data-color="${key}" data-val="" title="Tự động"></button>` : "";
      return `<span class="colors">${auto}${sw}<input type="color" data-colorpick="${key}" value="${hexOf(value)}"></span>`;
    }

    // ── sự kiện ──────────────────────────────────────────────────────
    _onInput(e) {
      const t = e.target;
      if (t.dataset.d !== undefined && this._draft) { this._draft[t.dataset.d] = t.value; this._schedulePreview(); }
      else if (t.dataset.ent !== undefined && this._draft) { this._draft.entities[+t.dataset.ent][t.dataset.f] = t.value; this._schedulePreview(); }
      else if (t.dataset.colorpick !== undefined) { this._setColor(t.dataset.colorpick, t.value, false); }
    }
    async _onChange(e) {
      const t = e.target;
      if (t.dataset.set !== undefined) {
        let v = t.type === "checkbox" ? t.checked : t.value;
        if (t.dataset.num) v = Number(v);
        const st = await this._call("dosen_pro/set", { changes: { [t.dataset.set]: v } });
        if (st) this._applyState(st); else this._load();
      } else if (t.dataset.d === "theme" || t.dataset.d === "layout") { this._draft[t.dataset.d] = t.value; this._schedulePreview(); }
      else if (t.dataset.join !== undefined) { this._applyState(await this._call("dosen_pro/rotation", { selected: this._joined(t.dataset.join, t.checked) })); }
      else if (t.id === "rot-on") { const st = await this._call("dosen_pro/rotation", { on: t.checked }); if (st) this._applyState(st); else this._load(); }
      else if (t.id === "rot-sec") { this._applyState(await this._call("dosen_pro/rotation", { seconds: Number(t.value) })); }
    }
    _joined(id, on) {
      const cur = new Set(this._state.carousel.selected);
      on ? cur.add(id) : cur.delete(id);
      return [...cur];
    }
    async _onClick(e) {
      const t = e.target.closest("button, .nm");
      if (!t) return;
      const d = t.dataset;
      if (t.id === "btn-add") { this._startDraft({ type: this._$("new-type").value, name: TYPES[this._$("new-type").value], color: "red", theme: "light", layout: "list", entities: [] }); }
      else if (d.pick) { this._sel = this._state.carousel.cards.find((c) => c.id === d.pick); this._draft = null; this._renderAll(); }
      else if (d.edit) { this._startDraft(JSON.parse(JSON.stringify(this._state.carousel.cards.find((c) => c.id === d.edit)))); }
      else if (d.del) { if (confirm("Xoá thẻ này?")) { this._applyState(await this._call("dosen_pro/card_delete", { ids: [d.del] })); if (this._sel?.id === d.del) { this._sel = null; this._png = null; } this._renderAll(); } }
      else if (d.up || d.down) { this._applyState(await this._call("dosen_pro/card_move", { card_id: d.up || d.down, delta: d.up ? -1 : 1 })); }
      else if (t.id === "btn-addrow") { this._draft.entities.push({ entity: "" }); this._renderEditor(); }
      else if (d.rdel !== undefined) { this._draft.entities.splice(+d.rdel, 1); this._renderEditor(); this._schedulePreview(); }
      else if (d.rup !== undefined || d.rdown !== undefined) {
        const i = +(d.rup ?? d.rdown), j = d.rup !== undefined ? i - 1 : i + 1, a = this._draft.entities;
        [a[i], a[j]] = [a[j], a[i]]; this._renderEditor(); this._schedulePreview();
      }
      else if (d.color !== undefined) { this._setColor(d.color, d.val || null, true); }
      else if (t.id === "btn-save") { await this._save(); }
      else if (t.id === "btn-cancel") { this._draft = null; this._renderAll(); }
      else if (t.id === "btn-prev") { this._preview(this._draft || this._sel); }
      else if (t.id === "btn-show") { const r = await this._call("dosen_pro/show_now", { card: this._clean(this._draft || this._sel) }); if (r) this._error(""); }
      else if (t.id === "btn-scr") { const r = await this._call("dosen_pro/screen_set", { luminance: Number(this._$("scr-lum").value), seconds: Number(this._$("scr-sec").value) }); if (r) { this._screen = r; this._renderScreen(); } }
      else if (t.id === "btn-on" || t.id === "btn-off") { const r = await this._call("dosen_pro/screen_set", { power: t.id === "btn-on" }); if (r) { this._screen = r; this._renderScreen(); } }
    }
    _setColor(key, val, rerender) {
      if (!this._draft) return;
      if (key.startsWith("ent-")) { const i = +key.slice(4); if (val) this._draft.entities[i].color = val; else delete this._draft.entities[i].color; }
      else if (val) this._draft[key] = val; else delete this._draft[key];
      if (rerender) this._renderEditor();
      this._schedulePreview();
    }
    _startDraft(card) { this._draft = card; this._sel = null; this._renderAll(); this._preview(card); }
    _renderEditor() { const el = this._$("editor"); if (el) el.innerHTML = this._editorHtml(); }

    // ── dữ liệu thẻ ──────────────────────────────────────────────────
    _clean(card) {
      if (!card) return null;
      const out = { type: card.type, name: card.name };
      if (card.id) out.id = card.id;
      // Mỗi loại thẻ chỉ lưu các trường của nó (thẻ camera/ảnh không mang theo màu, giao diện, bố cục của thẻ thông tin).
      const FIELDS = { card: ["title", "caption", "main_entity", "color", "theme", "layout", "title_color"], camera: ["camera_entity"], image: ["image_entity", "source"], clock: [] };
      for (const k of FIELDS[card.type] || []) if (card[k]) out[k] = card[k];
      if (card.type === "card") out.entities = (card.entities || []).filter((e) => e.entity).map((e) => { const r = { entity: e.entity }; if (e.name) r.name = e.name; if (e.color) r.color = e.color; return r; });
      return out;
    }
    async _save() {
      const card = this._clean(this._draft);
      const res = await this._call("dosen_pro/card_save", { card });
      if (res) { this._draft = null; this._sel = res.carousel.cards.find((c) => c.id === res.id); this._applyState(res); }
    }
    _schedulePreview() { clearTimeout(this._t); this._t = setTimeout(() => this._preview(this._draft), 450); }
    async _preview(card) {
      if (!card || !this._hass) return;
      const seq = ++this._previewSeq;
      const res = await this._call("dosen_pro/preview", { card: this._clean(card) });
      if (!res || seq !== this._previewSeq) return;
      this._png = res.png; this._note = res.note;
      const lcd = this._$("lcd"); if (lcd) lcd.innerHTML = this._previewHtml();
    }
  }

  class DosenProPanel extends HTMLElement {
    set hass(h) { this._hass = h; if (this._card) this._card.hass = h; }
    set narrow(_) {}
    set panel(_) {}
    set route(_) {}
    connectedCallback() {
      if (this._card) return;
      this.style.display = "block";
      this._card = document.createElement("dosen-pro-card");
      this._card.setConfig({});
      this.appendChild(this._card);
      if (this._hass) this._card.hass = this._hass;
    }
  }

  if (!customElements.get("dosen-pro-card")) customElements.define("dosen-pro-card", DosenProCard);
  if (!customElements.get("dosen-pro-panel")) customElements.define("dosen-pro-panel", DosenProPanel);
  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === "dosen-pro-card")) {
    window.customCards.push({ type: "dosen-pro-card", name: "Màn hình Dosen", description: "Toàn bộ cài đặt màn hình camera Dosen: màn mặc định, luân phiên, thẻ thông tin, xem trước.", preview: false });
  }
})();
