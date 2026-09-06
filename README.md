# DASH 视频下载器（DASH Video Grabber）

> ## ⚠️ 重要声明（Disclaimer）
>
> 1. 本项目**仅供个人学习、技术交流与二次创作素材收集**使用，严禁用于盗取他人视频、
>    未授权搬运、商业侵权等任何违法行为。
> 2. 下载内容的**版权归原作者/平台所有**，请遵守目标平台的服务条款与当地法律法规。
> 3. 请勿使用本项目绕过付费墙、DRM 或任何访问控制。
> 4. 作者不对任何人的滥用行为负责，使用者需自行承担全部责任。
> 5. 若你是内容创作者且不希望你的作品被本工具下载，请联系平台处理访问控制。

一个基于 [yt-dlp](https://github.com/yt-dlp/yt-dlp)（Unlicense）封装的 **DASH/HLS 流媒体下载工具**，提供中文图形界面（tkinter）与命令行两种用法，用于网页视频素材的抓取与高清二次创作。

> ⚠️ 请仅下载你有权使用的视频内容，遵守目标平台服务条款与版权法规。

一个用于**网页刷视频、抓取高清素材做二次剪辑**的 DASH/HLS 流媒体下载工具，支持中文图形界面与命令行两种用法。

## 功能一览

- **DASH 流**（.mpd 清单，如 YouTube / B站高清多码率）与 **HLS 流**（.m3u8）均可下载
- 自动分离下载**最高画质视频轨 + 最佳音频轨**，用 ffmpeg 无损合并为单个 mp4
- 支持 Bilibili / YouTube / 抖音 / TikTok / 微博 / Twitter(X) / Reddit 等 2000+ 网站
- 支持**借用浏览器登录态**：可获取需登录/会员才可见的清晰度（如 B站大会员 4K）
- **通用流兜底**：网页能播但工具不认识的网站，用浏览器 F12 抓到真实流地址直接下
- 可只提取音频转 MP3（320k），方便做配音/背景音素材

> 支持的网站完整名单：https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md

## 文件说明

| 文件 | 说明 |
|---|---|
| dash_gui.py | 图形界面（推荐日常使用） |
| dash_dl.py | 命令行引擎（脚本、批量场景） |
| start_gui.bat | 双击一键启动图形界面 |
| downloads/ | 默认下载保存目录（可改） |

> **下载位置说明**：默认保存到脚本所在目录的 `downloads` 文件夹（不占用系统盘空间），
> 可在界面点【选择文件夹】改到任意位置。
> 已下载文件均经 Windows Defender 扫描无威胁；请勿下载受版权保护内容用于商业用途。

## 安装（分 3 步）

### 第 1 步：安装 Python 3（3.10~3.13 均可，建议 3.12/3.13）

1. 官网下载：https://www.python.org/downloads/
2. 安装时**务必勾选“Add python.exe to PATH”**（加入环境变量）
3. 装完后新开一个 PowerShell 验证：python --version 能输出版本号即可

> 也可以直接到微软商店搜“Python”安装，商店版同样可用。
> 注意：装完需**新开**一个终端窗口，旧窗口不识别新 PATH。

### 第 2 步：安装 yt-dlp（核心下载引擎）

在 PowerShell / cmd 中执行：

~~~
python -m pip install -U yt-dlp
~~~

> 国内网络建议加镜像：python -m pip install -U yt-dlp -i https://pypi.tuna.tsinghua.edu.cn/simple
> 升级也用同一条命令（网站改版后需要保持 yt-dlp 最新）。

### 第 3 步：安装 ffmpeg（合并 DASH 视频轨/音频轨必需）

方式一（推荐，需 Win10 1709+）：

~~~
winget install Gyan.FFmpeg
~~~

方式二：https://www.gyan.dev/ffmpeg/builds/ 下载“release essentials”压缩包，
解压后把 bin 文件夹路径加入系统 PATH。

> 装完**重启终端**验证：ffmpeg -version

> **免装 ffmpeg**：本工具内置自动方案——系统找不到 ffmpeg 时会自动使用
> imageio-ffmpeg（`python -m pip install imageio-ffmpeg`）内置的 ffmpeg 完成合并。
> 需要 ffprobe 等完整功能时再运行 winget install Gyan.FFmpeg。

## 图形界面用法

1. 双击 start_gui.bat（或运行 python dash_gui.py）
2. 粘贴视频链接 → 点【解析画质】
3. 列表出现所有清晰度/格式（1080p/4K 纯视频轨、音频轨、完整轨等）
4. 直接点【★ 下载最佳画质】即可（自动选最高视频轨 + 最佳音频轨合并）
5. 也可选中某行点【下载选中画质】；【仅提取音频 MP3】单独下音频
6. 完成后自动提示并打开文件夹

**两个关键选项：**

- “借用浏览器登录态”下拉框：选你**已登录**的浏览器（edge/chrome/firefox…），
  即可解析/下载会员或登录才可见的清晰度（B站 1080P60/4K、YouTube 高码率等）。
- “保存到”：点右侧【选择文件夹】改下载目录。

## 命令行用法

~~~
python dash_dl.py "https://www.bilibili.com/video/BVxxxx"   # 交互式选画质
python dash_dl.py "视频URL" --list                          # 只看有哪些画质
python dash_dl.py "视频URL" --best                          # 直接最佳画质+合并
python dash_dl.py "视频URL" --audio-mp3                     # 只提取 MP3 音频
python dash_dl.py "视频URL" -f 137+140                      # 指定 yt-dlp 格式ID
python dash_dl.py "视频URL" --cookies edge                  # 借用 Edge 登录态
python dash_dl.py "视频URL" -o "D:/素材"                   # 改保存目录
python dash_dl.py "视频URL" --playlist                      # 下载整个合集/播放列表
~~~

## 🎫 下载高清/会员清晰度：用 Cookie 文件（推荐，浏览器可开着）

界面勾选「**使用Cookie文件**」→【选择Cookie文件】→ 之后解析/下载自动带上登录态，
**不需要关闭浏览器**，B站 1080P60/4K 高码率、YouTube 登录清晰度都能拿到。

1. 在 Edge/Chrome 安装扩展 **Get cookies.txt LOCALLY**（作者 kairi003，GitHub 仓库
   `kairi003/Get-cookies.txt-LOCALLY`；Edge 扩展商店搜不到时：去 GitHub 下载源码 →
   `edge://extensions/` 打开"开发人员模式" → "加载解压缩的扩展" → 选中源码文件夹）
   > 该扩展全部在本地处理，不会把 Cookie 上传外网。
2. 登录目标网站（如 bilibili.com）→ 点扩展图标 → Export → 保存为 `cookies.txt`
3. GUI 勾选「使用Cookie文件」→ 选择该文件 → 粘贴链接 → 解析/下载

> 备用方式：GUI 下拉框「借用浏览器」选 edge/chrome（**需完全关闭该浏览器**）。
> Cookie 文件下载的内容也请遵守平台条款与版权规定。

## 平台速查

| 平台 | 说明 |
|---|---|
| Bilibili | 未登录默认仅 480P/720P；要 1080P60+/4K/高码率必须登录。先登录 B站网页版，再借用 Edge/Chrome 登录态解析 |
| YouTube | DASH 支持最完善。借用登录态可获登录后高码率与会员视频 |
| 抖音 / TikTok | 直接下载即无水印；保持 yt-dlp 为最新版以适配改版 |
| 其他小网站 | 用下方“浏览器抓流兜底”方案 |

## 浏览器抓流兜底（网页能播 → 就能下）

对 yt-dlp 还不认识 / 反爬强的网页：

1. 在浏览器打开视频并**开始播放**
2. 按 F12 打开开发者工具 → 切到 Network（网络）标签
3. 过滤框输入 .mpd 或 .m3u8，刷新页面重新播放
4. 在结果中找 URL 以 .mpd / .m3u8 结尾的请求（一般选看起来最大/带 source 的那个）
5. 右键 → Copy → Copy URL
6. 把该地址直接粘贴进 GUI 或命令行即可下载

> 提示：部分页面用 DRM 加密（Netflix 类），这类**无法**用本工具下载，属正常技术限制。
> 复制到的地址若带 token 且很快过期，就重新播放再复制一次。

## 反爬与报错 FAQ

- **403 / Forbidden**：一般缺登录态 → 用“借用浏览器登录态”重试。
- **需要的清晰度不出现**：先登录该网站网页版，再借用 Cookie 重新解析。
- **下载后没声音/没画面**：DASH 分离轨需 ffmpeg 合并 → 检查 ffmpeg 是否装好。
- **ffmpeg not found**：安装 ffmpeg 后重启终端再试。
- **提示 yt-dlp 过期/失效**：网站改版后旧版会失效，运行 python -m pip install -U yt-dlp。
- **反爬强被拦截**：先借用浏览器登录态；仍不行就浏览器抓流兜底。
- **报错信息很重要**：GUI 底部日志 / CLI 终端输出直接写着原因，可截图问我。

## 使用提醒（再读一遍）

- 本工具**仅用于个人学习、素材收集与二次创作**，请勿用于盗视频、未授权搬运或商业侵权。
- 下载内容的版权归原作者/平台所有，请遵守目标平台服务条款与法律法规。
- 请勿用于绕过付费墙、DRM 或访问控制；DRM 加密内容无法下载属正常技术限制。
- 若因不当使用产生任何纠纷，责任由使用者自行承担，与项目作者无关。

祝剪辑顺利 🎬