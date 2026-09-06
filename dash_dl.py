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
  python dash_dl.py "视频URL"                # 交互式选择画质下载
  python dash_dl.py "视频URL" --list         # 仅列出全部可用画质/格式
  python dash_dl.py "视频URL" --best         # 直接下载最佳画质(自动合并)
  python dash_dl.py "视频URL" -f 137+140     # 按 yt-dlp 格式ID 下载
  python dash_dl.py "视频URL" --audio-mp3    # 仅提取音频为 MP3(320k)
  python dash_dl.py "视频URL" --cookies edge # 借用浏览器登录态(可下B站高码率)
  python dash_dl.py "视频URL" -o "D:/素材"   # 指定保存目录
  python dash_dl.py "视频URL" --playlist     # 下载整个播放列表
"""

import argparse
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _console_utf8():
    """输出被重定向(管道/文件)时强制 UTF-8, 保证中文不乱码; 终端内保持系统编码."""
    try:
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
        https://【《鸣潮》共鸣者「清宵」PV | 修行】 https://www.bilibili.com/video/BV1GFbk6LEVm/?...
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

def pick_best_video_index(formats):
    """返回最佳纯视频轨(或完整轨)的列表下标; 找不到返回 None."""
    best_i, best_score = None, -1
    for i, f in enumerate(formats):
        if not has_video(f):
            continue
        score = (f.get("height") or 0) * 1000 + (f.get("fps") or 0)
        # DASH 纯视频轨通常码率更高, 加权 2000 优先选择(之后自动补音频)
        score += 2000 if not has_audio(f) else 0
        if score > best_score:
            best_score, best_i = score, i
    return best_i


def make_format_spec(f):
    """根据所选格式自动构造 yt-dlp 的 format 参数 (分离轨自动补音频)."""
    fid = f["format_id"]
    if has_video(f) and not has_audio(f):
        return "%s+bestaudio/best" % fid   # DASH 纯视频轨 + 最佳音频
    return fid


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
             progress_hook=None, log_hook=None):
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
    }
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
        opts["format"] = "bestaudio/best"
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
                 progress_hook=_cli_progress)
        print("完成: 已保存到", args.outdir or default_outdir(), "(MP3)")
        return

    fmt_spec = None
    if args.best:
        fmt_spec = "bv*+ba/b"
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
            fmt_spec = "bv*+ba/b"
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
                fmt_spec = "bv*+ba/b"
            else:
                fmt_spec = make_format_spec(pick)
                print("选择:", describe(pick))

    print("开始下载 ...")
    try:
        download(args.url, fmt_spec, args.outdir, args.cookies,
                 args.cookies_file,
                 playlist=args.playlist, progress_hook=_cli_progress)
    except Exception as e:
        msg = str(e)
        if "ffmpeg" in msg.lower() or "postprocess" in msg.lower():
            print("\n[提示] 看起来缺少 ffmpeg 或合并失败。")
            print("        ffmpeg 用于合并 DASH 分离的视频轨/音频轨。")
            print("        安装方法: winget install Gyan.FFmpeg  (装完重开终端)")
        raise
    print("\n完成! 已保存到:", args.outdir or default_outdir())


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
    p.add_argument("url", help="视频页 URL 或 直接流地址 (.mpd / .m3u8)")
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
