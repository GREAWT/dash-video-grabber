# -*- coding: utf-8 -*-
"""
DASH 流媒体下载引擎 (基于 yt-dlp)
==================================
支持:
  - 网页视频链接   (Bilibili / YouTube / 抖音 / TikTok / ... 共 2000+ 站点)
  - 直接流地址     (DASH 的 .mpd / HLS 的 .m3u8, 通用兜底)

DASH 特点: 视频轨与音频轨分离传输, 本引擎自动:
  1) 挑选最高画质的视频轨 (如 1080p/4K, 不含声音)
  2) 挑选最高质量的音频轨
  3) 用 ffmpeg 无损合并为单个 mp4

命令行用法:
  python dash_dl.py "视频URL"                      # 交互式选择画质下载
  python dash_dl.py "视频URL" --list               # 仅列出全部可用画质/格式
  python dash_dl.py "视频URL" --best               # 直接下载最佳画质(自动合并)
  python dash_dl.py "视频URL" -f 137+140           # 按 yt-dlp 格式ID 下载
  python dash_dl.py "视频URL" --audio-mp3          # 仅提取音频为 MP3(320k)
  python dash_dl.py "视频URL" --cookies edge       # 借用浏览器登录态(需先关闭该浏览器)
  python dash_dl.py "视频URL" --cookies-file c.txt # 使用导出的 Cookie 文件(无需关浏览器)
  python dash_dl.py "视频URL" -o "D:/素材"         # 指定保存目录
  python dash_dl.py "视频URL" --playlist           # 下载整个播放列表
  python dash_dl.py "视频URL" --proxy http://127.0.0.1:7890   # 使用代理(供自己网络环境)
  python dash_dl.py "视频URL" --sleep 1            # 请求间隔(降低触发风控概率)
  python dash_dl.py "视频URL" --limit-rate 2M      # 限速
  python dash_dl.py --clean-file "视频.mp4" --clean-preset top-right
  python dash_dl.py --clean-file "视频.mp4" --clean-rect 1666,39,205,91 --clean-method box
  python dash_dl.py --clean-file "目录" --preview-clean      # 只生成清理前后对比图
  python dash_dl.py --version                      # 显示版本
"""

import argparse
import os
import re
import sys
import time

__version__ = "1.1.0"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _console_utf8():
    """保证控制台输出安全: 重定向时用 UTF-8, 任何字符都不会因编码崩溃."""
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
        if not sys.stdout.isatty():
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if not sys.stderr.isatty():
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def default_outdir():
    return os.path.join(SCRIPT_DIR, "downloads")


def _ffmpeg_location():
    """自动定位 ffmpeg 的可执行文件路径.

    yt-dlp 的 ffmpeg_location 需要能解析出名为 ffmpeg 的可执行文件:
    - 系统 PATH 里有 ffmpeg -> 返回其完整路径
    - 否则回退到 imageio-ffmpeg 内置的二进制(文件名形如 ffmpeg-win-...-v7.1.exe,
      必须传完整文件路径, 传目录不会命中)
    """
    import shutil
    try:
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
    except Exception:
        pass
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _cookie_browsers():
    return ["edge", "chrome", "chromium", "firefox", "brave", "opera", "vivaldi", "safari"]


def _ensure_windows_env():
    """补全 Windows 环境变量.

    某些方式启动的进程(如部分自动化环境)缺少 LOCALAPPDATA/APPDATA,
    导致 yt-dlp 展开 %LOCALAPPDATA% 失败、找不到浏览器 Cookie 目录。
    这里从 USERPROFILE 推导补齐, 保证"借用浏览器登录态"始终可用。
    """
    if os.name != "nt":
        return
    up = os.environ.get("USERPROFILE") or ""
    if not up:
        try:
            up = os.path.expanduser("~")
        except Exception:
            up = ""
    if up:
        if not os.environ.get("LOCALAPPDATA"):
            os.environ["LOCALAPPDATA"] = os.path.join(up, "AppData", "Local")
        if not os.environ.get("APPDATA"):
            os.environ["APPDATA"] = os.path.join(up, "AppData", "Roaming")


def _base_opts(cookies_browser=None, quiet=True):
    opts = {
        "quiet": quiet,
        "no_warnings": quiet,
        "noplaylist": True,
        "retries": 2,
        "fragment_retries": 3,
        "socket_timeout": 15,
        "noprogress": True,
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def has_video(f):
    v = f.get("vcodec")
    return bool(v) and v != "none"


def has_audio(f):
    a = f.get("acodec")
    return bool(a) and a != "none"


def describe(f):
    """把一个格式字典变成一行人类可读的文字."""
    fid = f.get("format_id", "?")
    height = f.get("height")
    fps = f.get("fps")
    vcodec = (f.get("vcodec") or "无").split(".")[0]
    if vcodec in ("none", "无"):
        vcodec = "无"
    acodec = (f.get("acodec") or "无").split(".")[0]
    if acodec in ("none", "无"):
        acodec = "无"
    ext = f.get("ext", "?")
    note = (f.get("format_note") or "").strip()
    if has_video(f) and has_audio(f):
        kind = "完整"
    elif has_video(f):
        kind = "纯视频"
    elif has_audio(f):
        kind = "纯音频"
    else:
        kind = "?"
    res = "%-8s %-4s" % (fid, ext)
    if height:
        res += " %sp" % height
        if fps:
            res += "(%sfps)" % fps
    else:
        res += "  %s" % kind
    res += " 视频:%s 音频:%s" % (vcodec, acodec)
    dr = (f.get("dynamic_range") or "").strip()
    if dr and dr.upper() != "SDR":
        res += " [%s]" % dr   # 例如 HDR10, 提醒用户这是 HDR 流
    fs = f.get("filesize") or f.get("filesize_approx") or 0
    tbr = f.get("tbr") or 0
    if fs:
        res += " %.0fMB" % (fs / 1048576.0)
    elif tbr:
        res += " ~%dkbps" % tbr
    else:
        res += " --"
    if note:
        res += " " + note
    return res


def extract_url(text):
    """从可能混入标题文字的输入里提取真正的视频链接.

    B站等平台的"复制链接"常会附带视频标题, 例如:
        https://【示例标题】 https://www.bilibili.com/video/BV1xxxxxxxxx/?...
    这里自动取出其中真正合法的 URL; 输入本身干净时原样返回.
    返回 (clean_url, 是否发生过清理)
    """
    import re as _re
    text = (text or "").strip()
    if _re.match(r"^https?://[^\s]+$", text):
        return text, False
    cands = _re.findall(r"https?://[^\s]+", text)
    # 去掉候选里混入的中文/全角字符及其后内容, 并去尾部分隔符
    cands = [_re.sub(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u3008-\u301f「」『』【】\u201c\u201d].*$", "", c)
             .rstrip(".,;:!?，。；：！？)]}>」』】") for c in cands]
    # 只保留纯 ASCII URL
    cands = [c for c in cands
             if _re.match(r"^https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%\-]+$", c)]
    if not cands:
        return text, False
    cleaned = cands[-1]  # 分享文本的链接通常在末尾
    return cleaned, (cleaned != text)


_BROWSER_PROCESS = {
    "edge": "msedge.exe", "chrome": "chrome.exe", "chromium": "chrome.exe",
    "brave": "brave.exe", "opera": "opera.exe", "vivaldi": "vivaldi.exe",
    "firefox": "firefox.exe",
}


def _browser_running(browser):
    """检测目标浏览器进程是否正在运行(仅用于最终错误提示, 不做硬性拦截)."""
    if os.name != "nt":
        return False
    exe = _BROWSER_PROCESS.get(browser)
    if not exe:
        return False
    try:
        import subprocess
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq %s" % exe],
            capture_output=True, text=True, timeout=15,
        ).stdout
        return exe.lower() in out.lower()
    except Exception:
        return False


_COOKIE_RETRY_KEYS = ("could not copy", "cookie database", "permission")


def _with_cookie_retry(fn, cookies_browser, log=None, tries=3, delay=4.0):
    """借用浏览器 Cookie 时, 浏览器可能瞬时占用数据库文件
    (冷启动/刚写入/被安全软件扫描), yt-dlp 遇 PermissionError 会直接报错退出;
    这里在出错后自动等待并重试, 把"第一次报错"自动消化掉."""
    if not cookies_browser:
        return fn()
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            msg = str(e).lower()
            if not any(k in msg for k in _COOKIE_RETRY_KEYS):
                raise   # 与 Cookie 占用无关的错误, 直接抛出
            if attempt < tries:
                if log:
                    log("Cookie 数据库被浏览器占用(冷启动/正在写入?), "
                        "%.0f 秒后自动重试 (%d/%d) ..." % (delay, attempt, tries))
                time.sleep(delay)
    extra = ""
    if _browser_running(cookies_browser):
        extra = ("浏览器 %s 正在运行, 已自动重试 %d 次仍无法读取其 Cookie。\n"
                 "建议: 完全关闭 %s 后再试, 或改用『Cookie文件』方式(无需关闭浏览器)。\n"
                 % (cookies_browser, tries, cookies_browser))
    raise RuntimeError("%s%s" % (extra, last))


def _cookie_opts(cookies_browser=None, cookies_file=None):
    """把 Cookie 来源转成 yt-dlp 选项; 文件方式优先(无需关闭浏览器)."""
    _ensure_windows_env()
    if cookies_file:
        if not os.path.isfile(cookies_file):
            raise RuntimeError("Cookie 文件不存在: %s" % cookies_file)
        return {"cookiefile": cookies_file}
    if cookies_browser:
        return {"cookiesfrombrowser": (cookies_browser,)}
    return {}



def fetch_info(url, cookies_browser=None, cookies_file=None, log_hook=None):
    """解析视频页, 返回 yt-dlp 的信息字典 (含 formats 列表)."""
    _ensure_windows_env()
    from yt_dlp import YoutubeDL

    def _run():
        opts = _base_opts()
        opts.update(_cookie_opts(cookies_browser, cookies_file))
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    return _with_cookie_retry(_run, cookies_browser, log=log_hook)

def _codec_rank(f):
    """编码优先度: H.264 > HEVC > AV1 > 其它 (与 BEST_FORMAT 的默认选择一致)."""
    v = (f.get("vcodec") or "").lower()
    if v.startswith("avc1"):
        return 3
    if v.startswith("hvc1") or v.startswith("hev1"):
        return 2
    if v.startswith("av01"):
        return 1
    return 0


def pick_best_video_index(formats):
    """返回最佳纯视频轨(或完整轨)的列表下标; 找不到返回 None.

    排序优先级: 清晰度 > 帧率 > 编码(H.264 优先) > 码率 > 纯视频轨.
    与 BEST_FORMAT 的实际选择保持一致, 保证界面高亮的行就是会被下载的流.
    """
    best_i, best_key = None, None
    for i, f in enumerate(formats):
        if not has_video(f):
            continue
        key = (
            (f.get("height") or 0),
            (f.get("fps") or 0),
            _codec_rank(f),
            (f.get("tbr") or 0),
            1 if not has_audio(f) else 0,
        )
        if best_key is None or key > best_key:
            best_key, best_i = key, i
    return best_i


def make_format_spec(f):
    """根据所选格式自动构造 yt-dlp 的 format 参数 (分离轨自动补音频).

    补音频时优先 AAC(mp4a), 兼容剪辑软件; 没有再用 bestaudio.
    """
    fid = f["format_id"]
    if has_video(f) and not has_audio(f):
        return "%s+bestaudio[acodec^=mp4a]/%s+bestaudio/best" % (fid, fid)
    return fid


# ======================== 画面清理(去角标/去水印) ========================

# 百分比预设: (x%, y%, 宽%, 高%), 相对画面尺寸, 自动适配 720P/1080P/4K
_CLEAN_PRESETS = {
    "top-right": (86.8, 3.7, 10.7, 8.5),
    "top-left": (2.5, 3.7, 10.7, 8.5),
    "bottom-right": (86.8, 87.8, 10.7, 8.5),
    "bottom-left": (2.5, 87.8, 10.7, 8.5),
}

_CLEAN_METHODS = ("delogo", "box", "crop")

# 默认"最佳画质"表达式:
#   优先 H.264(avc1) —— 兼容性最好、码率档位最高(如 B站 30112 1080P 高码率),
#   音频优先 AAC(mp4a) 便于剪辑; 没有 avc1 时再回退到任意编码(如 4K av01).
BEST_FORMAT = ("bv*[vcodec^=avc1]+ba[acodec^=mp4a]"
               "/bv*[vcodec^=avc1]+ba"
               "/bv*+ba/b")


def _probe_video_detail(path, timeout=120):
    """探测视频信息, 返回 (info, ffmpeg 原始输出, 是否超时).

    超时单独标记, 不吞掉: 大文件刚写完时安全软件扫描可能让 ffmpeg 卡住,
    调用方据此决定"保留文件"还是"判定损坏".
    """
    exe = _ffmpeg_location()
    if not exe:
        return {}, "未找到 ffmpeg", False
    if not os.path.isfile(path):
        return {}, "文件不存在", False
    import subprocess
    try:
        r = subprocess.run([exe, "-hide_banner", "-i", path],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        text = r.stderr or ""
        m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text, re.S)
        d = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
        out = {}
        if m:
            out["width"], out["height"] = int(m.group(1)), int(m.group(2))
        if d:
            out["duration"] = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
        return out, text, False
    except subprocess.TimeoutExpired:
        return {}, "probe 超时(%d 秒): 文件可能正被杀毒软件扫描或磁盘繁忙" % timeout, True
    except Exception as e:
        return {}, "probe 异常: %s" % e, False


def probe_video_info(path, retries=1, delay=0.0):
    """读取视频宽高/时长; 失败返回空 dict; 探测超时返回 {"timeout": True}."""
    for attempt in range(max(1, retries)):
        info, _raw, timed_out = _probe_video_detail(path)
        if timed_out:
            return {"timeout": True}
        if info.get("width") and info.get("height"):
            return info
        if attempt < retries - 1:
            time.sleep(delay)
    return {}


def resolve_clean_rect(width, height, rect=None, preset=None):
    """解析清理区域: 百分比预设或像素矩形 -> (x, y, w, h)."""
    if preset in _CLEAN_PRESETS:
        px, py, pw, ph = _CLEAN_PRESETS[preset]
        return (int(width * px / 100.0), int(height * py / 100.0),
                int(width * pw / 100.0), int(height * ph / 100.0))
    if rect:
        try:
            vals = rect if isinstance(rect, (list, tuple)) else str(rect).split(",")
            x, y, w, h = [int(float(v)) for v in vals]
            return (x, y, w, h)
        except Exception:
            return None
    return None


def build_clean_filter(width, height, rect, method="delogo"):
    """生成 ffmpeg 滤镜表达式.

    注意: delogo 会从区域边界采样像素做插值, 区域贴边时容易残留亮边,
    因此自动向外扩 8% (至少 6px) 的边距, 显著提升修复质量.
    """
    x, y, w, h = rect
    mx = max(6, int(w * 0.08))
    my = max(6, int(h * 0.08))
    x = max(0, x - mx)
    y = max(0, y - my)
    w = min(width - x, w + 2 * mx)
    h = min(height - y, h + 2 * my)
    x = max(0, min(x, width - 2))
    y = max(0, min(y, height - 2))
    w = max(2, min(w, width - x))
    h = max(2, min(h, height - y))
    if method == "box":
        return "drawbox=x=%d:y=%d:w=%d:h=%d:color=black@0.6:t=fill" % (x, y, w, h)
    if method == "crop":
        if x + w >= width - 4:
            return "crop=%d:%d:0:0,scale=%d:%d" % (max(2, x - 2), height, width, height)
        if x <= 4:
            return "crop=%d:%d:%d:0,scale=%d:%d" % (max(2, width - x - w - 2), height,
                                                    min(width, x + w + 2), width, height)
        if y + h >= height - 4:
            return "crop=%d:%d:0:0,scale=%d:%d" % (width, max(2, y - 2), width, height)
        return "crop=%d:%d:0:%d,scale=%d:%d" % (width, max(2, height - y - h - 2),
                                                min(height, y + h + 2), width, height)
    return "delogo=x=%d:y=%d:w=%d:h=%d:show=0" % (x, y, w, h)


_ILLEGAL_CHARS = '<>:"/\\|?*[]'


def _safe_out_name(name):
    """清理 Windows 文件名非法字符."""
    for ch in _ILLEGAL_CHARS:
        name = name.replace(ch, "_")
    name = name.strip().rstrip(".")
    return name or "video"


def default_clean_outdir():
    """画面清理默认输出目录(独立目录, 不污染源文件所在位置)."""
    return os.path.join(default_outdir(), "cleaned")


def verify_video_output(path):
    """校验输出: 存在、非空、可解析出有效视频流.

    返回 (ok, reason, timed_out):
      - timed_out=True 表示探测超时(文件可能仍被杀软扫描), 调用方不应删除文件;
      - 只有确认"无有效视频流"才建议删除残留.
    刚写完的大文件可能被安全软件短暂占用, 因此最多重试 4 次.
    """
    if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False, "输出文件不存在或大小为 0", False
    detail = ""
    for attempt in range(4):
        info, raw, timed_out = _probe_video_detail(path)
        if timed_out:
            return False, "校验超时: %s" % raw, True
        if info.get("width") and info.get("height"):
            if float(info.get("duration") or 0) > 0:
                return True, "", False
            detail = "解析到视频流但时长无效"
        else:
            tail = (raw or "").strip().replace("\n", " ")
            detail = tail[-500:] if tail else "无视频流/无法打开"
        if attempt < 3:
            time.sleep(0.8)
    return False, "输出文件无法解析(%s, 大小 %.1f MB)" % (
        detail, os.path.getsize(path) / 1048576.0), False


def _check_disk_space(target_dir, need_bytes):
    import shutil
    try:
        free = shutil.disk_usage(target_dir).free
    except Exception:
        return
    if free < need_bytes:
        raise RuntimeError("磁盘空间不足: 需要约 %.0f MB, 目标盘剩余 %.0f MB"
                           % (need_bytes / 1048576.0, free / 1048576.0))


def clean_video(src, rect=None, preset=None, method="delogo", out_path=None,
                out_dir=None, log_hook=None, progress_hook=None,
                cancel_check=None, overwrite=False):
    """对视频做画面清理(去角标/去水印), 原文件永不覆盖.

    输出固定为 H.264 + AAC 的 mp4, 默认写入独立目录 downloads/cleaned/.
    progress_hook: 进度回调(0~100); cancel_check: 返回 True 时中止并清理残留.
    """
    if not src or not os.path.isfile(src):
        raise RuntimeError("输入文件不存在: %s" % src)
    exe = _ffmpeg_location()
    if not exe:
        raise RuntimeError("未找到 ffmpeg, 无法处理视频")
    info = probe_video_info(src)
    if info.get("timeout"):
        raise RuntimeError("读取源视频信息超时(文件可能正被杀毒软件扫描), 请稍后重试: %s"
                           % os.path.basename(src))
    width, height = info.get("width"), info.get("height")
    if not (width and height):
        raise RuntimeError("视频损坏或无法解析: %s" % os.path.basename(src))
    duration = float(info.get("duration") or 0)
    r = resolve_clean_rect(width, height, rect, preset)
    if not r:
        raise RuntimeError("未指定清理区域(rect 或 preset)")
    if method not in _CLEAN_METHODS:
        method = "delogo"
    vf = build_clean_filter(width, height, r, method)

    if not out_path:
        base_dir = out_dir or default_clean_outdir()
        os.makedirs(base_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(src))[0]
        out_path = os.path.join(base_dir, _safe_out_name(stem) + "_clean.mp4")
    else:
        od = os.path.dirname(os.path.abspath(out_path))
        if od:
            os.makedirs(od, exist_ok=True)
    # 并发保护: 同一输出文件同时只允许一个任务处理, 避免两个进程写坏同一个文件
    lock_path = out_path + ".lock"
    if os.path.exists(lock_path):
        try:
            stale = time.time() - os.path.getmtime(lock_path) > 7200
        except OSError:
            stale = False
        if not stale:
            raise RuntimeError("另一个任务正在处理同一输出文件, 请稍后再试: %s"
                               % os.path.basename(out_path))
        try:
            os.remove(lock_path)
        except OSError:
            pass

    if os.path.exists(out_path) and not overwrite:
        ok, reason, timed_out = verify_video_output(out_path)
        if ok:
            if log_hook:
                log_hook("画面清理: 已存在且校验通过 %s (跳过)" % os.path.basename(out_path))
            return out_path
        if timed_out:
            if log_hook:
                log_hook("[提示] 现有输出暂时无法校验(可能被安全软件占用), 将重新处理: %s" % reason)
        else:
            if log_hook:
                log_hook("[提示] 现有输出已损坏, 将重新处理: %s" % reason)
            try:
                os.remove(out_path)
            except Exception:
                pass

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        raise RuntimeError("另一个任务正在处理同一输出文件, 请稍后再试: %s"
                           % os.path.basename(out_path))
    try:
        # 磁盘空间检查(输出约为源文件的 1.2 倍, 至少预留 200MB)
        need = max(200 * 1048576, int(os.path.getsize(src) * 1.2))
        _check_disk_space(os.path.dirname(os.path.abspath(out_path)), need)

        if log_hook:
            log_hook("画面清理: %s | 区域 x=%d y=%d w=%d h=%d | 方式 %s"
                     % (os.path.basename(src), r[0], r[1], r[2], r[3], method))
        import subprocess
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", src,
               "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
               "-progress", "pipe:1", "-nostats", out_path]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
        err = ""
        try:
            for line in proc.stdout:
                if cancel_check and cancel_check():
                    proc.kill()
                    raise RuntimeError("已取消")
                line = line.strip()
                if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                    try:
                        us = int(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    if progress_hook and duration > 0:
                        progress_hook(min(99.9, us / 1000000.0 / duration * 100.0))
        finally:
            # 编码结束后的 faststart 收尾阶段没有进度输出, 主动刷新活动时间
            try:
                if log_hook:
                    log_hook("正在写入文件索引(faststart)并收尾, 请稍候...")
            except Exception:
                pass
            try:
                err = (proc.stderr.read() or "").strip()
            except Exception:
                pass
            proc.wait()

        if proc.returncode != 0:
            try:
                os.remove(out_path)
            except Exception:
                pass
            raise RuntimeError("ffmpeg 处理失败: %s" % (err[-300:] or "未知错误"))

        time.sleep(0.3)
        ok, reason, timed_out = verify_video_output(out_path)
        if not ok:
            if timed_out:
                raise RuntimeError("输出校验超时: %s (输出文件已保留, 可稍后重试)" % reason)
            if log_hook:
                log_hook("[校验失败] 输出文件无法解析: %s" % reason)
            try:
                os.remove(out_path)
            except Exception:
                pass
            raise RuntimeError("输出校验失败: %s (已删除残留文件)" % reason)

        if progress_hook:
            progress_hook(100.0)
        if log_hook:
            log_hook("画面清理完成: %s (%.1f MB)" % (os.path.basename(out_path),
                                                  os.path.getsize(out_path) / 1048576.0))
        return out_path
    finally:
        try:
            os.remove(lock_path)
        except Exception:
            pass


def preview_clean(src, rect=None, preset=None, method="delogo", out_dir=None, log_hook=None):
    """抽一帧生成清理前后对比图, 返回对比图路径(可直接用系统看图打开)."""
    exe = _ffmpeg_location()
    if not exe:
        raise RuntimeError("未找到 ffmpeg")
    info = probe_video_info(src)
    if info.get("timeout"):
        raise RuntimeError("读取视频信息超时(文件可能正被杀毒软件扫描), 请稍后重试")
    width, height = info.get("width"), info.get("height")
    if not (width and height):
        raise RuntimeError("无法读取视频分辨率")
    r = resolve_clean_rect(width, height, rect, preset)
    if not r:
        raise RuntimeError("未指定清理区域")
    vf = build_clean_filter(width, height, r, method)
    out_dir = out_dir or os.path.dirname(os.path.abspath(src))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0]
    t = float(info.get("duration", 60)) * 0.15
    x, y, w, h = r
    cw = min(width, w + 120)
    ch = min(height, h + 120)
    cx = max(0, min(x - 60, width - cw))
    cy = max(0, min(y - 60, height - ch))
    region = "crop=%d:%d:%d:%d,scale=iw*2:ih*2:flags=neighbor" % (cw, ch, cx, cy)
    before = os.path.join(out_dir, base + "_预览_原.png")
    after = os.path.join(out_dir, base + "_预览_净.png")
    cmp = os.path.join(out_dir, base + "_预览_对比.png")
    import subprocess
    subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-ss", "%.2f" % t,
                    "-i", src, "-frames:v", "1", "-vf", region, "-y", before],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=120)
    subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-ss", "%.2f" % t,
                    "-i", src, "-frames:v", "1", "-vf", vf + "," + region, "-y", after],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=120)
    subprocess.run([exe, "-hide_banner", "-loglevel", "error",
                    "-i", before, "-i", after,
                    "-filter_complex", "[0:v][1:v]vstack=inputs=2", "-y", cmp],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=120)
    if log_hook:
        log_hook("预览对比图: %s" % cmp)
    return cmp


def _parse_rate(text):
    """把 '2M' / '500K' 这类限速值转成字节/秒; 非法返回 None."""
    if not text:
        return None
    s = str(text).strip().upper()
    mult = 1
    if s.endswith("K"):
        mult, s = 1024, s[:-1]
    elif s.endswith("M"):
        mult, s = 1024 * 1024, s[:-1]
    elif s.endswith("G"):
        mult, s = 1024 * 1024 * 1024, s[:-1]
    try:
        return int(float(s) * mult)
    except Exception:
        return None


def friendly_error_hint(msg):
    """把常见网络/风控错误翻译成可操作的中文提示."""
    m = (msg or "").lower()
    if "412" in m or "precondition" in m:
        return "站点风控(412): 建议改用 Cookie 文件方式, 或稍等几分钟后重试。"
    if "403" in m:
        return "访问被拒绝(403): 可能需要登录态(Cookie 文件), 或内容受地区/会员限制。"
    if "429" in m or "too many requests" in m:
        return "请求过多(429): 已触发限流, 请用 --sleep 放慢速度, 或稍后再试。"
    if "unable to download" in m or "timed out" in m or "timeout" in m:
        return "网络连接失败: 检查网络或代理; 国内直连 GitHub 等站点通常需要代理。"
    if "cookie" in m:
        return "Cookie 读取失败: 关闭浏览器后重试, 或改用 Cookie 文件方式。"
    return ""


def _downloaded_path(info, outdir=None):
    """从 yt-dlp 返回信息里取下载完成的文件路径."""
    try:
        rd = info.get("requested_downloads") or []
        if rd and rd[0].get("filepath"):
            return rd[0]["filepath"]
    except Exception:
        pass
    d = outdir or default_outdir()
    best, bt = None, -1
    try:
        for name in os.listdir(d):
            if name.lower().endswith((".mp4", ".mkv", ".webm", ".mp3", ".m4a")):
                p = os.path.join(d, name)
                t = os.path.getmtime(p)
                if t > bt:
                    bt, best = t, p
    except Exception:
        pass
    return best


def _run_clean_files(args):
    """批量处理已有视频文件."""
    targets = []
    for item in args.clean_file or []:
        if os.path.isdir(item):
            targets += [os.path.join(item, f) for f in os.listdir(item)
                        if f.lower().endswith((".mp4", ".mkv", ".webm", ".flv", ".mov"))]
        elif os.path.isfile(item):
            targets.append(item)
    if not targets:
        print("没有找到可处理的视频文件。")
        return
    rect = args.clean_rect
    preset = args.clean_preset or (None if rect else "top-right")
    out_dir = args.clean_outdir or default_clean_outdir()
    for src in targets:
        if args.preview_clean:
            p = preview_clean(src, rect=rect, preset=preset,
                              method=args.clean_method, out_dir=out_dir)
            print("预览对比图:", p)
        else:
            try:
                out = clean_video(src, rect=rect, preset=preset,
                                  method=args.clean_method, out_dir=out_dir,
                                  log_hook=print)
                print("已生成:", out)
            except Exception as e:
                print("处理失败:", e)


class _LogForwarder:
    """把 yt-dlp 的内部日志(重试/警告等)转发给界面."""

    def __init__(self, cb):
        self._cb = cb

    def debug(self, msg):
        self._cb(msg)

    def info(self, msg):
        self._cb(msg)

    def warning(self, msg):
        self._cb("[警告] " + msg)

    def error(self, msg):
        self._cb("[错误] " + msg)


def download(url, format_spec, outdir=None, cookies_browser=None,
             cookies_file=None, audio_only=False, playlist=False,
             progress_hook=None, log_hook=None,
             proxy=None, sleep=None, limit_rate=None):
    """
    下载核心.
    format_spec : yt-dlp 格式表达式, 如 "bv*+ba/b" / "137+140" / "best"
    audio_only  : True 时忽略 format_spec, 只提音频并转 MP3
    progress_hook: 回调函数(dict), 内容与 yt-dlp 进度钩子一致
    返回下载完成后 yt-dlp 的 info 字典.
    """
    _ensure_windows_env()
    from yt_dlp import YoutubeDL
    outdir = outdir or default_outdir()
    os.makedirs(outdir, exist_ok=True)

    opts = {
        "outtmpl": os.path.join(outdir, "%(title).120B [%(id)s].%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": not playlist,
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "fragment_retries": 3,
        "socket_timeout": 15,
        "noprogress": True,
        "overwrites": True,   # 每次点击下载都重新下载(避免"已下载过"静默跳过)
        "progress_hooks": [progress_hook] if progress_hook else [],
        # 更像真实浏览器的请求头, 降低被风控概率
        "http_headers": {
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        # 避免并发片段请求触发风控
        "concurrent_fragment_downloads": 1,
    }
    if proxy:
        opts["proxy"] = proxy
    if limit_rate:
        opts["ratelimit"] = limit_rate
    if sleep:
        opts["sleep_interval"] = sleep
        opts["max_sleep_interval"] = sleep
        opts["sleep_interval_requests"] = sleep
    if log_hook:
        opts.update({
            "quiet": False,
            "no_warnings": False,
            "logger": _LogForwarder(log_hook),
        })
    opts.update(_cookie_opts(cookies_browser, cookies_file))
    floc = _ffmpeg_location()
    if floc:
        opts["ffmpeg_location"] = floc  # 合并 DASH 视频/音频轨必需

    if audio_only:
        opts["format"] = "bestaudio[acodec^=mp4a]/bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }]
    else:
        opts["format"] = format_spec
    def _run():
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)

    return _with_cookie_retry(_run, cookies_browser, log=log_hook)



def playable_formats(info):
    """过滤出真正可播放的格式(去掉 storyboard/纯元数据等无用项), 并按 format_id 去重."""
    seen = set()
    out = []
    for f in (info.get("formats") or []):
        fid = f.get("format_id", "")
        if fid in seen:
            continue
        if not (has_video(f) or has_audio(f)):
            continue
        seen.add(fid)
        out.append(f)
    return out


def _fmt_count(info):
    fmts = playable_formats(info)
    v = sum(1 for f in fmts if has_video(f))
    a = sum(1 for f in fmts if has_audio(f) and not has_video(f))
    return len(fmts), v, a


def cmd_list(url, cookies_browser, cookies_file=None):
    info = fetch_info(url, cookies_browser, cookies_file)
    title = info.get("title") or url
    print("标题:", title)
    n, v, a = _fmt_count(info)
    print("共 %d 个格式 | 视频轨 %d | 纯音频轨 %d" % (n, v, a))
    fmts = playable_formats(info)
    for i, f in enumerate(fmts):
        print("  %2d. %s" % (i, describe(f)))
    return fmts


def cmd_download(args):
    if args.clean_file:
        _run_clean_files(args)
        return
    if not args.url:
        print("请提供视频链接(或使用 --clean-file 处理已有文件)。")
        return
    args.url, cleaned = extract_url(args.url)  # 自动清理混入的标题文字
    if cleaned:
        print("已自动清理链接:", args.url)
    if args.list:
        cmd_list(args.url, args.cookies, args.cookies_file)
        return
    if args.audio:
        print("仅提取音频 MP3(320k) ...")
        download(args.url, "bestaudio/best", args.outdir, args.cookies,
                 args.cookies_file,
                 audio_only=True, playlist=args.playlist,
                 progress_hook=_cli_progress,
                 proxy=args.proxy, sleep=args.sleep,
                 limit_rate=_parse_rate(args.limit_rate))
        print("完成: 已保存到", args.outdir or default_outdir(), "(MP3)")
        return

    fmt_spec = None
    if args.best:
        fmt_spec = BEST_FORMAT
    elif args.format:
        fmt_spec = args.format
    else:
        # 交互: 先列格式再让用户选
        fmts = cmd_list(args.url, args.cookies, args.cookies_file)
        if not fmts:
            print("没有可下载的格式。")
            return
        print("输入格式编号(如 1)或格式ID(如 137), 直接回车=最佳画质:")
        try:
            line = input("> ").strip()
        except EOFError:
            line = ""
        if not line:
            fmt_spec = BEST_FORMAT
        else:
            pick = None
            if line.isdigit() and int(line) < len(fmts):
                pick = fmts[int(line)]
            else:
                for f in fmts:
                    if f.get("format_id") == line:
                        pick = f
                        break
            if pick is None:
                print("无效输入, 使用最佳画质。")
                fmt_spec = BEST_FORMAT
            else:
                fmt_spec = make_format_spec(pick)
                print("选择:", describe(pick))

    print("开始下载 ...")
    info = None
    try:
        info = download(args.url, fmt_spec, args.outdir, args.cookies,
                        args.cookies_file,
                        playlist=args.playlist, progress_hook=_cli_progress,
                        proxy=args.proxy, sleep=args.sleep,
                        limit_rate=_parse_rate(args.limit_rate))
    except Exception as e:
        msg = str(e)
        if "ffmpeg" in msg.lower() or "postprocess" in msg.lower():
            print("\n[提示] 看起来缺少 ffmpeg 或合并失败。")
            print("        ffmpeg 用于合并 DASH 分离的视频轨/音频轨。")
            print("        安装方法: winget install Gyan.FFmpeg  (装完重开终端)")
        hint = friendly_error_hint(msg)
        if hint:
            print("[提示]", hint)
        raise
    print("\n完成! 已保存到:", args.outdir or default_outdir())
    # 说明: 下载后不会自动清理(避免二次编码损失画质); 需要清理请用 --clean-file
    if args.preview_clean:
        path = _downloaded_path(info, args.outdir)
        if path:
            rect = args.clean_rect
            preset = args.clean_preset or (None if rect else "top-right")
            try:
                print("预览对比图:", preview_clean(path, rect=rect, preset=preset,
                                                  method=args.clean_method,
                                                  out_dir=os.path.dirname(path)))
            except Exception as e:
                print("预览失败:", e)


def _cli_progress(d):
    if d.get("status") == "downloading":
        pct = d.get("_percent_str", "").strip()
        speed = (d.get("_speed_str") or "").strip()
        eta = (d.get("_eta_str") or "").strip()
        name = os.path.basename(d.get("filename") or "")
        sys.stdout.write("\r  %s  %s  %s  %s" % (name[-40:], pct, speed, eta))
        sys.stdout.flush()
    elif d.get("status") == "finished":
        print()


def build_parser():
    p = argparse.ArgumentParser(
        prog="dash_dl",
        description="DASH/HLS 流媒体下载器 (yt-dlp 封装)。视频URL 或 直接流地址均可。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("url", nargs="?", help="视频页 URL 或 直接流地址 (.mpd / .m3u8)")
    p.add_argument("--list", "-F", action="store_true", help="只列出可用格式")
    p.add_argument("--best", "-b", action="store_true", help="下载最佳画质并自动合并")
    p.add_argument("--format", "-f", help="yt-dlp 格式表达式, 如 137+140 / bv*+ba")
    p.add_argument("--audio-mp3", "-a", dest="audio", action="store_true",
                   help="仅提取音频为 MP3")
    p.add_argument("--cookies", "-c", choices=_cookie_browsers() + [""],
                   default=None, help="借用哪个浏览器的登录态(需先关闭该浏览器)")
    p.add_argument("--cookies-file", default=None,
                   help="Netscape 格式的 cookie 文件路径(浏览器扩展导出, 无需关闭浏览器)")
    p.add_argument("--outdir", "-o", default=None, help="保存目录(默认脚本目录/downloads)")
    p.add_argument("--playlist", action="store_true", help="下载整个播放列表")
    # ---- 反爬 / 网络 ----
    p.add_argument("--proxy", default=None, help="代理地址, 如 http://127.0.0.1:7890")
    p.add_argument("--sleep", type=float, default=None,
                   help="请求间隔秒数(降低触发站点风控概率)")
    p.add_argument("--limit-rate", default=None, help="限速, 如 2M / 500K(字节/秒)")
    # ---- 画面清理(去角标/去水印, 独立功能, 不影响下载画质) ----
    p.add_argument("--clean-file", action="append", default=None,
                   help="处理已有视频(文件或目录, 可多次指定), 不下载")
    p.add_argument("--clean-preset", choices=list(_CLEAN_PRESETS), default=None,
                   help="清理区域角落预设")
    p.add_argument("--clean-rect", default=None, help="清理区域像素: X,Y,W,H")
    p.add_argument("--clean-method", choices=list(_CLEAN_METHODS), default="delogo",
                   help="清理方式: delogo 模糊填充 / box 色块 / crop 裁剪")
    p.add_argument("--preview-clean", action="store_true",
                   help="生成清理前后对比图(不处理整片)")
    p.add_argument("--clean-outdir", default=None,
                   help="画面清理输出目录(默认 downloads/cleaned)")
    p.add_argument("--version", action="version", version="DASH Video Grabber " + __version__)
    return p


def main(argv=None):
    _console_utf8()
    args = build_parser().parse_args(argv)
    try:
        import yt_dlp  # noqa
    except ImportError:
        print("错误: 未安装 yt-dlp。请先运行:  python -m pip install -U yt-dlp")
        return 2
    try:
        cmd_download(args)
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as e:
        print("\n失败:", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
