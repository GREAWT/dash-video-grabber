# -*- coding: utf-8 -*-
"""
DASH 视频下载器 - 图形界面版
==============================
基于 yt-dlp 的 DASH/HLS 流媒体下载器。

运行:  python dash_gui.py   (或双击 start_gui.bat)

用法:
  1. 把网页视频链接粘贴到输入框 (B站/YouTube/抖音/... 或直接 .mpd/.m3u8 地址)
  2. 点 [解析画质]   ->  下方列出所有清晰度/格式
  3. 选中一行, 点 [下载选中]  或直接 [最佳画质]
  4. 完成后在右侧/底部查看进度, 文件保存到 "downloads" 文件夹

需要登录/会员清晰度时: 在"借用浏览器"下拉里选择你已登录的浏览器。
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import dash_dl as core
except ImportError:
    # 兼容从其他目录运行
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import dash_dl as core

FONT = ("Microsoft YaHei UI", 10)
FONT_B = ("Microsoft YaHei UI", 10, "bold")
COOKIE_CHOICES = ["不借用（免登录）"] + core._cookie_browsers()


class DashApp:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.working = False
        self.formats = []          # 当前视频的格式列表 (与 Listbox 行一一对应)
        self.outdir = core.default_outdir()
        self._cancel = False        # 停止标志
        self._dl_start = 0.0        # 本次下载开始时间
        self._last_activity = 0.0   # 最近进度/日志时间(卡死检测用)
        self._hint_at = 0.0         # 60秒无进度提示时间戳
        self._stop_at = None        # 点击停止的时间(45秒兜底)
        self._watch_seen = None     # 文件检测: (路径, 大小)
        self._done_notified = False # 完成通知是否已发(防重复)

        root.title("DASH 视频下载器 (yt-dlp)")
        root.geometry("980x680")
        root.minsize(860, 600)

        pad = {"padx": 8, "pady": 4}
        self._build_widgets()
        self.root.after(100, self._poll_queue)

    # ---------------- 界面布局 ----------------
    def _build_widgets(self):
        f0 = ttk.Frame(self.root); f0.pack(fill="x", padx=8, pady=(8, 0))

        ttk.Label(f0, text="视频链接:", font=FONT).pack(side="left")
        self.url_var = tk.StringVar()
        e = ttk.Entry(f0, textvariable=self.url_var, font=FONT)
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda _e: self.do_parse())
        self.btn_parse = ttk.Button(f0, text="解析画质", command=self.do_parse)
        self.btn_parse.pack(side="left")

        f1 = ttk.Frame(self.root); f1.pack(fill="x", padx=8, pady=2)
        ttk.Label(f1, text="借用浏览器登录态(下会员/高码率用):", font=FONT).pack(side="left")
        self.cookie_var = tk.StringVar(value=COOKIE_CHOICES[0])
        ttk.Combobox(f1, textvariable=self.cookie_var, values=COOKIE_CHOICES,
                     state="readonly", width=18, font=FONT).pack(side="left", padx=6)
        ttk.Label(f1, text="保存到:", font=FONT).pack(side="left", padx=(16, 0))
        self.dir_var = tk.StringVar(value=self.outdir)
        ttk.Entry(f1, textvariable=self.dir_var, font=FONT).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(f1, text="选择文件夹", command=self.choose_dir).pack(side="left")

        f1b = ttk.Frame(self.root); f1b.pack(fill="x", padx=8, pady=2)
        self.use_cf = tk.BooleanVar(value=False)
        tk.Checkbutton(f1b, text="使用Cookie文件(浏览器导出一次即可, 无需关闭浏览器)", variable=self.use_cf,
                       command=self._toggle_cf, font=FONT).pack(side="left")
        self.cf_var = tk.StringVar(value="")
        ttk.Entry(f1b, textvariable=self.cf_var, font=FONT, width=44).pack(side="left", padx=6)
        self.btn_cf = ttk.Button(f1b, text="选择Cookie文件", command=self.choose_cookie_file)
        self.btn_cf.pack(side="left")
        self.btn_cf.configure(state="disabled")

        # 格式列表
        f2 = ttk.Frame(self.root); f2.pack(fill="both", expand=True, padx=8, pady=2)
        ttk.Label(f2, text="可用画质 / 格式 (选中的行会高亮):", font=FONT_B).pack(anchor="w")
        body = ttk.Frame(f2); body.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(body, font=("Consolas", 9), selectmode=tk.SINGLE,
                                  activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        # 按钮行
        f3 = ttk.Frame(self.root); f3.pack(fill="x", padx=8, pady=4)
        self.btn_best = ttk.Button(f3, text="★ 下载最佳画质(自动合并)", command=lambda: self.do_download("best"))
        self.btn_best.pack(side="left")
        self.btn_sel = ttk.Button(f3, text="下载选中画质", command=lambda: self.do_download("selected"))
        self.btn_sel.pack(side="left", padx=6)
        self.btn_audio = ttk.Button(f3, text="仅提取音频 MP3", command=lambda: self.do_download("audio"))
        self.btn_audio.pack(side="left", padx=6)
        self.btn_stop = ttk.Button(f3, text="■ 停止", command=self.stop_download)
        self.btn_stop.pack(side="left", padx=6)
        self.btn_stop.configure(state="disabled")
        self.btn_open = ttk.Button(f3, text="打开下载文件夹", command=self.open_outdir)
        self.btn_open.pack(side="left", padx=6)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(f3, textvariable=self.status_var, font=FONT_B, foreground="#1a7f37").pack(side="right")

        # 进度条 + 日志
        f4 = ttk.Frame(self.root); f4.pack(fill="x", padx=8, pady=2)
        self.progress = ttk.Progressbar(f4, mode="determinate")
        self.progress.pack(fill="x")
        self.pct_var = tk.StringVar(value="")
        ttk.Label(f4, textvariable=self.pct_var, font=("Consolas", 9)).pack(anchor="e")

        f5 = ttk.Frame(self.root); f5.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        ttk.Label(f5, text="日志:", font=FONT).pack(anchor="w")
        self.log = tk.Text(f5, height=7, font=("Consolas", 9), state="disabled", wrap="word")
        lsb = ttk.Scrollbar(f5, command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)

    # ---------------- 工具 ----------------
    def log_line(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", s + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or self.outdir)
        if d:
            self.dir_var.set(d)

    def open_outdir(self):
        d = self.dir_var.get().strip() or self.outdir
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)   # Windows
        except Exception:
            pass

    def cookie_browser(self):
        c = self.cookie_var.get()
        return None if c == COOKIE_CHOICES[0] else c

    def _toggle_cf(self):
        self.btn_cf.configure(state="normal" if self.use_cf.get() else "disabled")
        if not self.use_cf.get():
            self.cf_var.set("")

    def choose_cookie_file(self):
        f = filedialog.askopenfilename(
            title="选择导出的Cookie文件 (Netscape 格式 .txt)",
            filetypes=[("Cookie文件", "*.txt"), ("所有文件", "*.*")])
        if f:
            self.cf_var.set(f)

    def cookie_file_path(self):
        if self.use_cf.get():
            return self.cf_var.get().strip() or None
        return None

    def set_working(self, flag):
        self.working = flag
        for b in (self.btn_parse, self.btn_best, self.btn_sel, self.btn_audio):
            b.configure(state="disabled" if flag else "normal")
        self.btn_stop.configure(state="normal" if flag else "disabled")

    # ---------------- 解析 ----------------
    def do_parse(self):
        url, changed = core.extract_url(self.url_var.get())
        if not url:
            messagebox.showwarning("提示", "请先粘贴视频链接。")
            return
        if changed:
            self.url_var.set(url)
            self.log_line("已自动清理链接(去掉混入的标题文字): %s" % url)
        if self.working:
            return
        self.set_working(True)
        self.status_var.set("解析中...")
        self.progress["value"] = 0
        self.pct_var.set("")
        browser = self.cookie_browser()
        cfile = self.cookie_file_path()
        threading.Thread(target=self._parse_worker, args=(url, browser, cfile), daemon=True).start()

    def _parse_worker(self, url, browser, cfile):
        try:
            info = core.fetch_info(url, browser, cfile, self._log_hook)
            self.q.put(("parsed", info))
        except Exception as e:
            self.q.put(("error", "解析失败: %s" % e))

    def _apply_parsed(self, info):
        title = info.get("title") or ""
        fmts = core.playable_formats(info)
        self.formats = fmts
        self.listbox.delete(0, "end")
        for f in fmts:
            self.listbox.insert("end", core.describe(f))
        best = core.pick_best_video_index(fmts)
        if best is not None:
            self.listbox.selection_set(best)
            self.listbox.see(best)
            self._select_info(best)
        self.log_line("标题: %s" % title)
        self.log_line("共 %d 个格式可用" % len(fmts))
        if not fmts:
            self.log_line("[提示] 没有取到格式列表。若为需要登录的网站, 请在下拉框选择已登录的浏览器后再解析。")
        self.set_working(False)
        self.status_var.set("解析完成 ✓ 可开始下载")

    def _on_select(self, _e):
        sel = self.listbox.curselection()
        if sel:
            self._select_info(sel[0])

    def _select_info(self, i):
        if 0 <= i < len(self.formats):
            f = self.formats[i]
            self.status_var.set("选中: " + core.describe(f)[:60])

    # ---------------- 下载 ----------------
    def do_download(self, mode):
        if self.working:
            return
        url, changed = core.extract_url(self.url_var.get())
        if not url:
            messagebox.showwarning("提示", "请先粘贴视频链接。")
            return
        if changed:
            self.url_var.set(url)
            self.log_line("已自动清理链接(去掉混入的标题文字): %s" % url)
        outdir = self.dir_var.get().strip() or self.outdir
        browser = self.cookie_browser()
        cfile = self.cookie_file_path()

        if mode == "best":
            spec = "bv*+ba/b"
            tag = "最佳画质(自动合并)"
        elif mode == "selected":
            sel = self.listbox.curselection()
            if not sel:
                messagebox.showwarning("提示", "请先在列表中选中一个画质。")
                return
            f = self.formats[sel[0]]
            spec = core.make_format_spec(f)
            tag = "选中画质: " + core.describe(f)
        else:  # audio
            spec = "bestaudio/best"
            tag = "仅音频 MP3"

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        self.set_working(True)
        self.log_line("=" * 60)
        self.log_line("开始下载: %s" % tag)
        self.log_line("链接: %s" % url)
        self.log_line("格式表达式: %s  (模式: %s)" % (spec, mode))
        self.log_line("保存到: %s" % outdir)
        if browser:
            self.log_line("借用浏览器: %s (登录态, 若报错请先关闭该浏览器)" % browser)
        if cfile:
            self.log_line("使用Cookie文件: %s" % cfile)
        self._cancel = False
        self._dl_start = time.time()
        self._last_activity = time.time()
        self._hint_at = 0.0
        self._stop_at = None
        self._watch_seen = None
        self._done_notified = False
        self._timeout_at = None
        self._last_hb = -1
        self.progress.configure(mode="determinate")
        self.progress["value"] = 0
        self.pct_var.set("0%")
        self.status_var.set("下载中...")
        audio_only = (mode == "audio")
        self._worker = threading.Thread(
            target=self._download_worker,
            args=(url, spec, outdir, browser, cfile, audio_only),
            daemon=True,
        )
        self._worker.start()

    def _download_worker(self, url, spec, outdir, browser, cfile, audio_only):
        try:
            core.download(
                url, spec, outdir=outdir, cookies_browser=browser,
                cookies_file=cfile, audio_only=audio_only,
                progress_hook=self._hook, log_hook=self._log_hook,
            )
            self.q.put(("done", (outdir, audio_only)))
        except Exception as e:
            if self._cancel:
                self.q.put(("stopped", "已停止(用户取消)"))
            else:
                self.q.put(("error", "下载失败: %s" % e))

    def _log_hook(self, msg):
        """yt-dlp 内部日志(重试/警告等)转发到界面日志区."""
        try:
            if msg and msg.strip():
                self.q.put(("log", msg.strip()))
        except Exception:
            pass

    def _hook(self, d):
        """yt-dlp 进度回调 (在下载线程中执行), 只把数据丢进队列."""
        try:
            if self._cancel:
                raise RuntimeError("_STOP_")   # 抛异常让 yt-dlp 中止下载
            st = d.get("status")
            if st == "downloading":
                name = os.path.basename(d.get("filename") or "")
                pct = d.get("_percent_str", "").strip()
                speed = (d.get("_speed_str") or "").strip()
                eta = (d.get("_eta_str") or "").strip()
                db = d.get("downloaded_bytes") or 0
                tb = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                num = 0.0
                try:
                    num = float(str(d.get("_percent_str", "0")).replace("%", "").strip())
                except Exception:
                    num = 0.0
                if num <= 0 and tb > 0:
                    num = db * 100.0 / tb
                size_txt = "%.0fMB" % (db / 1048576.0) if db else "0MB"
                if tb:
                    size_txt += "/%.0fMB" % (tb / 1048576.0)
                self.q.put(("pct", num, "下载中 %s %s %s 已下%s | %s"
                            % (pct, speed, eta, size_txt, name[-38:])))
            elif st == "finished":
                self.q.put(("msg", "片段下载完成, 正在用 ffmpeg 合并/转换 ..."))
        except Exception:
            if self._cancel:
                raise            # 停止信号必须向上传递以中止下载
            # 其它钩子异常一律忽略, 绝不让进度回调中断下载

    def _poll_queue(self):
        try:
            while True:
                item = self.q.get_nowait()
                # 兼容任意长度的消息: ("kind", payload) 或 ("kind", a, b) 都支持
                kind = item[0]
                payload = item[1] if len(item) == 2 else item[1:]
                try:
                    self._handle_msg(kind, payload)
                except Exception:
                    import traceback
                    self.log_line("[界面提示] 处理消息异常(不影响下载): " +
                                  traceback.format_exc(limit=2).strip())
        except queue.Empty:
            pass
        except Exception:
            # 队列本身出错也不能让轮询停止
            import traceback
            self.log_line("[界面提示] 队列读取异常(已忽略并继续): " +
                          traceback.format_exc(limit=2).strip())
        try:
            self._watchdog()
        except Exception:
            pass
        # 无论发生什么都保证继续轮询, 界面永不"卡死"
        self.root.after(100, self._poll_queue)

    def _handle_msg(self, kind, payload):
        self._last_activity = time.time()
        if kind == "parsed":
            self._apply_parsed(payload)
        elif kind == "pct":
            num, txt = payload
            if getattr(self, "_merging", False):
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self._merging = False
            self.progress["value"] = min(100.0, num)
            self.pct_var.set("%.0f%%" % num)
            self.status_var.set(txt)
            # 心跳: 每跨越 10% 写一条日志, 便于观察与排障
            hb = int(num // 10)
            if hb != getattr(self, "_last_hb", -1):
                self._last_hb = hb
                self.log_line(txt[:95])
        elif kind == "msg":
            self._merging = True
            self.status_var.set(payload)
            self.pct_var.set("")
            self.log_line(payload)
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        elif kind == "log":
            txt = str(payload).lstrip("\r\n ").strip()
            if txt:
                self.log_line(txt[:400])
        elif kind == "done":
            if self._done_notified:
                return   # 已由文件检测或前次事件通知过, 忽略重复
            self._done_notified = True
            outdir, _audio_only = payload
            if getattr(self, "_merging", False):
                self.progress.stop()
                self._merging = False
            self.progress.configure(mode="determinate")
            self.progress["value"] = 100
            self.pct_var.set("100%")
            self.set_working(False)
            self.status_var.set("完成 ✓")
            newest = self._newest_media(outdir)
            if newest:
                self.log_line("下载完成! 文件: %s (%.1f MB)" % (
                    os.path.basename(newest), os.path.getsize(newest) / 1048576.0))
            else:
                self.log_line("下载完成! 文件保存在: %s" % outdir)
            self.log_line("点上方【打开下载文件夹】可查看文件。")
            self.root.bell()
        elif kind == "stopped":
            self.set_working(False)
            self.status_var.set("已停止")
            self.pct_var.set("")
            if getattr(self, "_merging", False):
                self.progress.stop()
                self._merging = False
            self.progress.configure(mode="determinate")
            self.progress["value"] = 0
            self.log_line(str(payload))
            self.root.bell()
        elif kind == "error":
            msg = payload
            self.log_line(msg)
            self.set_working(False)
            self.status_var.set("出错")
            if getattr(self, "_merging", False):
                self.progress.stop()
                self._merging = False
            self.progress.configure(mode="determinate")
            self.root.bell()
            if "ffmpeg" in msg.lower() and ("找不到" in msg or "not found" in msg.lower() or "postprocess" in msg.lower()):
                messagebox.showerror(
                    "缺少 ffmpeg",
                    "合并 DASH 视频/音频轨需要 ffmpeg。\n\n安装方法(重启终端后生效):\n"
                    "    winget install Gyan.FFmpeg\n或从 https://www.gyan.dev/ffmpeg/builds/ 下载解压并加入 PATH。",
                )
            else:
                messagebox.showerror("出错了", msg)

    def _newest_media(self, d):
        best, bt = None, -1
        try:
            for f in os.listdir(d):
                if f.lower().endswith((".mp4", ".mkv", ".webm", ".mp3", ".m4a")):
                    p = os.path.join(d, f)
                    t = os.path.getmtime(p)
                    if t > bt:
                        bt, best = t, p
        except Exception:
            return None
        return best

    def _new_media_since(self, d, since):
        """返回目录中修改时间晚于 since 的最终媒体文件(不含 .part 中间文件)."""
        best, bt = None, -1.0
        try:
            for f in os.listdir(d):
                low = f.lower()
                if low.endswith((".mp4", ".mkv", ".webm", ".mp3", ".m4a")) and not low.endswith((".part", ".ytdl")):
                    p = os.path.join(d, f)
                    try:
                        t = os.path.getmtime(p)
                    except Exception:
                        continue
                    if t >= since - 2 and t > bt:
                        bt, best = t, p
        except Exception:
            return None
        return best

    def stop_download(self):
        if not self.working:
            return
        self._cancel = True
        self._stop_at = time.time()
        self.log_line("收到停止请求: 已通知下载中止, 界面最多 10 秒后自动恢复。")
        self.status_var.set("正在停止...")

    def _watchdog(self):
        """卡死检测 + 停止兜底 + 文件落盘自动确认完成."""
        if not self.working:
            return
        now = time.time()
        # 0) 工作线程已结束但没收到事件 → 立即自动恢复(最强保险)
        worker = getattr(self, "_worker", None)
        if worker is not None and not worker.is_alive():
            outdir = self.dir_var.get().strip() or self.outdir
            if self._done_notified:
                self.q.put(("stopped", "任务状态已自动复位。"))
            elif self._cancel:
                self.q.put(("stopped", "下载已停止, 界面自动恢复。"))
            else:
                self.q.put(("done", (outdir, False)))
            return
        # 1) 停止后的强制复位宽限期 = 10 秒
        if self._stop_at and (now - self._stop_at > 10):
            self._stop_at = None
            self.q.put(("stopped", "已停止并复位界面(后台线程将在网络超时后自然结束)"))
            return
        # 2) 90 秒无任何进度/日志 → 判定卡死: 自动终止并复位
        if not self._stop_at and (now - self._last_activity) > 90:
            self.log_line("[超时] 90 秒无下载进度, 判定为卡死: 自动终止本次下载并复位界面。")
            self.log_line("       若反复出现多为站点风控/网络问题, 可改用 Cookie 文件方式。")
            self._cancel = True
            self._stop_at = now - 9   # 1 秒后走上面的强制复位分支
            return
        # 3) 60 秒无进展的提醒(尚未到自动终止)
        if not self._stop_at and (now - self._last_activity) > 60 and not self._hint_at:
            self._hint_at = now
            self.log_line("[提示] 已 60 秒无下载进度 —— 90 秒将自动终止, 或点【■ 停止】。")
        # 4) 文件落盘检测(最后一道保险, 正常情况下用不到)
        if (not self._stop_at and (now - self._last_activity) > 15
                and (now - self._dl_start) > 20):
            try:
                outdir = self.dir_var.get().strip() or self.outdir
                newest = self._new_media_since(outdir, self._dl_start)
            except Exception:
                newest = None
            if newest:
                key = (newest, os.path.getsize(newest))
                if self._watch_seen == key and (now - getattr(self, "_watch_t", 0)) >= 3:
                    self.log_line("检测到文件已生成, 自动刷新为完成状态: %s" % os.path.basename(newest))
                    self.q.put(("done", (outdir, False)))
                    self._watch_seen = None
                else:
                    self._watch_seen = key
                    self._watch_t = now
            else:
                self._watch_seen = None


def main():
    # 高DPI 下界面文字更清晰
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    try:
        import yt_dlp  # noqa
    except ImportError:
        messagebox.showerror(
            "缺少 yt-dlp",
            "未检测到 yt-dlp。\n\n请先在命令提示符中执行:\n"
            "    python -m pip install -U yt-dlp\n\n然后重新打开本程序。\n"
            "(若 ffmpeg 也未安装:  winget install Gyan.FFmpeg )",
        )
        root.destroy()
        return 1
    DashApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
