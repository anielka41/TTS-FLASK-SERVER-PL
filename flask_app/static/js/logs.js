// logs.js - Logika zakładki "Logi"

const logsModule = {
  currentLogs: {},
  activeLogFile: null,

  async loadLogs() {
    try {
      const resp = await fetch("/api/logs");
      const data = await resp.json();
      if (data.success) {
        this.currentLogs = data.logs || {};
        this.renderTabs();
      } else {
        showToast("Błąd pobierania logów: " + data.error, "error");
      }
    } catch (e) {
      console.error("logsModule.loadLogs:", e);
      showToast("Błąd pobierania logów", "error");
    }
  },

  renderTabs() {
    const nav = document.getElementById("logs-tabs-nav");
    nav.innerHTML = "";
    
    const fileNames = Object.keys(this.currentLogs).sort();
    
    if (fileNames.length === 0) {
      document.getElementById("log-viewer").textContent = "Brak dostępnych logów.";
      document.getElementById("btn-clear-active-log").style.display = "none";
      this.activeLogFile = null;
      return;
    }

    fileNames.forEach(filename => {
      const btn = document.createElement("button");
      btn.className = "tab-btn";
      
      // Keep track of active tab
      if (this.activeLogFile === filename) {
        btn.classList.add("active");
      }
      
      btn.textContent = filename;
      btn.onclick = () => this.switchTab(filename);
      nav.appendChild(btn);
    });

    // If no active tab or active tab was deleted, select the first one
    if (!this.activeLogFile || !this.currentLogs[this.activeLogFile]) {
      this.switchTab(fileNames[0]);
    } else {
      // Just update the viewer for the current active tab
      this.switchTab(this.activeLogFile);
    }
  },

  switchTab(filename) {
    this.activeLogFile = filename;
    
    // Update active class on buttons
    const nav = document.getElementById("logs-tabs-nav");
    Array.from(nav.children).forEach(btn => {
      if (btn.textContent === filename) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
    });

    // Update viewer content
    const viewer = document.getElementById("log-viewer");
    viewer.textContent = this.currentLogs[filename] || "";
    
    // Scroll to bottom
    const container = document.getElementById("logs-content-container");
    container.scrollTop = container.scrollHeight;
    
    // Show clear active button
    document.getElementById("btn-clear-active-log").style.display = "inline-block";
  },

  async clearActiveLog() {
    if (!this.activeLogFile) return;
    
    if (!confirm(`Czy na pewno chcesz wyczyścić plik ${this.activeLogFile}?`)) {
      return;
    }

    try {
      const resp = await fetch(`/api/logs/${this.activeLogFile}`, {
        method: "DELETE"
      });
      const data = await resp.json();
      if (data.success) {
        showToast(data.message, "success");
        // Reload logs to get the empty state
        await this.loadLogs();
      } else {
        showToast("Błąd: " + data.error, "error");
      }
    } catch (e) {
      console.error("logsModule.clearActiveLog:", e);
      showToast("Błąd podczas czyszczenia logu", "error");
    }
  },

  async clearAllLogs() {
    if (!confirm("Czy na pewno chcesz wyczyścić zawartość wszystkich plików logów?")) {
      return;
    }

    try {
      const resp = await fetch("/api/logs", {
        method: "DELETE"
      });
      const data = await resp.json();
      if (data.success) {
        showToast(data.message, "success");
        // Reload logs
        await this.loadLogs();
      } else {
        showToast("Błąd: " + data.error, "error");
      }
    } catch (e) {
      console.error("logsModule.clearAllLogs:", e);
      showToast("Błąd podczas czyszczenia logów", "error");
    }
  }
};
