const storeId = "ST1008";
const numberFmt = new Intl.NumberFormat("en-IN");

const els = {
  startReplay: document.querySelector("#startReplay"),
  resetReplay: document.querySelector("#resetReplay"),
  uploadForm: document.querySelector("#uploadForm"),
  cctvFile: document.querySelector("#cctvFile"),
  fileName: document.querySelector("#fileName"),
  uploadButton: document.querySelector("#uploadButton"),
  uploadStatus: document.querySelector("#uploadStatus"),
  uploadDetail: document.querySelector("#uploadDetail"),
  healthDot: document.querySelector("#healthDot"),
  healthText: document.querySelector("#healthText"),
  healthDetail: document.querySelector("#healthDetail"),
  replayLabel: document.querySelector("#replayLabel"),
  replayCounts: document.querySelector("#replayCounts"),
  replayBar: document.querySelector("#replayBar"),
  uniqueVisitors: document.querySelector("#uniqueVisitors"),
  eventCount: document.querySelector("#eventCount"),
  conversionRate: document.querySelector("#conversionRate"),
  convertedVisitors: document.querySelector("#convertedVisitors"),
  queueDepth: document.querySelector("#queueDepth"),
  abandonmentRate: document.querySelector("#abandonmentRate"),
  dataConfidence: document.querySelector("#dataConfidence"),
  zoneCount: document.querySelector("#zoneCount"),
  lastUpdated: document.querySelector("#lastUpdated"),
  funnel: document.querySelector("#funnel"),
  heatmap: document.querySelector("#heatmap"),
  anomalies: document.querySelector("#anomalies"),
  dwellList: document.querySelector("#dwellList"),
};

els.startReplay.addEventListener("click", async () => {
  els.startReplay.disabled = true;
  els.uploadButton.disabled = true;
  try {
    await postJson("/demo/replay/start?batch_size=22&interval_ms=550");
    await refresh();
  } catch (error) {
    renderUpload({ status: "failed", error: error.message });
  }
});

els.resetReplay.addEventListener("click", async () => {
  els.resetReplay.disabled = true;
  try {
    await postJson("/demo/replay/reset");
    els.cctvFile.value = "";
    els.fileName.textContent = "Choose CCTV footage";
    renderUpload({ status: "idle" }, "Dashboard cleared. Upload CCTV to run a new analysis.");
    await refresh();
  } catch (error) {
    renderUpload({ status: "failed", error: error.message });
  } finally {
    els.resetReplay.disabled = false;
  }
});

els.cctvFile.addEventListener("change", () => {
  const file = els.cctvFile.files[0];
  els.fileName.textContent = file ? file.name : "Choose CCTV footage";
});

els.uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = els.cctvFile.files[0];
  if (!file) {
    renderUpload({ status: "idle" }, "Select a CCTV ZIP or MP4 file first.");
    return;
  }

  els.uploadButton.disabled = true;
  const form = new FormData();
  form.append("file", file);
  try {
    const job = await uploadCctv(form);
    renderUpload(job);
  } catch (error) {
    renderUpload({ status: "failed", error: error.message });
  }
});

async function refresh() {
  const [health, replay, upload, metrics, funnel, heatmap, anomalies] = await Promise.all([
    getJson("/health"),
    getJson("/demo/replay/status"),
    getJson("/uploads/cctv/latest"),
    getJson(`/stores/${storeId}/metrics`),
    getJson(`/stores/${storeId}/funnel`),
    getJson(`/stores/${storeId}/heatmap`),
    getJson(`/stores/${storeId}/anomalies`),
  ]);

  renderHealth(health);
  renderReplay(replay);
  renderUpload(upload);
  syncControls(replay, upload);
  renderMetrics(metrics, heatmap);
  renderFunnel(funnel);
  renderHeatmap(heatmap);
  renderAnomalies(anomalies);
  renderDwell(metrics.avg_dwell_ms_per_zone || {});
}

function renderHealth(health) {
  if (health.status === "ok") {
    els.healthDot.className = "dot ok";
    els.healthText.textContent = "API healthy";
    els.healthDetail.textContent = "All feeds within freshness window";
    return;
  }
  els.healthDot.className = "dot warn";
  els.healthText.textContent = "Feed stale, API online";
  els.healthDetail.textContent = staleFeedDetail(health);
}

function renderReplay(replay) {
  const total = replay.total_events || 0;
  const ingested = replay.ingested_events || 0;
  const pct = total ? Math.min(100, Math.round((ingested / total) * 100)) : 0;
  els.replayLabel.textContent = replay.running
    ? "Sample replay streaming"
    : total && ingested === total
      ? "Sample replay complete"
      : "Sample replay idle";
  els.replayCounts.textContent = `${numberFmt.format(ingested)} / ${numberFmt.format(total)} events`;
  els.replayBar.style.width = `${pct}%`;
}

function renderUpload(upload, overrideDetail) {
  const status = upload.status || "idle";
  const filename = upload.filename ? ` for ${upload.filename}` : "";
  els.uploadStatus.textContent = label(status === "idle" ? "No upload running" : `${status}${filename}`);
  els.uploadDetail.textContent = overrideDetail || uploadDetail(upload);
}

function syncControls(replay, upload) {
  const uploadRunning = upload.status === "queued" || upload.status === "processing";
  els.startReplay.disabled = replay.running || uploadRunning;
  els.uploadButton.disabled = replay.running || uploadRunning;
}

function renderMetrics(metrics, heatmap) {
  els.uniqueVisitors.textContent = numberFmt.format(metrics.unique_visitors || 0);
  els.eventCount.textContent = `${numberFmt.format(metrics.event_count || 0)} customer events`;
  els.conversionRate.textContent = percent(metrics.conversion_rate || 0);
  els.convertedVisitors.textContent = `${numberFmt.format(metrics.converted_visitors || 0)} converted sessions`;
  els.queueDepth.textContent = numberFmt.format(metrics.queue_depth || 0);
  els.abandonmentRate.textContent = `${percent(metrics.abandonment_rate || 0)} abandonment`;
  els.dataConfidence.textContent = heatmap.data_confidence || "LOW";
  els.zoneCount.textContent = `${(heatmap.zones || []).length} zones observed`;
  els.lastUpdated.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function renderFunnel(payload) {
  const stages = payload.stages || [];
  const maxCount = Math.max(1, ...stages.map((stage) => stage.count));
  els.funnel.innerHTML = stages
    .map((stage) => {
      const width = Math.max(2, Math.round((stage.count / maxCount) * 100));
      return `<div class="funnel-row">
        <div><strong>${label(stage.stage)}</strong><small>${percent(stage.dropoff_pct)} drop-off</small></div>
        <div class="bar"><span style="width:${width}%"></span></div>
        <strong>${numberFmt.format(stage.count)}</strong>
      </div>`;
    })
    .join("");
}

function renderHeatmap(payload) {
  const zones = [...(payload.zones || [])].sort((a, b) => b.heat_score - a.heat_score);
  if (!zones.length) {
    els.heatmap.innerHTML = `<div class="zone-tile" style="background:#eef3f5"><strong>No zones yet</strong><span>Upload footage or replay the sample to populate activity.</span></div>`;
    return;
  }
  els.heatmap.innerHTML = zones
    .map((zone) => {
      const hue = 180 - Math.min(130, zone.heat_score * 1.3);
      const light = 92 - Math.min(35, zone.heat_score * 0.35);
      return `<div class="zone-tile" style="background:hsl(${hue} 70% ${light}%)">
        <strong>${zone.zone_id.replaceAll("_", " ")}</strong>
        <span>${numberFmt.format(zone.visit_count)} visits · ${Math.round(zone.avg_dwell_ms / 1000)}s dwell</span>
      </div>`;
    })
    .join("");
}

function renderAnomalies(payload) {
  const anomalies = payload.anomalies || [];
  if (!anomalies.length) {
    els.anomalies.innerHTML = `<div class="anomaly"><strong>No active anomalies</strong><p>Traffic, queue, and zone activity are within configured rules.</p></div>`;
    return;
  }
  els.anomalies.innerHTML = anomalies
    .map((item) => `<div class="anomaly ${String(item.severity || "").toLowerCase()}">
      <strong>${label(item.type || "Signal")}</strong>
      <p>${item.suggested_action || "Review store activity."}</p>
    </div>`)
    .join("");
}

function renderDwell(dwell) {
  const rows = Object.entries(dwell)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);
  if (!rows.length) {
    els.dwellList.innerHTML = `<div class="dwell-row"><strong>No dwell yet</strong><span>0s</span></div>`;
    return;
  }
  els.dwellList.innerHTML = rows
    .map(([zone, ms]) => `<div class="dwell-row"><strong>${zone.replaceAll("_", " ")}</strong><span>${Math.round(ms / 1000)}s avg</span></div>`)
    .join("");
}

async function getJson(url) {
  const response = await fetch(url, { headers: { "x-trace-id": `dashboard-${Date.now()}` } });
  if (!response.ok) throw new Error(`${url} returned ${response.status}`);
  return response.json();
}

async function postJson(url) {
  const response = await fetch(url, { method: "POST", headers: { "x-trace-id": `dashboard-${Date.now()}` } });
  if (!response.ok) throw new Error(`${url} returned ${response.status}`);
  return response.json();
}

async function uploadCctv(form) {
  const response = await fetch("/uploads/cctv", {
    method: "POST",
    headers: { "x-trace-id": `dashboard-upload-${Date.now()}` },
    body: form,
  });
  if (!response.ok) throw new Error(`/uploads/cctv returned ${response.status}`);
  return response.json();
}

function percent(value) {
  return `${(value * 100).toFixed(1)}%`;
}

function label(value) {
  return String(value)
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function staleFeedDetail(health) {
  const stores = Object.values(health.stores || {});
  const stale = stores.find((store) => store.warning === "STALE_FEED");
  if (!stale || !stale.last_event_timestamp) return "Historical sample feed";
  const last = new Date(stale.last_event_timestamp);
  return `Last event ${last.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}`;
}

function uploadDetail(upload) {
  if (!upload || upload.status === "idle") return "Upload CCTV to run a new analysis. Sample replay is only for quick demo data.";
  if (upload.status === "queued") return "Queued for analysis.";
  if (upload.status === "processing") return "Detector is converting footage into customer journey events.";
  if (upload.status === "completed") {
    return `${numberFmt.format(upload.accepted_events || 0)} events loaded, ${numberFmt.format(upload.rejected_events || 0)} rejected.`;
  }
  if (upload.status === "failed") return upload.error || "Upload analysis failed.";
  return "Waiting for upload status.";
}

refresh();
setInterval(refresh, 1500);
