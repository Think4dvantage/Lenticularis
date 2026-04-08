/**
 * replay.js — Weather data replay engine
 *
 * Loads historical data for all stations via GET /api/stations/replay
 * and steps through time frames at a configurable speed multiplier.
 * When the window extends past "now", forecast data is appended
 * seamlessly so replay continues into the future.
 *
 * Usage:
 *   const engine = new ReplayEngine(onFrame, onStateChange);
 *   await engine.load({ hours: 24 });
 *   engine.play();
 *
 * onFrame(stationsSnapshot, isoTimestamp, index, total, isForecast)
 *   stationsSnapshot — array in the same shape as GET /api/stations,
 *     each station's `latest` set to its most-recent measurement ≤ current ts.
 *   isForecast — true when the current frame comes from forecast data
 *     (timestamp ≥ forecastFrom boundary returned by the API).
 *
 * onStateChange(state)  — 'idle' | 'loading' | 'playing' | 'paused'
 */

// ms between frames at each speed multiplier
const REPLAY_FRAME_INTERVAL = { 10: 800, 50: 200, 100: 80, 200: 40, 500: 16 };
// How long a prefetched entry stays valid (10 min — aligns with MeteoSwiss update cadence)
const REPLAY_CACHE_TTL_MS = 10 * 60 * 1000;

class ReplayEngine {
  constructor(onFrame, onStateChange) {
    this._onFrame = onFrame;
    this._onStateChange = onStateChange;
    this._data = null;        // raw response data dict
    this._timestamps = [];    // sorted unique ISO strings
    this._forecastFrom = null; // ISO string — boundary between observations and forecast
    this._currentIndex = 0;
    this._playing = false;
    this._speed = 10;
    this._timer = null;
    this._state = 'idle';
    this._cache = new Map();  // url → { json, fetchedAt }
  }

  /** Build the replay API URL for a set of load params (shared by load + prefetch). */
  _buildUrl(params) {
    let url = '/api/stations/replay?';
    if (params.hours != null) {
      url += `hours=${params.hours}`;
    } else if (params.start && params.end) {
      url += `start=${encodeURIComponent(params.start)}&end=${encodeURIComponent(params.end)}`;
    } else {
      url += 'hours=24';
    }
    if (params.forecast_hours != null) url += `&forecast_hours=${params.forecast_hours}`;
    if (params.include_forecast === false) url += '&include_forecast=false';
    return url;
  }

  /**
   * Pre-fetch and cache a replay request in the background.
   * No-op if an entry for this exact URL already exists and is still fresh.
   * Errors are swallowed — this is best-effort.
   */
  async prefetch(params, signal) {
    const url = this._buildUrl(params);
    const cached = this._cache.get(url);
    if (cached && Date.now() - cached.fetchedAt < REPLAY_CACHE_TTL_MS) {
      console.log(`[Lenti:replay] prefetch SKIP (cache fresh, age=${((Date.now()-cached.fetchedAt)/1000).toFixed(0)}s): ${url}`);
      return;
    }
    console.log(`[Lenti:replay] prefetch START: ${url}`);
    const t0 = performance.now();
    try {
      const res = await fetch(url, { signal });
      if (!res.ok) { console.warn(`[Lenti:replay] prefetch HTTP ${res.status}: ${url}`); return; }
      const json = await res.json();
      this._cache.set(url, { json, fetchedAt: Date.now() });
      console.log(`[Lenti:replay] prefetch DONE in ${(performance.now()-t0).toFixed(0)}ms: ${url}`);
    } catch (err) {
      if (err.name === 'AbortError') {
        console.log(`[Lenti:replay] prefetch ABORTED (page unload): ${url}`);
      } else {
        console.warn(`[Lenti:replay] prefetch ERROR: ${url}`, err);
      }
    }
  }

  /** Load replay data. Serves from cache when available and fresh. */
  async load(params) {
    const url = this._buildUrl(params);
    const cached = this._cache.get(url);

    if (cached && Date.now() - cached.fetchedAt < REPLAY_CACHE_TTL_MS) {
      // Cache hit — apply instantly with no loading indicator
      console.log(`[Lenti:replay] load CACHE HIT (age=${((Date.now()-cached.fetchedAt)/1000).toFixed(0)}s): ${url}`);
      this.pause();
      this._applyJson(cached.json);
      this._setState('paused');
      if (this._timestamps.length > 0) this._emitFrame();
      console.log(`[Lenti:replay] load applied ${this._timestamps.length} frames from cache`);
      return this._timestamps.length;
    }

    // Cache miss — show loading state and fetch
    console.log(`[Lenti:replay] load CACHE MISS — fetching: ${url}`);
    const t0 = performance.now();
    this._setState('loading');
    this.pause();
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    this._cache.set(url, { json, fetchedAt: Date.now() });
    this._applyJson(json);
    this._setState('paused');
    if (this._timestamps.length > 0) this._emitFrame();
    console.log(`[Lenti:replay] load fetched ${this._timestamps.length} frames in ${(performance.now()-t0).toFixed(0)}ms`);
    return this._timestamps.length;
  }

  _applyJson(json) {
    this._data         = json.data         || {};
    this._forecastFrom = json.forecast_from || null;
    const tsSet = new Set();
    for (const entry of Object.values(this._data)) {
      for (const m of entry.measurements) tsSet.add(m.timestamp);
    }
    this._timestamps = Array.from(tsSet).sort();
    this._currentIndex = 0;
  }

  play() {
    if (!this._data || this._playing) return;
    if (this._currentIndex >= this._timestamps.length - 1) this._currentIndex = 0;
    this._playing = true;
    this._setState('playing');
    this._tick();
  }

  pause() {
    this._playing = false;
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
    if (this._state === 'playing') this._setState('paused');
  }

  toggle() {
    if (this._playing) this.pause(); else this.play();
  }

  seekTo(index) {
    this._currentIndex = Math.max(0, Math.min(index, this._timestamps.length - 1));
    this._emitFrame();
  }

  stepForward() { this.seekTo(this._currentIndex + 1); }
  stepBack()    { this.seekTo(this._currentIndex - 1); }
  seekFirst()   { this.seekTo(0); }
  seekLast()    { this.seekTo(this._timestamps.length - 1); }

  setSpeed(s) {
    this._speed = s;
    if (this._playing) { clearTimeout(this._timer); this._tick(); }
  }

  get currentTime()    { return this._timestamps[this._currentIndex] ?? null; }
  get totalFrames()    { return this._timestamps.length; }
  get currentIndex()   { return this._currentIndex; }
  get isPlaying()      { return this._playing; }
  get hasData()        { return this._data !== null && this._timestamps.length > 0; }
  get forecastFrom()   { return this._forecastFrom; }
  /** True when the current frame is a forecast (past the observation/forecast boundary). */
  get isForecastFrame() {
    if (!this._forecastFrom) return false;
    const ts = this._timestamps[this._currentIndex];
    return ts != null && ts >= this._forecastFrom;
  }

  destroy() { this.pause(); this._data = null; this._timestamps = []; this._setState('idle'); }

  // ── private ───────────────────────────────────────────────────────────────

  _tick() {
    if (!this._playing) return;
    const delay = REPLAY_FRAME_INTERVAL[this._speed] ?? 800;
    this._timer = setTimeout(() => {
      if (this._currentIndex >= this._timestamps.length - 1) {
        this.pause();
        return;
      }
      this._currentIndex++;
      this._emitFrame();
      this._tick();
    }, delay);
  }

  _emitFrame() {
    const ts = this._timestamps[this._currentIndex];
    this._onFrame(this._buildSnapshot(ts), ts, this._currentIndex, this._timestamps.length, this.isForecastFrame);
  }

  /**
   * Build a station-list snapshot (same shape as /api/stations) where each
   * station's `latest` is its most-recent measurement with timestamp <= ts.
   * Measurements are pre-sorted by time, so we scan forward and keep the last hit.
   */
  _buildSnapshot(ts) {
    const out = [];
    for (const entry of Object.values(this._data)) {
      let latest = null;
      for (const m of entry.measurements) {
        if (m.timestamp <= ts) latest = m;
        else break;
      }
      out.push({ ...entry.station, latest });
    }
    return out;
  }

  _setState(s) {
    this._state = s;
    this._onStateChange(s);
  }
}
