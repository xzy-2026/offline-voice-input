"""Download the official sherpa-onnx SenseVoice archive, extracting only two files."""
import os
import shutil
import tarfile
import tempfile
import urllib.request

MODEL_URL = ('https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/'
             'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2')
FILES = ('model.int8.onnx', 'tokens.txt')


def ready(model_dir):
    return all(os.path.isfile(os.path.join(model_dir, name)) and
               os.path.getsize(os.path.join(model_dir, name)) > 0 for name in FILES)


def install(model_dir, progress=lambda done, total: None):
    parent = os.path.dirname(os.path.abspath(model_dir))
    os.makedirs(parent, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='voice-model-', dir=parent) as temp:
        archive = os.path.join(temp, 'model.tar.bz2')
        request = urllib.request.Request(MODEL_URL, headers={'User-Agent': 'offline-voice-input'})
        with urllib.request.urlopen(request, timeout=60) as response, open(archive, 'wb') as out:
            total = int(response.headers.get('Content-Length') or 0)
            done = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                progress(done, total)
            if total and done != total:
                raise IOError('下载不完整，请重试')
        with tarfile.open(archive, 'r:bz2') as tar:
            for name in FILES:
                found = [m for m in tar.getmembers() if m.isfile() and
                         m.name.replace('\\', '/').split('/')[-1] == name]
                if len(found) != 1 or found[0].size == 0:
                    raise ValueError('模型包中缺少或重复文件：' + name)
                with tar.extractfile(found[0]) as source, open(os.path.join(temp, name), 'wb') as out:
                    shutil.copyfileobj(source, out)
        os.makedirs(model_dir, exist_ok=True)
        for name in FILES:
            os.replace(os.path.join(temp, name), os.path.join(model_dir, name))


def ensure_model(model_dir):
    if ready(model_dir):
        return True
    import queue
    import threading
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    if not messagebox.askyesno('下载语音模型', '首次使用需要下载 SenseVoice 模型（约 228 MB）。\n'
                               '将从 sherpa-onnx 官方 GitHub 下载，需要联网。\n\n现在下载吗？', parent=root):
        root.destroy()
        return False
    root.deiconify()
    root.title('下载语音模型')
    root.resizable(False, False)
    label = tk.Label(root, text='正在下载，请保持窗口打开…', padx=24, pady=20)
    label.pack()
    root.protocol('WM_DELETE_WINDOW', lambda: None)
    events = queue.Queue()
    def work():
        try:
            install(model_dir, lambda a, b: events.put(('progress', (a, b))))
            events.put(('done', None))
        except Exception as e:
            events.put(('error', str(e)))
    threading.Thread(target=work, daemon=True).start()
    result = [False]
    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == 'progress':
                    done, total = value
                    label.config(text='已下载 %.1f MB%s' % (done / 1048576,
                                 (' / %.1f MB' % (total / 1048576)) if total else ''))
                elif kind == 'done':
                    result[0] = True
                    root.destroy()
                    return
                else:
                    messagebox.showerror('下载失败', '模型下载或解压失败：\n%s\n\n请检查网络后重新启动。' % value, parent=root)
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(100, poll)
    root.after(100, poll)
    root.mainloop()
    return result[0]
