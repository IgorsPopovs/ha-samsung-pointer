/** Samsung Pointer trackpad. No dependencies, credentials, or direct TV access. */
export class MotionBuffer {
  constructor(maxAge = 100) { this.maxAge = maxAge; this.samples = []; }
  add(x, y, time) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    this.prune(time);
    this.samples.push({ x, y, time });
    // A stalled browser must not allocate an unbounded gesture history.
    if (this.samples.length > 256) this.samples.shift();
  }
  prune(now) { this.samples = this.samples.filter(s => now - s.time <= this.maxAge); }
  take(now) {
    this.prune(now);
    const sum = this.samples.reduce((v, s) => [v[0] + s.x, v[1] + s.y], [0, 0]);
    this.clear();
    return sum.map(v => Math.max(-2000, Math.min(2000, v)));
  }
  clear() { this.samples = []; }
}

export class Gesture {
  constructor(x, y, time, threshold = 3) {
    this.x = this.startX = x; this.y = this.startY = y;
    this.time = time; this.threshold = threshold; this.moved = false;
  }
  move(x, y) {
    const dx = x - this.x, dy = y - this.y;
    this.x = x; this.y = y;
    if (!this.moved) {
      if (Math.hypot(x - this.startX, y - this.startY) < this.threshold) return [0, 0];
      this.moved = true;
      return [x - this.startX, y - this.startY];
    }
    return [dx, dy];
  }
  isTap(now) { return !this.moved && now - this.time <= 300; }
}

/** At most one HA call in flight. No replay after errors/cancellation. */
export class MotionPump {
  constructor(send, { interval = 25, maxAge = 100, now = () => performance.now(),
    schedule = (fn, ms) => setTimeout(fn, ms), cancel = id => clearTimeout(id),
    onError = () => {}, onLatency = () => {} } = {}) {
    Object.assign(this, { send, interval, now, schedule, cancel, onError, onLatency });
    this.buffer = new MotionBuffer(maxAge); this.busy = false; this.timer = null;
    this.lastSend = -Infinity; this.epoch = 0; this.disposed = false;
  }
  add(x, y) {
    if (this.disposed) return;
    this.buffer.add(x, y, this.now()); this.arm();
  }
  arm() {
    if (this.disposed || this.busy || this.timer !== null || !this.buffer.samples.length) return;
    this.timer = this.schedule(() => {
      this.timer = null; void this.flush();
    }, Math.max(0, this.interval - (this.now() - this.lastSend)));
  }
  async flush() {
    if (this.disposed || this.busy) return;
    const [x, y] = this.buffer.take(this.now());
    if (!x && !y) return;
    this.busy = true; const epoch = this.epoch; this.lastSend = this.now();
    try {
      await this.send(x, y);
      if (epoch === this.epoch) this.onLatency(this.now() - this.lastSend);
    } catch (error) {
      if (epoch === this.epoch) { this.clear(); this.onError(error); }
    } finally { this.busy = false; this.arm(); }
  }
  clear() {
    this.epoch++; this.buffer.clear();
    if (this.timer !== null) this.cancel(this.timer);
    this.timer = null;
  }
  dispose() { this.clear(); this.disposed = true; }
}

const BaseElement = globalThis.HTMLElement ?? class {};
export class SamsungPointerCard extends BaseElement {
  constructor() {
    super();
    if (!this.attachShadow) return;
    this.attachShadow({ mode: 'open' });
    this._blur = () => this.cancelGesture();
    this._visibility = () => { if (document.hidden) this.cancelGesture(); };
  }
  setConfig(config) {
    if (!/^remote\.[a-z0-9_]+$/.test(config.entity ?? '')) throw new Error('Set entity to your Samsung Pointer remote');
    const sensitivity = Number(config.sensitivity ?? 1.5);
    const interval = Number(config.interval ?? 25);
    if (!Number.isFinite(sensitivity) || sensitivity < 0.25 || sensitivity > 4) throw new Error('sensitivity must be 0.25–4');
    if (!Number.isFinite(interval) || interval < 16 || interval > 50) throw new Error('interval must be 16–50 ms');
    this.cancelGesture(); this.pump?.dispose();
    this.config = { ...config, sensitivity, interval };
    this.gain = sensitivity; this.render(); this.createPump(); this.updateState();
  }
  set hass(hass) { this._hass = hass; this.updateState(); }
  getCardSize() { return 5; }
  getGridOptions() { return { columns: 12, rows: 5, min_columns: 6 }; }
  static getStubConfig() { return { entity: 'remote.s90d_pointer', sensitivity: 1.5, interval: 25 }; }
  connectedCallback() {
    window.addEventListener('blur', this._blur);
    document.addEventListener('visibilitychange', this._visibility);
    if (this.config && (!this.pump || this.pump.disposed)) this.createPump();
  }
  disconnectedCallback() {
    this.cancelGesture(); this.pump?.dispose();
    window.removeEventListener('blur', this._blur);
    document.removeEventListener('visibilitychange', this._visibility);
  }
  createPump() {
    this.pump = new MotionPump((x, y) => {
      if (!this.ready) throw new Error('Pointer disconnected');
      return this.call('samsung_pointer', 'move', { x, y, max_speed_gain: 1 });
    }, { interval: this.config.interval, onError: () => {
      this.cancelGesture(); this.note('Связь прервана. Подключите курсор заново.');
    }, onLatency: ms => {
      this.shadowRoot.querySelector('.latency').textContent = this.config.show_latency
        ? `Ответ HA: ${Math.round(ms)} мс · не задержка экрана ТВ` : '';
    } });
  }
  get ready() { return this._hass?.states?.[this.config?.entity]?.state === 'on'; }
  call(domain, service, data = {}) {
    if (!this._hass) return Promise.reject(new Error('Home Assistant unavailable'));
    return this._hass.callService(domain, service, data, { entity_id: this.config.entity });
  }
  updateState() {
    if (!this.config || !this.shadowRoot?.querySelector('.status')) return;
    const connected = this.ready;
    if (this.wasReady && !connected) this.cancelGesture();
    this.wasReady = connected;
    this.shadowRoot.querySelector('.status').textContent = connected ? 'Подключён' : 'Нет соединения';
    this.shadowRoot.querySelector('.status').classList.toggle('online', connected);
    this.shadowRoot.querySelector('.connect').disabled = Boolean(this.connecting) || connected;
  }
  note(text) { const el = this.shadowRoot?.querySelector('.hint'); if (el) el.textContent = text; }
  async connect() {
    if (this.connecting || this.ready || this.pump?.busy) return;
    this.cancelGesture(); this.connecting = true; this.updateState();
    this.note('Подключение к телевизору…');
    try { await this.call('remote', 'turn_on'); this.note('Проведите пальцем · касание — клик'); }
    catch { this.note('Не удалось подключиться. Проверьте ТВ и разрешение подключения.'); }
    finally { this.connecting = false; this.updateState(); }
  }
  cancelGesture() {
    const id = this.pointerId; this.pointerId = null; this.gesture = null;
    this.pump?.clear();
    const pad = this.shadowRoot?.querySelector('.pad');
    if (id != null && pad?.hasPointerCapture(id)) pad.releasePointerCapture(id);
    pad?.classList.remove('active');
  }
  down(e) {
    if (this.pointerId != null) { this.cancelGesture(); return; }
    if (!e.isPrimary || e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.focus({ preventScroll: true });
    if (!this.ready) { void this.connect(); return; }
    if (this.clickBusy || this.pump.busy) { this.note('Дождитесь ответа и повторите жест.'); return; }
    this.pump.clear(); this.pointerId = e.pointerId;
    this.gesture = new Gesture(e.clientX, e.clientY, performance.now());
    e.currentTarget.setPointerCapture(e.pointerId);
    e.currentTarget.classList.add('active');
    this.note('Проведите пальцем · касание — клик');
  }
  move(e) {
    if (e.pointerId !== this.pointerId || !this.gesture) return;
    e.preventDefault();
    const samples = e.getCoalescedEvents?.() ?? [];
    // Coalesced events preserve changes in direction between browser frames.
    for (const sample of samples.length ? samples : [e]) this.sample(sample);
  }
  sample(e) {
    const [x, y] = this.gesture.move(e.clientX, e.clientY);
    if (x || y) this.pump.add(x * this.gain, y * this.gain);
  }
  up(e) {
    if (e.pointerId !== this.pointerId || !this.gesture) return;
    this.sample(e); const tap = this.gesture.isTap(performance.now());
    this.pointerId = null; this.gesture = null;
    e.currentTarget.classList.remove('active');
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    // Fresh final movement is sent once; old samples expire in MotionBuffer.
    if (tap) { this.pump.clear(); void this.doClick(); }
  }
  async doClick() {
    if (!this.ready) { void this.connect(); return; }
    if (this.clickBusy || this.pump.busy || this.pointerId != null || this.pump.buffer.samples.length) {
      this.note('Дождитесь остановки курсора и нажмите ещё раз.'); return;
    }
    this.clickBusy = true;
    try { await this.call('samsung_pointer', 'click', { button: 'left' }); }
    catch { this.note('Клик не подтверждён. Проверьте соединение.'); }
    finally { this.clickBusy = false; }
  }
  render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; }
        ha-card { display:block; border-radius:var(--ha-card-border-radius,16px); padding:16px; background:var(--ha-card-background,var(--card-background-color,#202329)); color:var(--primary-text-color,#eee); }
        header { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:14px; }
        .title { font-size:16px; font-weight:600; } .status { font-size:12px; opacity:.65; }
        .online { color:var(--success-color,#66cba0); opacity:1; }
        button { font:inherit; color:inherit; background:transparent; border:1px solid var(--divider-color,#ffffff30); border-radius:12px; padding:9px 13px; cursor:pointer; }
        button:disabled { opacity:.45; cursor:default; }
        button:focus-visible,.pad:focus-visible { outline:2px solid var(--primary-color,#56b8ff); outline-offset:3px; }
        .pad { height:230px; box-sizing:border-box; border:1px solid var(--divider-color,#ffffff30); border-radius:20px; touch-action:none; user-select:none; -webkit-user-select:none; -webkit-touch-callout:none; cursor:crosshair; display:grid; place-items:center; background:radial-gradient(circle,#ffffff12 1px,transparent 1px) 0 0/12px 12px, #171b22; }
        .pad.active { border-color:var(--primary-color,#56b8ff); }
        svg { width:38px; fill:#aab4c3; pointer-events:none; }
        .controls { display:flex; align-items:center; gap:12px; margin-top:14px; }
        label { font-size:12px; flex:1; } input { display:block; width:100%; margin:8px 0 0; accent-color:var(--primary-color,#56b8ff); }
        .hint { font-size:12px; opacity:.75; min-height:32px; margin:12px 0 0; line-height:1.4; }
        .latency { font-size:11px; opacity:.6; }
        @media(max-width:360px) { ha-card {padding:12px} .pad {height:210px} }
      </style>
      <ha-card>
        <header><div><div class="title">Курсор ТВ</div><div class="status" role="status"></div></div><button class="connect">Подключить</button></header>
        <div class="pad" tabindex="0" role="application" aria-label="Трекпад телевизора. Стрелки перемещают курсор, Enter — клик.">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 2v18l5-5 4 7 3-2-4-7h7z"/></svg>
        </div>
        <div class="controls"><label>Чувствительность <output></output><input type="range" min="0.25" max="4" step="0.25" aria-label="Чувствительность"></label><button class="click">Клик</button></div>
        <p class="hint" aria-live="polite">Проведите пальцем · касание — клик</p><div class="latency"></div>
      </ha-card>`;
    const root = this.shadowRoot, pad = root.querySelector('.pad');
    root.querySelector('.connect').onclick = () => void this.connect();
    root.querySelector('.click').onclick = () => void this.doClick();
    const range = root.querySelector('input'); range.value = String(this.gain);
    root.querySelector('output').textContent = `${this.gain}×`;
    range.oninput = () => { this.cancelGesture(); this.gain = Number(range.value); root.querySelector('output').textContent = `${this.gain}×`; };
    pad.onpointerdown = e => this.down(e); pad.onpointermove = e => this.move(e); pad.onpointerup = e => this.up(e);
    pad.onpointercancel = () => this.cancelGesture();
    pad.onlostpointercapture = () => { if (this.pointerId != null) this.cancelGesture(); };
    pad.oncontextmenu = e => e.preventDefault();
    pad.onkeydown = e => {
      const directions = { ArrowLeft: [-1,0], ArrowRight: [1,0], ArrowUp: [0,-1], ArrowDown: [0,1] };
      if (directions[e.key]) {
        e.preventDefault();
        if (this.ready && !this.clickBusy) { const [x,y] = directions[e.key]; this.pump.add(x*6*this.gain,y*6*this.gain); }
      } else if ((e.key === 'Enter' || e.key === ' ') && !e.repeat) { e.preventDefault(); void this.doClick(); }
    };
  }
}
if (globalThis.customElements && !customElements.get('samsung-pointer-card')) {
  customElements.define('samsung-pointer-card', SamsungPointerCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: 'samsung-pointer-card', name: 'Samsung Pointer', description: 'Precise local TV trackpad', preview: false });
}
