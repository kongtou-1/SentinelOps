import {
  app,
  BrowserWindow,
  Menu,
  dialog,
  ipcMain,
  net,
  protocol,
  session,
  shell,
} from "electron";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { Network } from "./network.mjs";

const directory = path.dirname(fileURLToPath(import.meta.url));
const uiRoot = path.resolve(directory, "../dist");
const dev = !app.isPackaged && process.argv.includes("--dev");
const testDirectory = process.env.SENTINELOPS_DESKTOP_TEST_DIR;
app.setName("SentinelOps");
if (testDirectory) app.setPath("userData", path.resolve(testDirectory));
protocol.registerSchemesAsPrivileged([
  {
    scheme: "sentinelops",
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
    },
  },
]);
const uiURL = dev ? "http://127.0.0.1:5174/" : "sentinelops://app/";
const titleBarHeight = 44;
const titleBarThemes = {
  dark: { color: "#15171a", symbolColor: "#c3c6cb", height: titleBarHeight },
  light: { color: "#ffffff", symbolColor: "#475569", height: titleBarHeight },
};
const csp =
  "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src " +
  (dev ? "'self' ws://127.0.0.1:5174" : "'none'") +
  "; object-src 'none'; base-uri 'self'; form-action 'none'";
let window, network;
const trusted = (value) => {
  try {
    const url = new URL(value);
    return dev
      ? url.origin === "http://127.0.0.1:5174"
      : url.protocol === "sentinelops:" && url.host === "app";
  } catch {
    return false;
  }
};

async function start() {
  const configDirectory = path.join(app.getPath("userData"), "config");
  const configPath = path.join(configDirectory, "connection.json");
  let saved = "";
  try {
    saved = JSON.parse(await readFile(configPath, "utf8")).url;
  } catch {
    /* First launch. */
  }
  try {
    network = new Network(
      (url, init) =>
        net.fetch(url, { ...init, credentials: "omit", cache: "no-store" }),
      saved,
    );
  } catch {
    network = new Network((url, init) =>
      net.fetch(url, { ...init, credentials: "omit", cache: "no-store" }),
    );
  }

  protocol.handle("sentinelops", async (request) => {
    try {
      const url = new URL(request.url);
      if (url.host !== "app") return new Response(null, { status: 403 });
      const file = path.resolve(
        uiRoot,
        "." +
          decodeURIComponent(
            url.pathname === "/" ? "/index.html" : url.pathname,
          ),
      );
      if (!file.startsWith(uiRoot + path.sep))
        return new Response(null, { status: 403 });
      const response = await net.fetch(pathToFileURL(file).href);
      const headers = new Headers(response.headers);
      headers.set("Content-Security-Policy", csp);
      return new Response(response.body, { status: response.status, headers });
    } catch {
      return new Response(null, { status: 404 });
    }
  });

  const handlers = {
    set_window_theme: ({ theme }) => {
      if (theme === "dark" || theme === "light")
        window.setTitleBarOverlay(titleBarThemes[theme]);
    },
    load_server: () => ({ ...network.server }),
    test_server: ({ url }) => network.testServer(url),
    save_server: async ({ url }) => {
      const normalized = await network.testServer(url);
      await mkdir(configDirectory, { recursive: true });
      await writeFile(
        configPath,
        JSON.stringify({ url: normalized }, null, 2),
        "utf8",
      );
      return network.replace(normalized);
    },
    request_open: (args) => network.open(args),
    request_send: ({ id }) => network.send(id),
    request_read: ({ id }) => network.read(id),
    request_cancel: ({ id }) => network.cancel(id),
    save_file: async ({ name, data }) => {
      const chosen = await dialog.showSaveDialog(window, {
        defaultPath: path.basename(name),
        title: "保存文件",
      });
      if (chosen.canceled || !chosen.filePath) return false;
      await writeFile(chosen.filePath, new Uint8Array(data));
      return true;
    },
    open_external: async ({ url: value }) => {
      const url = new URL(value);
      if (
        !["http:", "https:"].includes(url.protocol) ||
        url.username ||
        url.password
      )
        throw new Error("只允许打开 HTTP 或 HTTPS 链接");
      await shell.openExternal(url.href);
    },
  };
  for (const [name, handler] of Object.entries(handlers)) {
    ipcMain.handle("desktop:" + name, (event, args) => {
      if (
        !window ||
        event.senderFrame !== window.webContents.mainFrame ||
        !trusted(event.senderFrame.url)
      )
        throw new Error("无效的应用页面");
      return handler(args);
    });
  }

  session.defaultSession.setPermissionRequestHandler(
    (_contents, _permission, callback) => callback(false),
  );
  session.defaultSession.setPermissionCheckHandler(() => false);
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const headers = { ...details.responseHeaders };
    if (trusted(details.url)) headers["Content-Security-Policy"] = [csp];
    callback({ responseHeaders: headers });
  });
  Menu.setApplicationMenu(null);
  window = new BrowserWindow({
    title: "SentinelOps",
    width: 1440,
    height: 960,
    minWidth: 1000,
    minHeight: 680,
    titleBarStyle: "hidden",
    titleBarOverlay: titleBarThemes.dark,
    backgroundColor: "#0c0d0f",
    icon: path.join(directory, "../resources/icon.png"),
    webPreferences: {
      zoomFactor: 0.9,
      preload: path.join(directory, "preload.cjs"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      devTools: !app.isPackaged || Boolean(testDirectory),
    },
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (!trusted(url)) event.preventDefault();
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on(
    "did-start-navigation",
    (_event, _url, inPlace, mainFrame) => {
      if (mainFrame && !inPlace) network.cancelAll();
    },
  );
  window.on("closed", () => {
    network.cancelAll();
    window = null;
  });
  await window.loadURL(uiURL);
  app.on("window-all-closed", () => app.quit());
}

// Let Electron finish evaluating this module before it emits ready.
app
  .whenReady()
  .then(start)
  .catch((error) => {
    dialog.showErrorBox(
      "SentinelOps 启动失败",
      error instanceof Error ? error.message : String(error),
    );
    app.quit();
  });
