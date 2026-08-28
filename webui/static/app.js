const ui = {
  connectionDot: document.querySelector("#connection-dot"),
  connectionLabel: document.querySelector("#connection-label"),
  connectionDetail: document.querySelector("#connection-detail"),
  sdkBadge: document.querySelector("#sdk-badge"),
  playbackBadge: document.querySelector("#playback-badge"),
  playbackDescription: document.querySelector("#playback-description"),
  songList: document.querySelector("#song-list"),
  logs: document.querySelector("#logs"),
  connect: document.querySelector("#connect-button"),
  disconnect: document.querySelector("#disconnect-button"),
  reset: document.querySelector("#reset-button"),
  play: document.querySelector("#play-button"),
  stop: document.querySelector("#stop-button"),
  refresh: document.querySelector("#refresh-button"),
  toast: document.querySelector("#toast"),
};

let toastTimer;
let latestStatus;

function toast(message, isError = false) {
  window.clearTimeout(toastTimer);
  ui.toast.textContent = message;
  ui.toast.classList.toggle("error", isError);
  ui.toast.classList.add("visible");
  toastTimer = window.setTimeout(() => ui.toast.classList.remove("visible"), 4200);
}

async function request(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok || !body.ok) throw new Error(body.error || "请求失败");
  return body.data;
}

async function runAction(path, payload, successMessage) {
  try {
    render(await request(path, payload));
    if (successMessage) toast(successMessage);
  } catch (error) {
    toast(error.message, true);
    await refresh();
  }
}

function renderSongs(status) {
  const playing = Boolean(status.playback);
  ui.songList.replaceChildren(...status.songs.map((song, index) => {
    const button = document.createElement("button");
    button.className = `song${song.key === status.selected_song ? " selected" : ""}`;
    button.type = "button";
    button.disabled = playing;
    button.innerHTML = `<span class="song-kicker">0${index + 1} · ${song.script}</span><strong>《${song.title}》</strong><span>${song.description}</span>`;
    button.addEventListener("click", () => runAction("/api/select-song", { song: song.key }, `已选择《${song.title}》`));
    return button;
  }));
}

function render(status) {
  latestStatus = status;
  const { connection, playback } = status;
  ui.connectionDot.className = `status-dot ${connection.state}`;
  ui.connectionLabel.textContent = connection.label;
  ui.connectionDetail.textContent = connection.detail;
  ui.sdkBadge.textContent = status.sdk_available ? "SDK 可用" : "SDK 不可用";
  ui.sdkBadge.className = `badge${status.sdk_available ? "" : " muted"}`;
  ui.playbackBadge.textContent = playback ? `演奏中 · PID ${playback.pid}` : "空闲";
  ui.playbackBadge.className = `badge ${playback ? "playing" : "muted"}`;
  ui.playbackDescription.textContent = playback
    ? `正在演奏《${playback.title}》，启动于 ${playback.started_at}。`
    : "选择一首曲目后启动。运行日志会显示在下方。";
  ui.logs.textContent = status.logs.length ? status.logs.join("\n") : "暂无日志。";
  ui.logs.scrollTop = ui.logs.scrollHeight;

  const connected = connection.state === "connected";
  ui.connect.disabled = playback || connected || !status.sdk_available;
  ui.disconnect.disabled = playback || !connected;
  ui.reset.disabled = playback || !connected;
  ui.play.disabled = Boolean(playback);
  ui.stop.disabled = !playback;
  renderSongs(status);
}

async function refresh() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    const body = await response.json();
    if (!body.ok) throw new Error(body.error || "无法读取状态");
    render(body.data);
  } catch (error) {
    toast(`无法连接 Web UI：${error.message}`, true);
  }
}

ui.connect.addEventListener("click", () => runAction("/api/connect", {}, "机器人已连接"));
ui.disconnect.addEventListener("click", () => runAction("/api/disconnect", {}, "机器人已断开"));
ui.reset.addEventListener("click", () => runAction("/api/reset-hands", {}, "已发送双手安全归零指令"));
ui.play.addEventListener("click", () => runAction("/api/play", {}, "已启动演奏进程"));
ui.stop.addEventListener("click", () => runAction("/api/stop", {}, "已发送软停止信号"));
ui.refresh.addEventListener("click", refresh);

refresh();
window.setInterval(refresh, 1500);
