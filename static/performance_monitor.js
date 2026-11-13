/** Four-stage observations of the existing fingerprinting flow. */
export class PerformanceMonitor {
  constructor(modality) {
    this.started = performance.now();
    this.responses = new WeakMap();
    this.pendingBodies = [];
    this.networkRequests = [];
    this.round = null;
    this.finished = false;
    this.report = {
      schema_version: 2,
      modality,
      started_at: new Date().toISOString(),
      started_at_ms: performance.timeOrigin + this.started,
      completed_at_ms: null,
      elapsed_ms: null,
      elapsed_wall_ms: null,
      storage_excluded_ms: 0,
      outcome: null,
      stages_ms: { static: 0, challenge: 0, dynamic: 0, result: 0 },
      pure_render_ms: 0,
      server_ms: {
        challenge: 0,
        primitive: 0,
        hash: 0,
        verify: 0,
        decode: 0,
        storage: 0,
        body_read: 0,
        total: 0,
      },
      samples: [],
      artifacts: [],
      requests: [],
      request_body_bytes: 0,
      response_body_bytes: 0,
      audio_buffer_upload_bytes: modality === "audio" ? 0 : null,
    };
  }

  record(phase, started, excludedBefore) {
    const wall = performance.now() - started;
    const excluded = this.report.server_ms.storage - excludedBefore;
    const duration = Math.max(0, wall - excluded);
    if (phase === "render") {
      this.report.pure_render_ms += duration;
    } else {
      this.report.stages_ms[phase] += duration;
    }
    this.report.samples.push({
      phase,
      round: this.round,
      duration_ms: duration,
      wall_ms: wall,
      storage_excluded_ms: excluded,
    });
  }

  async measure(phase, operation) {
    const started = performance.now();
    const excludedBefore = this.report.server_ms.storage;
    try {
      return await operation();
    } finally {
      this.record(phase, started, excludedBefore);
    }
  }

  measureSync(phase, operation) {
    const started = performance.now();
    const excludedBefore = this.report.server_ms.storage;
    try {
      return operation();
    } finally {
      this.record(phase, started, excludedBefore);
    }
  }

  async fetch(label, url, options = {}) {
    const started = performance.now();
    const entry = {
      label,
      round: this.round,
      method: options.method || "GET",
      status: null,
      response_consumed: false,
      rpc_elapsed_ms: null,
      rpc_wall_ms: null,
      network_elapsed_ms: null,
      network_wall_ms: null,
      storage_excluded_ms: 0,
      request_body_bytes: null,
      response_body_bytes: null,
      response_transfer_bytes: null,
      server_ms: {},
      error: null,
    };
    this.report.requests.push(entry);
    const pending = {
      entry,
      url: new URL(url, location.href).href,
      started,
      headersReceived: null,
    };
    this.pendingBodies.push({ entry, body: options.body || "" });
    this.networkRequests.push(pending);
    try {
      const response = await fetch(url, options);
      pending.headersReceived = performance.now();
      entry.status = response.status;
      entry.rpc_wall_ms = pending.headersReceived - started;
      const contentLength = response.headers.get("Content-Length");
      if (contentLength !== null) entry.response_body_bytes = Number(contentLength);
      const timingHeader = response.headers.get("Server-Timing") || "";
      for (const metric of timingHeader.split(",")) {
        const match = metric.trim().match(/^sbfp_(\w+);dur=([\d.]+)$/);
        if (!match) continue;
        const duration = Number(match[2]);
        entry.server_ms[match[1]] = duration;
        this.report.server_ms[match[1]] = (this.report.server_ms[match[1]] || 0) + duration;
      }
      entry.storage_excluded_ms = entry.server_ms.storage || 0;
      this.report.storage_excluded_ms = this.report.server_ms.storage;
      entry.rpc_elapsed_ms = Math.max(0, entry.rpc_wall_ms - entry.storage_excluded_ms);
      this.responses.set(response, { entry, started });
      return response;
    } catch (error) {
      entry.error = "fetch_failed";
      entry.rpc_wall_ms = performance.now() - started;
      entry.rpc_elapsed_ms = entry.rpc_wall_ms;
      throw error;
    }
  }

  async read(response, method) {
    const request = this.responses.get(response);
    try {
      return await response[method]();
    } catch (error) {
      if (request) request.entry.error = "response_read_failed";
      throw error;
    } finally {
      if (request) {
        request.entry.response_consumed = true;
        request.entry.rpc_wall_ms = performance.now() - request.started;
        request.entry.rpc_elapsed_ms = Math.max(0, request.entry.rpc_wall_ms - request.entry.storage_excluded_ms);
      }
    }
  }

  recordCanvas(dataUrl, width, height) {
    const base64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
    const padding = base64.endsWith("==") ? 2 : Number(base64.endsWith("="));
    this.report.artifacts.push({
      round: this.round,
      kind: "canvas",
      raw_rgba_bytes: width * height * 4,
      png_bytes: base64.length * 3 / 4 - padding,
    });
  }

  recordAudio(pcm) {
    this.report.artifacts.push({ round: this.round, kind: "audio", pcm_bytes: pcm.byteLength });
  }

  finish(outcome) {
    if (this.finished) return this.report;
    const ended = performance.now();
    this.finished = true;
    this.report.completed_at_ms = performance.timeOrigin + ended;
    this.report.elapsed_wall_ms = ended - this.started;
    this.report.elapsed_ms = Math.max(0, this.report.elapsed_wall_ms - this.report.storage_excluded_ms);
    this.report.outcome = outcome;
    // Byte counting follows the measured authentication endpoint.
    const encoder = new TextEncoder();
    for (const { entry, body } of this.pendingBodies) {
      entry.request_body_bytes = typeof body === "string" ? encoder.encode(body).byteLength : body.byteLength;
      this.report.request_body_bytes += entry.request_body_bytes;
    }
    this.pendingBodies.length = 0;
    this.report.request_count = this.report.requests.length;
    if (this.report.modality === "audio") {
      this.report.audio_buffer_upload_bytes = this.report.requests
        .filter((entry) => entry.label === "audio_upload")
        .reduce((sum, entry) => sum + entry.request_body_bytes, 0);
    }
    this.refreshNetworkTimings();
    return this.report;
  }

  refreshNetworkTimings() {
    for (const { entry, url, started, headersReceived } of this.networkRequests) {
      const resource = performance.getEntriesByName(url, "resource").find(
        (item) => item.startTime >= started && item.startTime <= headersReceived && item.initiatorType === "fetch"
      );
      if (resource) {
        entry.network_wall_ms = resource.duration;
        entry.network_elapsed_ms = Math.max(0, resource.duration - entry.storage_excluded_ms);
        entry.response_body_bytes = resource.encodedBodySize;
        entry.response_transfer_bytes = resource.transferSize;
      }
    }
    const responses = this.report.requests.map((entry) => entry.response_body_bytes);
    this.report.response_body_bytes = responses.every((size) => size !== null)
      ? responses.reduce((sum, size) => sum + size, 0)
      : null;
  }
}

/** Adjust the parallel join before adding shared setup and final verification. */
export function adjustedEndToEnd(shared, modalities, registrationWaitMs = 0) {
  const reports = Object.values(modalities);
  let parallelStorage = 0;
  if (reports.length) {
    const latestWall = Math.max(...reports.map((report) => report.completed_at_ms));
    const latestAdjusted = Math.max(...reports.map((report) => report.completed_at_ms - report.storage_excluded_ms));
    parallelStorage = latestWall - latestAdjusted;
  }
  return Math.max(0, shared.elapsed_wall_ms - shared.storage_excluded_ms - parallelStorage - registrationWaitMs);
}
