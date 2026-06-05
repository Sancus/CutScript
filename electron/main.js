const { app, BrowserWindow, ipcMain, dialog, safeStorage } = require('electron');
const path = require('path');
const net = require('net');
const { PythonBackend } = require('./python-bridge');

let mainWindow = null;
let pythonBackend = null;

const isDev = !app.isPackaged;
let BACKEND_PORT = 8642;

// Ask the OS for an unused port so the backend never clashes with a stale/
// orphaned instance or another app sitting on a fixed port.
function findFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: 'CutScript',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: isDev ? false : true,
    },
    show: false,
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'frontend', 'dist', 'index.html'));
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  // In production, bind the backend to an ephemeral free port to avoid clashes
  // with stale/orphaned backends or other apps. Dev keeps 8642 to match the
  // separately-run `npm run dev:backend`.
  if (!isDev) {
    try {
      BACKEND_PORT = await findFreePort();
    } catch (err) {
      console.error('[backend] Could not find a free port, using default:', err.message);
    }
  }

  // Show the UI immediately; the backend imports a heavy ML stack and can take
  // tens of seconds to become ready, so we must not block window creation on it.
  createWindow();

  pythonBackend = new PythonBackend(BACKEND_PORT, isDev);
  pythonBackend.start().catch((err) => {
    console.error('[backend] Failed to start:', err);
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  if (pythonBackend) {
    pythonBackend.stop();
  }
});

// IPC Handlers

ipcMain.handle('dialog:openFile', async (_event, options) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: [
      { name: 'Video Files', extensions: ['mp4', 'avi', 'mov', 'mkv', 'webm'] },
      { name: 'Audio Files', extensions: ['m4a', 'wav', 'mp3', 'flac'] },
      { name: 'All Files', extensions: ['*'] },
    ],
    ...options,
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('dialog:saveFile', async (_event, options) => {
  const result = await dialog.showSaveDialog(mainWindow, {
    filters: [
      { name: 'Video Files', extensions: ['mp4', 'mov', 'webm'] },
      { name: 'Project Files', extensions: ['aive'] },
    ],
    ...options,
  });
  return result.canceled ? null : result.filePath;
});

ipcMain.handle('dialog:openProject', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: [
      { name: 'AI Video Editor Project', extensions: ['aive'] },
    ],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('safe-storage:encrypt', (_event, data) => {
  if (safeStorage.isEncryptionAvailable()) {
    return safeStorage.encryptString(data).toString('base64');
  }
  return data;
});

ipcMain.handle('safe-storage:decrypt', (_event, encrypted) => {
  if (safeStorage.isEncryptionAvailable()) {
    return safeStorage.decryptString(Buffer.from(encrypted, 'base64'));
  }
  return encrypted;
});

ipcMain.handle('get-backend-url', () => {
  // Use 127.0.0.1 (not "localhost"): on Windows "localhost" can resolve to IPv6
  // ::1 first, but uvicorn binds IPv4 127.0.0.1, causing ERR_CONNECTION_REFUSED.
  return `http://127.0.0.1:${BACKEND_PORT}`;
});

ipcMain.handle('fs:readFile', async (_event, filePath) => {
  const fs = require('fs');
  return fs.readFileSync(filePath, 'utf-8');
});

ipcMain.handle('fs:writeFile', async (_event, filePath, content) => {
  const fs = require('fs');
  fs.writeFileSync(filePath, content, 'utf-8');
  return true;
});
