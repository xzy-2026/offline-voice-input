# 离线语音输入

一个 Windows 中文语音输入小工具：按快捷键说话，识别后把文字粘贴到当前输入位置。适合口述想法、创作文字，也可以用个人词表纠正常见的人名和地名识别错误。

识别使用本机 SenseVoice 模型，不需要云端语音识别服务。**首次安装依赖、下载模型需要联网**；安装完成后的语音识别在本机进行。

## 功能

- `Ctrl + Alt + 空格` 开始录音，再按一次停止并识别。
- 悬浮窗显示录音和识别状态；按住左键拖动，单击开始或停止录音。
- 右键悬浮窗选择录音设备、切换窗口置顶或退出。
- 通知区蓝色麦克风图标：左键找回悬浮窗，右键打开菜单；也可按 `Ctrl + Alt + Home` 找回。图标可能在任务栏右侧的 `∧` 中。
- 可选个人纠错词表和口语清理。识别后默认自动粘贴，不自动发送。

## 安装

以下步骤面向 Windows，使用 PowerShell。需要麦克风、64 位 Python 3.14 和足够磁盘空间。当前开发环境为 Python 3.14；其他版本尚未验证。

1. 安装 [Python](https://www.python.org/downloads/windows/)，确认安装了 Python Launcher。下载本项目并解压，在项目文件夹中打开 PowerShell。
2. 在 PowerShell 依次运行：

   ```powershell
   py -3.14 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. 从 [sherpa-onnx 官方模型说明](https://github.com/k2-fsa/sherpa/blob/master/docs/source/onnx/sense-voice/pretrained.rst) 下载 `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2`，解压后把其中的 `model.int8.onnx` 和 `tokens.txt` 放入本项目的 `models\sense-voice\` 文件夹。模型约 228 MiB。
4. 双击项目文件夹中的 `启动.bat`，等待模型加载。将光标放进输入框，按 `Ctrl + Alt + 空格` 开始说话，再按一次结束。

如果启动后看不到窗口，可查看项目文件夹里的 `运行日志.txt`。也可以在 PowerShell 运行 `.\.venv\Scripts\python.exe voice_input.py` 查看错误。

## 个人设置

第一次使用默认配置；在右键菜单更改麦克风或置顶设置时会生成 `config.json`。也可以复制 `config.example.json` 为 `config.json` 后手动修改。`mic_device` 留空表示系统默认录音设备。`paste_mode` 改为 `clipboard` 后只复制结果，需手动按 Ctrl+V。

把 `领域词.example.txt` 复制为 `领域词.txt`，每行写一条 `错误识别=期望文字`，保存并重启程序。它是**识别后的文字替换**，不是模型训练；替换规则可能误改相同文字。`cleanup` 设为 `off` 可关闭口语清理，默认 `safe` 仍可能误删，请核对结果。

## 隐私与限制

- 程序会将识别文本写入本地 `运行日志.txt`；日志可能包含敏感内容。请不要公开上传日志。
- 自动粘贴会覆盖当前剪贴板内容，并模拟 Ctrl+V；某些应用、权限级别或输入框可能不接受粘贴。
- 使用时需要麦克风权限；热键被其他程序占用时无法注册。
- 识别不是实时逐字显示，而是结束录音后处理；默认单次最长 120 秒。
- 语音模型、依赖的安装和许可由各自项目提供；本仓库不包含模型文件。模型来自 [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) 并由 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 提供兼容格式。请查看模型包及上游项目的许可证。
- 当前版本尚未经过广泛机型、输入法和应用兼容性测试。

## 许可证

本项目代码按 [MIT 许可证](LICENSE)发布；第三方依赖与模型使用各自的许可证。
