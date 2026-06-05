const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

class PythonBackend {
  constructor(port, isDev) {
    this.port = port;
    this.isDev = isDev;
    this.process = null;
  }

  async start() {
    // In dev mode, check if a backend is already running (e.g. from `npm run dev:backend`)
    // If so, reuse it instead of spawning a duplicate.
    if (this.isDev) {
      const alreadyRunning = await this._isPortOpen(2000);
      if (alreadyRunning) {
        console.log(`[backend] Dev backend already running on port ${this.port} — reusing it.`);
        return;
      }
    }

    const { command, args, cwd } = this._resolveLaunch();

    // A bundled FFmpeg lives alongside the packaged backend so the app is
    // fully self-contained. Make sure both the Electron-spawned process and
    // any library that shells out to ffmpeg can find it.
    const env = { ...process.env, PYTHONUNBUFFERED: '1' };
    if (!this.isDev) {
      const ffmpegDir = path.join(process.resourcesPath, 'ffmpeg');
      if (fs.existsSync(ffmpegDir)) {
        env.PATH = `${ffmpegDir}${path.delimiter}${env.PATH || ''}`;
      }
    }

    console.log(`[backend] Launching: ${command} ${args.join(' ')}`);

    // Persist backend output to a log file so packaged-app startup failures are
    // diagnosable (the bundled .exe has no visible console).
    const logStream = this._openLogStream();

    this.process = spawn(command, args, {
      cwd,
      stdio: ['pipe', 'pipe', 'pipe'],
      env,
      // Prevent a console window from flashing for the bundled .exe on Windows.
      windowsHide: true,
    });

    this.process.stdout.on('data', (data) => {
      const text = data.toString();
      console.log(`[backend] ${text.trim()}`);
      if (logStream) logStream.write(text);
    });

    this.process.stderr.on('data', (data) => {
      const text = data.toString();
      console.error(`[backend] ${text.trim()}`);
      if (logStream) logStream.write(text);
    });

    this.process.on('error', (err) => {
      console.error('[backend] Failed to start Python backend:', err.message);
    });

    this.process.on('exit', (code) => {
      console.log(`[backend] Process exited with code ${code}`);
      this.process = null;
    });

    // The packaged backend imports torch/whisperx/pyannote at startup, which can
    // take a while on first launch (DLL loading + onedir decompression). Give it
    // a generous window, and don't hard-fail the app if it's still warming up —
    // the renderer polls the backend URL on its own.
    const readyTimeout = this.isDev ? 30000 : 180000;
    try {
      await this._waitForReady(readyTimeout);
      console.log(`[backend] Ready on port ${this.port}`);
    } catch (err) {
      console.error(`[backend] Not ready after ${readyTimeout}ms: ${err.message}`);
    }
  }

  _openLogStream() {
    try {
      const { app } = require('electron');
      const logPath = path.join(app.getPath('userData'), 'backend.log');
      const stream = fs.createWriteStream(logPath, { flags: 'a' });
      stream.write(`\n=== backend started ${new Date().toISOString()} ===\n`);
      console.log(`[backend] Logging to ${logPath}`);
      return stream;
    } catch (err) {
      console.error('[backend] Could not open log file:', err.message);
      return null;
    }
  }

  _resolveLaunch() {
    if (this.isDev) {
      // Dev: run the FastAPI app via the system Python interpreter.
      const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
      return {
        command: pythonCmd,
        args: [
          '-m', 'uvicorn', 'main:app',
          '--host', '127.0.0.1',
          '--port', String(this.port),
        ],
        cwd: path.join(__dirname, '..', 'backend'),
      };
    }

    // Production: run the self-contained backend bundled by PyInstaller.
    const exeName = process.platform === 'win32'
      ? 'cutscript-backend.exe'
      : 'cutscript-backend';
    const backendDir = path.join(process.resourcesPath, 'backend-dist');
    return {
      command: path.join(backendDir, exeName),
      args: ['--host', '127.0.0.1', '--port', String(this.port)],
      cwd: backendDir,
    };
  }

  _isPortOpen(timeoutMs) {
    return new Promise((resolve) => {
      const req = http.get(`http://127.0.0.1:${this.port}/health`, (res) => {
        resolve(res.statusCode === 200);
      });
      req.on('error', () => resolve(false));
      req.setTimeout(timeoutMs, () => { req.destroy(); resolve(false); });
      req.end();
    });
  }

  stop() {
    if (this.process) {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/pid', String(this.process.pid), '/f', '/t']);
      } else {
        this.process.kill('SIGTERM');
      }
      this.process = null;
    }
  }

  _waitForReady(timeoutMs) {
    const startTime = Date.now();
    return new Promise((resolve, reject) => {
      const check = () => {
        if (Date.now() - startTime > timeoutMs) {
          reject(new Error('Backend startup timed out'));
          return;
        }
        const req = http.get(`http://127.0.0.1:${this.port}/health`, (res) => {
          if (res.statusCode === 200) {
            resolve();
          } else {
            setTimeout(check, 500);
          }
        });
        req.on('error', () => setTimeout(check, 500));
        req.end();
      };
      setTimeout(check, 1000);
    });
  }
}

module.exports = { PythonBackend };
