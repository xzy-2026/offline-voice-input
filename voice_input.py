# -*- coding: utf-8 -*-
"""
语音转文字（本机离线 · 全局可用）
打开后常驻右下角，在**任何程序**里按 Ctrl+Alt+空格 说话，文字就粘到当前光标处。

热键：Ctrl+Alt+空格 —— 按一下开始录音，再按一下结束并识别。
语料不出本机，模型在 models/sense-voice/。
"""
import os
import sys
import json
import time
import queue
import threading
import subprocess
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
LOG_PATH = os.path.join(ROOT, "运行日志.txt")
RAW_PATH = os.path.join(ROOT, "临时录音.raw")
TERMS_PATH = os.path.join(ROOT, "领域词.txt")
TRAY_ICON_PATH = os.path.join(ROOT, "语音图标.ico")
tray_hwnd = None

# 口语清理用到的语气词（单独出现就删掉）
FILLER_WORDS = ["呃", "嗯", "哦", "噢", "唉", "哎", "唔"]
# 整句/整段只有这些词时，直接丢掉
LEAD_FILLERS = ["那个", "这个", "那么", "然后", "就是", "还有"]
# 只在这些词于分句首/尾出现时才删（句中保留，避免改坏语义）
TAIL_FILLERS = ["啊", "那个", "这个", "然后", "就是"]
# 一整句只是这些虚话时，整句删掉
DROP_CLAUSES = {"我想想", "我想一想", "我想一下", "让我想想", "怎么说呢",
                "你知道吗", "对不对", "是不是", "那个", "这个", "然后", "就是", "那"}


def load_terms():
    """读 领域词.txt：一行一条，写成 错词=正确 或 错词=正确=正确2"""
    pairs = []
    if not os.path.exists(TERMS_PATH):
        return pairs
    try:
        with open(TERMS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                parts = [p.strip() for p in line.split("=")]
                right = parts[-1]
                for bad in parts[:-1]:
                    if bad and right and bad != right:
                        pairs.append((bad, right))
    except Exception:
        log("领域词.txt 读不了：\n" + traceback.format_exc())
    # 长词先换，避免短词把长词切坏
    pairs.sort(key=lambda p: -len(p[0]))
    return pairs


def fix_terms(text, pairs):
    out = text
    for bad, good in pairs:
        if bad in out:
            out = out.replace(bad, good)
    return out


def _strip_fillers(seg):
    """去掉分句首尾的口头禅；整句都是虚话时返回空串"""
    import re
    s = seg.strip()
    if not s:
        return ""
    changed = True
    while changed and s:
        changed = False
        for w in LEAD_FILLERS:
            if s.startswith(w):
                s = s[len(w):]
                changed = True
        for w in TAIL_FILLERS:
            if s.endswith(w) and len(s) > len(w):
                s = s[:-len(w)]
                changed = True
        s = s.strip()
    s = re.sub(r"^[，、；：\s]+|[，、；：\s]+$", "", s)
    if s in DROP_CLAUSES:
        return ""
    return s


def clean_text(text, level="safe"):
    """口语清理。level: off / safe / strong"""
    import re
    if level == "off" or not text:
        return text
    t = text

    # 1) 语气词连同后面的逗号一起删
    for w in FILLER_WORDS:
        t = re.sub(w + r"[，,、]?", "", t)

    # 2) 重复字（我我我）、重复词（就是说就是说）
    t = re.sub(r"([\u4e00-\u9fa5])\1{2,}", r"\1", t)
    t = re.sub(r"([\u4e00-\u9fa5]{2,3})(\1)+", r"\1", t)

    # 3) 按标点切成分句，逐句去掉首尾口头禅；纯虚话的分句整句删掉
    parts = re.split(r"([，。！？；])", t)
    out = []
    buf = ""
    for i, p in enumerate(parts):
        if i % 2 == 1:
            seg = _strip_fillers(buf)
            if seg:
                out.append(seg + p)
            buf = ""
        else:
            buf = p
    tail = _strip_fillers(buf)
    if tail:
        out.append(tail)
    t = "".join(out)

    # 4) 标点收尾整理
    t = re.sub(r"[，、]{2,}", "，", t)
    t = re.sub(r"[，、]+([。！？；])", r"\1", t)
    t = re.sub(r"^[，、。；：\s]+", "", t)
    t = t.strip()

    return t or text.strip()

IDLE = "idle"
REC = "recording"
WORK = "working"

LABEL = {
    IDLE: "语音待命  Ctrl+Alt+空格",
    REC: "● 录音中，再按一次结束",
    WORK: "识别中，请稍等…",
}


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 2 * 1024 * 1024:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_wav_16k(path):
    """读 16 位 wav，转成 16k 单声道 float32"""
    import wave
    import numpy as np
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError("只支持 16 位 wav")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        n = int(len(data) * 16000 / sr)
        idx = np.linspace(0, len(data) - 1, n)
        data = np.interp(idx, np.arange(len(data)), data).astype(np.float32)
    return data


def load_config():
    path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else os.path.join(ROOT, "config.example.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_input_devices():
    """列出所有能录音的设备名（同名只留一个）"""
    import sounddevice as sd
    out, seen = [], set()
    for d in sd.query_devices():
        try:
            if int(d.get("max_input_channels", 0)) <= 0:
                continue
        except Exception:
            continue
        name = d.get("name", "")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def find_input_device(name):
    """按配置的名字找录音设备，找不到就返回 None（用系统默认）"""
    if not name:
        return None
    import sounddevice as sd
    devices = sd.query_devices()
    for idx, d in enumerate(devices):
        if int(d.get("max_input_channels", 0)) > 0 and d.get("name") == name:
            return idx
    for idx, d in enumerate(devices):
        if int(d.get("max_input_channels", 0)) > 0 and name in d.get("name", ""):
            return idx
    return None


class Engine:
    """录音 + 识别，纯后台逻辑，不碰界面"""

    def __init__(self, cfg, events):
        self.cfg = cfg
        self.events = events          # queue: (kind, payload)
        self.state = IDLE
        self.stream = None
        self.chunks = []
        self.sr = 48000
        self.rec_start = 0.0
        self.lock = threading.Lock()
        self.recognizer = None
        self.terms = load_terms()
        self.cleanup = (cfg.get("cleanup") or "safe").lower()
        self.use_terms = bool(cfg.get("fix_terms", True))
        log("领域词 %d 条，口语清理=%s" % (len(self.terms), self.cleanup))

    # ---- 识别引擎 ----
    def load_recognizer(self):
        import sherpa_onnx
        m = self.cfg["model_dir"]
        if not os.path.isabs(m):
            m = os.path.join(ROOT, m)
        t0 = time.time()
        log("加载识别模型：%s" % m)
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=os.path.join(m, "model.int8.onnx"),
            tokens=os.path.join(m, "tokens.txt"),
            num_threads=int(self.cfg.get("num_threads", 4)),
            use_itn=bool(self.cfg.get("use_itn", True)),
            language=self.cfg.get("language", "zh"),
            debug=False,
        )
        log("模型加载完成，用时 %.1f 秒" % (time.time() - t0))

    def transcribe(self, samples):
        stream = self.recognizer.create_stream()
        stream.accept_waveform(16000, samples)
        self.recognizer.decode_stream(stream)
        return (stream.result.text or "").strip()

    # ---- 录音 ----
    def start(self):
        import sounddevice as sd
        name = self.cfg.get("mic_device", "")
        dev = find_input_device(name)
        try:
            info = sd.query_devices(dev, "input") if dev is not None else sd.query_devices(kind="input")
        except Exception as e:
            log("查询录音设备失败：%r" % e)
            self.events.put(("error", "找不到麦克风，请运行 选择麦克风.bat"))
            return False
        self.sr = int(float(info.get("default_samplerate") or 48000))
        self.chunks = []

        def cb(indata, frames, t, status):
            self.chunks.append(indata.copy())

        try:
            self.stream = sd.InputStream(
                device=dev, samplerate=self.sr, channels=1,
                dtype="float32", callback=cb, blocksize=0,
            )
            self.stream.start()
        except Exception as e:
            log("打开麦克风失败：%r" % e)
            self.stream = None
            self.events.put(("error", "麦克风打不开，请运行 选择麦克风.bat"))
            return False
        self.rec_start = time.time()
        try:
            import winsound
            winsound.Beep(880, 100)
        except Exception:
            pass
        log("开始录音（%s，%d Hz）" % (info.get("name", "默认设备"), self.sr))
        self.state = REC
        self.events.put(("state", REC))
        t = threading.Timer(float(self.cfg.get("max_seconds", 120)), self._force_stop)
        t.daemon = True
        t.start()
        return True

    def _force_stop(self):
        if self.state == REC:
            log("到达最长录音时间，自动结束")
            self.finish()

    def stop_capture(self):
        if not self.stream:
            return None
        dur = time.time() - self.rec_start
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            log("关闭麦克风出错：\n" + traceback.format_exc())
        self.stream = None
        log("停止录音，时长 %.1f 秒" % dur)
        try:
            import winsound
            winsound.Beep(600, 80)
        except Exception:
            pass
        if dur < float(self.cfg.get("min_seconds", 0.4)):
            log("录音太短，忽略")
            return None
        if not self.chunks:
            log("没有录到音频数据")
            return None
        import numpy as np
        data = np.concatenate(self.chunks, axis=0).reshape(-1)
        if self.sr != 16000:
            n = int(len(data) * 16000 / self.sr)
            idx = np.linspace(0, len(data) - 1, n)
            data = np.interp(idx, np.arange(len(data)), data).astype(np.float32)
        if len(data) < 3200:
            log("音频数据太少，忽略")
            return None
        return data

    def finish(self):
        with self.lock:
            if self.state != REC:
                return
            self.state = WORK
        self.events.put(("state", WORK))
        samples = self.stop_capture()
        if samples is None:
            self.state = IDLE
            self.events.put(("state", IDLE))
            return
        threading.Thread(target=self._run, args=(samples,), daemon=True).start()

    def _run(self, samples):
        try:
            t0 = time.time()
            text = self.transcribe(samples)
            cost = time.time() - t0
            if not text:
                log("识别结果为空（用时 %.1f 秒）" % cost)
                self.events.put(("error", "没听清，再说一次"))
                return
            log("识别完成（%.1f 秒）：%s" % (cost, text))
            raw = text
            if self.use_terms and self.terms:
                text = fix_terms(text, self.terms)
            text = clean_text(text, self.cleanup)
            if not text:
                text = raw
            if text != raw:
                log("纠正后：%s" % text)
            if self.cfg.get("paste", True):
                time.sleep(0.12)
                if self.cfg.get("paste_mode", "auto") == "auto":
                    self.paste_text(text)
                else:
                    self.copy_text(text)
            self.events.put(("text", text))
            try:
                import winsound
                winsound.Beep(1200, 70)
            except Exception:
                pass
        except Exception:
            log("识别出错：\n" + traceback.format_exc())
            self.events.put(("error", "识别出错，看运行日志.txt"))
        finally:
            self.state = IDLE
            self.events.put(("state", IDLE))

    # ---- 粘贴 ----
    @staticmethod
    def copy_text(text):
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()

    @classmethod
    def paste_text(cls, text):
        import win32con
        import win32api
        cls.copy_text(text)
        for vk in (win32con.VK_CONTROL, win32con.VK_MENU, win32con.VK_SHIFT,
                   win32con.VK_LWIN, win32con.VK_RWIN):
            if win32api.GetAsyncKeyState(vk) & 0x8000:
                win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.12)
        # 先空敲一下 Shift 预热键盘队列，否则程序启动后的第一次 Ctrl+V 会被吞掉
        win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
        win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.15)
        # 按 Ctrl，并确认它真的被系统接收（注入按键偶尔会被吞）
        for _ in range(10):
            win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
            time.sleep(0.04)
            if win32api.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000:
                break
        time.sleep(0.10)
        win32api.keybd_event(ord("V"), 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.10)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

    def toggle(self):
        if self.state == WORK:
            return
        if self.state == IDLE:
            self.start()
        elif self.state == REC:
            self.finish()


def hotkey_thread(engine, events, cfg):
    import win32api
    import win32con
    import win32gui
    global tray_hwnd

    hammer = win32con.MOD_CONTROL | win32con.MOD_ALT
    vk = win32con.VK_SPACE
    last = [0.0]
    tray_message = win32con.WM_USER + 20
    taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
    tray_data = [None]

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == tray_message:
            if lparam == win32con.WM_LBUTTONUP:
                events.put(("show-window", None))
            elif lparam == win32con.WM_RBUTTONUP:
                events.put(("tray-menu", win32gui.GetCursorPos()))
            return 0
        if msg == taskbar_created and tray_data[0]:
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, tray_data[0])
            return 0
        if msg == win32con.WM_HOTKEY and wparam == 1:
            now = time.time()
            if now - last[0] > 0.8:
                last[0] = now
                try:
                    engine.toggle()
                except Exception:
                    log("热键处理出错：\n" + traceback.format_exc())
            return 0
        if msg == win32con.WM_HOTKEY and wparam == 2:
            events.put(("show-window", None))
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    wc = win32gui.WNDCLASS()
    wc.lpszClassName = "XiaoShuoVoiceInputWnd"
    wc.hInstance = win32api.GetModuleHandle(None)
    wc.lpfnWndProc = wnd_proc
    cls = win32gui.RegisterClass(wc)
    hwnd = win32gui.CreateWindow(cls, "voice", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None)
    try:
        icon = win32gui.LoadImage(0, TRAY_ICON_PATH, win32con.IMAGE_ICON, 0, 0,
                                  win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        tray_data[0] = (hwnd, 1, flags, tray_message, icon, "语音转文字：左键显示，右键设置")
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, tray_data[0])
        tray_hwnd = hwnd
        log("通知区图标已添加")
    except Exception:
        log("通知区图标添加失败：" + traceback.format_exc())
        events.put(("error", "通知区图标添加失败，请查看运行日志"))
    try:
        win32gui.RegisterHotKey(hwnd, 1, hammer, vk)
        log("热键已注册：Ctrl+Alt+空格")
        events.put(("hotkey-ok", None))
    except Exception as e:
        log("热键注册失败（可能被别的程序占用）：%r" % e)
        events.put(("error", "热键被占用，Ctrl+Alt+空格 注册不上"))
    try:
        win32gui.RegisterHotKey(hwnd, 2, hammer, win32con.VK_HOME)
        log("找回窗口热键已注册：Ctrl+Alt+Home")
    except Exception as e:
        log("找回窗口热键注册失败：%r" % e)
    while True:
        win32gui.PumpWaitingMessages()
        time.sleep(0.02)


class Window:
    def __init__(self, engine, events, cfg):
        import tkinter as tk
        from tkinter import font as tkfont
        self.tk = tk
        self.engine = engine
        self.events = events
        self.cfg = cfg
        self.root = tk.Tk()
        self.root.title("语音转文字")
        self.root.overrideredirect(True)
        self.topmost = bool(cfg.get("window_topmost", True))
        self.root.attributes("-topmost", self.topmost)

        bg = "#2b2b2b"
        fg = "#e8e8e8"
        self.bg_idle, self.bg_rec, self.bg_work = bg, "#8c2222", "#1f4f7a"
        self.label = tk.Label(
            self.root, text=LABEL[IDLE], bg=bg, fg=fg,
            font=("Microsoft YaHei UI", 11), padx=14, pady=7,
            justify="left", anchor="w", width=22,
        )
        self.label.pack()

        self.state = IDLE
        self.text_used = ""

        self.root.bind("<ButtonPress-1>", self._drag_start)
        self.root.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<ButtonRelease-1>", self._drag_end)
        self.root.bind("<Button-3>", self._menu)

        menu = tk.Menu(self.root, tearoff=0, font=("Microsoft YaHei UI", 9))
        menu.add_command(label="开始/结束录音", command=self.engine.toggle)
        self.device_menu = tk.Menu(menu, tearoff=0, font=("Microsoft YaHei UI", 9))
        menu.add_cascade(label="选择录音设备", menu=self.device_menu)
        self.topmost_var = tk.BooleanVar(value=self.topmost)
        menu.add_checkbutton(label="窗口置顶", variable=self.topmost_var, command=self._set_topmost)
        menu.add_command(label="拖动窗口：按住左键移动", state="disabled")
        menu.add_separator()
        menu.add_command(label="退出", command=self.quit)
        self.menu = menu

        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x, y = sw - w - 24, sh - h - 80
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))

        # 不抢焦点、不进 Alt+Tab
        try:
            import win32gui
            import win32con
            hwnd = int(self.root.frame(), 16)
            ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(
                hwnd, win32con.GWL_EXSTYLE,
                ex | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE,
            )
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST if self.topmost else win32con.HWND_NOTOPMOST, x, y, w, h,
                win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
            )
        except Exception:
            log("窗口置顶设置失败：" + traceback.format_exc())

        self.root.after(100, self._poll)

    def _save_setting(self, key, value):
        # Re-read first so changes made by the microphone selection tool survive.
        cfg = load_config()
        cfg[key] = value
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        self.cfg[key] = value

    def _select_device(self, name):
        from tkinter import messagebox
        if self.engine.state != IDLE:
            messagebox.showinfo("语音转文字", "请先结束录音并等待识别完成，再切换录音设备。")
            return
        try:
            self._save_setting("mic_device", name)
        except Exception:
            log("保存录音设备失败：" + traceback.format_exc())
            messagebox.showerror("语音转文字", "无法保存录音设备，请查看运行日志。")

    def _set_topmost(self):
        from tkinter import messagebox
        value = self.topmost_var.get()
        try:
            self._save_setting("window_topmost", value)
            self.root.attributes("-topmost", value)
        except Exception:
            self.topmost_var.set(not value)
            log("保存置顶设置失败：" + traceback.format_exc())
            messagebox.showerror("语音转文字", "无法保存置顶设置，请查看运行日志。")

    def _drag_start(self, event):
        self._drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())
        self._dragged = False

    def _drag_move(self, event):
        sx, sy, x, y = self._drag_origin
        dx, dy = event.x_root - sx, event.y_root - sy
        if abs(dx) >= 4 or abs(dy) >= 4:
            self._dragged = True
        if self._dragged:
            self.root.geometry("%+d%+d" % (x + dx, y + dy))

    def _drag_end(self, event):
        if not self._dragged:
            self.engine.toggle()

    def _menu(self, ev):
        self.device_menu.delete(0, "end")
        current = self.cfg.get("mic_device", "")
        self.device_menu.add_command(label=("✓ " if not current else "") + "系统默认设备",
                                     command=lambda: self._select_device(""))
        try:
            devices = list_input_devices()
            for name in devices:
                self.device_menu.add_command(label=("✓ " if name == current else "") + name,
                                             command=lambda n=name: self._select_device(n))
            if not devices:
                self.device_menu.add_command(label="未检测到录音设备", state="disabled")
            elif current and current not in devices:
                self.device_menu.add_command(label="当前设备不可用：" + current, state="disabled")
        except Exception:
            log("枚举录音设备失败：" + traceback.format_exc())
            self.device_menu.add_command(label="设备列表读取失败，请查看运行日志", state="disabled")
        try:
            self.menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            self.menu.grab_release()

    def show_window(self):
        self.root.deiconify()
        self.root.update_idletasks()
        try:
            import win32gui
            import win32con
            hwnd = int(self.root.frame(), 16)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST if self.topmost_var.get() else win32con.HWND_TOP,
                0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
            )
        except Exception:
            log("找回窗口失败：" + traceback.format_exc())

    def set_state(self, s):
        self.state = s
        text = LABEL[s]
        color = {IDLE: self.bg_idle, REC: self.bg_rec, WORK: self.bg_work}[s]
        self.label.configure(text=text, bg=color)
        self.root.configure(bg=color)

    def _poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "state":
                    self.set_state(payload)
                elif kind == "text":
                    self.text_used = payload
                    short = payload if len(payload) <= 12 else payload[:12] + "…"
                    prefix = "已粘贴：" if self.cfg.get("paste_mode", "auto") == "auto" else "已复制，按 Ctrl+V："
                    self.label.configure(text=prefix + short, bg="#1f6f3f")
                    self.root.configure(bg="#1f6f3f")
                    self.root.after(1600, lambda: self.set_state(self.engine.state))
                elif kind == "error":
                    self.label.configure(text="提示：" + payload, bg="#7a4a12")
                    self.root.configure(bg="#7a4a12")
                    self.root.after(2600, lambda: self.set_state(self.engine.state))
                elif kind == "show-window":
                    self.show_window()
                elif kind == "tray-menu":
                    from types import SimpleNamespace
                    self._menu(SimpleNamespace(x_root=payload[0], y_root=payload[1]))
        except queue.Empty:
            pass
        if self.state == REC:
            dur = time.time() - self.engine.rec_start
            self.label.configure(text="● 录音中 %.0f 秒，再按一次结束" % dur)
        self.root.after(100, self._poll)

    def quit(self):
        try:
            if tray_hwnd:
                import win32gui
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (tray_hwnd, 1))
            self.root.destroy()
        finally:
            os._exit(0)

    def run(self):
        self.root.mainloop()


def main():
    # 让窗口按真实像素定位（高分屏下不偏移、不模糊）
    try:
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    cfg = load_config()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode == "--list-devices":
        for d in list_input_devices():
            print(d)
        return 0

    if mode == "--transcribe":
        eng = Engine(cfg, queue.Queue())
        eng.load_recognizer()
        samples = read_wav_16k(sys.argv[2])
        print(eng.transcribe(samples))
        return 0

    import win32event
    import win32api
    mutex = win32event.CreateMutex(None, True, "Global\\xiaoshuo_voice_input_v2")
    if win32api.GetLastError() == 183:
        try:
            event = win32event.OpenEvent(win32event.EVENT_MODIFY_STATE, False, "Local\\xiaoshuo_voice_show_v1")
            win32event.SetEvent(event)
            win32api.CloseHandle(event)
        except Exception:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("语音转文字", "旧版本仍在运行。请先退出旧版本，再重新打开。")
        return 0

    events = queue.Queue()
    show_event = win32event.CreateEvent(None, False, False, "Local\\xiaoshuo_voice_show_v1")
    def wait_for_show():
        while True:
            win32event.WaitForSingleObject(show_event, win32event.INFINITE)
            events.put(("show-window", None))
    threading.Thread(target=wait_for_show, daemon=True).start()
    engine = Engine(cfg, events)
    try:
        engine.load_recognizer()
    except Exception:
        log("模型加载失败：\n" + traceback.format_exc())
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("语音转文字", "模型加载失败，看 运行日志.txt")
        return 1

    threading.Thread(target=hotkey_thread, args=(engine, events, cfg), daemon=True).start()
    Window(engine, events, cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
