const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("sentinelDesktop", {
  set_window_theme: (args) =>
    ipcRenderer.invoke("desktop:set_window_theme", args),
  load_server: () => ipcRenderer.invoke("desktop:load_server"),
  test_server: (args) => ipcRenderer.invoke("desktop:test_server", args),
  save_server: (args) => ipcRenderer.invoke("desktop:save_server", args),
  request_open: (args) => ipcRenderer.invoke("desktop:request_open", args),
  request_send: (args) => ipcRenderer.invoke("desktop:request_send", args),
  request_read: (args) => ipcRenderer.invoke("desktop:request_read", args),
  request_cancel: (args) => ipcRenderer.invoke("desktop:request_cancel", args),
  save_file: (args) => ipcRenderer.invoke("desktop:save_file", args),
  open_external: (args) => ipcRenderer.invoke("desktop:open_external", args),
});
