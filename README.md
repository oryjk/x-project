# x-project

为自己的 X 与小红书账号收集近期 AI/科技信息、用专业中文整理，并通过已配对的 Android 手机发布。支持多手机、多个账号集中管理；小红书首版提供单封面图文适配，尚未完成真机验证。

单账号 X 默认每小时 3 条，小时内随机分散安排，跨小时也至少间隔 8 分钟。多账号分别配置频率，新增账号默认停用，小红书默认额度为每小时1条、间隔30分钟。**到发送时段才现场检索、核验和整理，随后立即发送**，不会提前囤稿。计划时间是采集开始时间，实际发出会晚几分钟。每次最多研究3个候选，只保存本次选中的1条，自动发布明确使用该新id；旧队列不会用于自动补位。

时刻写入 SQLite，重启不重新洗牌；研究与提交最多10分钟且不跨小时，最后一个时段最迟安排在第50分钟。错过时段不补发。外部网络/手机故障或没有通过核验的内容时仍可能少于3条，失败原因保留在执行记录，不能靠编造和重复发送凑数。

## 运行

需要 Linux（启用 Landlock）、Python 3.11+、ADB，以及已登录且支持网页搜索的 Codex CLI。Python 部分没有第三方必装依赖。

下载或克隆项目后，只需配置 `.env`，不必创建 `config.toml`。以下命令在项目根目录执行；目录可放在任意位置：

```bash
cd x-project
rtk cp .env.example .env
rtk chmod 600 .env
# 在本机编辑 .env，填写账号、ADB 路径和无线调试地址/端口
```

在手机上打开无线调试的“使用配对码配对设备”，将弹窗中的配对端口、六位配对码填到 `.env`，保持弹窗打开再执行：

```bash
# 首次配对；已经配对的设备可跳过
rtk python3 x.py pair
# 连接手机并显示物理设备序列号；请核对后填入 .env 的 X_DEVICE_ID
rtk python3 x.py connect
# 填好 X_DEVICE_ID 及所需的锁屏设置后，检查部署
rtk python3 x.py doctor
# 明确启用正式发布
rtk python3 x.py enable --publish
rtk python3 x.py status
```

配对完成后可清空 `X_ADB_PAIR_CODE`；定时任务不使用也不保存该临时码。连接端口取自无线调试主页面，配对端口取自配对弹窗，两者通常不同。USB 连接可改用 `X_ADB_SERIAL`，无需填写无线地址或配对信息。

系统环境变量优先于 `.env`，其次是可选的旧 `config.toml`，最后使用默认值。项目目录由程序入口确定，从其他工作目录运行也无需改路径；`--root` 可显式选择配置目录。`.env` 自动读取，不需要 `source`，不会执行变量插值。默认关闭自动解锁和自动发布。

常用配置：

| 环境变量 | 用途 |
| --- | --- |
| `X_ACCOUNT` | 自己的 X 用户名 |
| `X_ADB_PATH` / `X_CODEX_PATH` | 程序名或部署机器上的绝对路径，默认 `adb` / `codex` |
| `X_ADB_HOST` / `X_ADB_PORT` | 无线调试连接地址和端口 |
| `X_ADB_SERIAL` | 完整连接地址或 USB 序列号；替代 HOST/PORT |
| `X_ADB_PAIR_HOST` / `X_ADB_PAIR_PORT` / `X_ADB_PAIR_CODE` | 首次配对地址、端口和临时六位配对码 |
| `X_DEVICE_ID` | 经人工核对的物理设备序列号 |
| `X_ALLOW_UNLOCK` / `X_UNLOCK_METHOD` | 是否允许解锁，以及 `pin` / `pattern` |
| `X_PHONE_PIN` / `X_PHONE_PATTERN` | 对应方式的锁屏凭据，只填一种 |
| `X_PERSONA` | 可选的中文整理口吻 |
| `CODEX_HOME`、代理环境变量 | 可选的登录目录与网络设置 |

手动文案支持 UTF-8 文件、中文及换行：

```bash
rtk python3 x.py preview --file post.txt
rtk python3 x.py post --file post.txt --publish
```

`preview --file` 不入自动发布队列，也不点击发布。`post` 未带 `--publish` 时同样只预览。`collect --count 3` 是人工研究/入队命令，队列可用 `queue` 查看、以 `preview --id` 预览或 `post --id --publish` 手动发送。自动流程到时独立采集，不消费这类预存草稿。

## 多手机、多账号与小红书

单账号可继续使用 `.env.example`；多账号参照 `multi.env.example`，将手机连接/解锁配置与发布账号配置分别填写在同一 `.env` 中。账号通过 `DEVICE` 关联手机，通过 `PLATFORM=x` 或 `xiaohongshu` 选择发布端。各平台账号只维护用户自己授权的账号；同一手机不自动切换同一平台登录账号。

```bash
rtk python3 x.py profiles
rtk python3 x.py --profile main_x pair
rtk python3 x.py --profile main_x connect
rtk python3 x.py --all status
rtk python3 x.py --profile main_x doctor
rtk python3 x.py --profile main_x enable --publish
# 每个新增账号分别显式启用；也可用 --all enable --publish 一次启用已配置账号
rtk python3 x.py --profile main_red enable --publish
# 单独停止一个账号，其他账号继续使用共享定时器
rtk python3 x.py --profile main_red disable
# 紧急停止全部账号，即使本机配置已丢失也可执行
rtk python3 x.py --all disable
```

手机凭据使用 `X_DEVICE_手机名_...` 前缀，账号配置使用 `X_PROFILE_账号名_...` 前缀；名称为小写字母、数字、下划线，变量名中转成大写。设备的 `DEVICE_ID` 仍须按 `connect` 返回值人工核对填写，账号的 `ACCOUNT` 必须与应用当前登录身份一致。小红书填写稳定的“小红书号”，昵称不能作为身份依据。配对码只用于该设备的显式 `pair`，不保存到定时运行环境，也不传入研究模型。

各账号独立保存队列、去重、随机时段、发布配额与未知提交；同一物理手机共享界面操作锁和解锁失败保护。某账号离线或结果为 unknown 时，其他账号继续执行。账号配置名称改变不重置其配额。同一平台账号不能重复注册。

已有单账号部署在原 `.env` 中追加配置并保留旧全局账号和设备绑定字段，首次运行 `profiles` 后，程序把旧状态归属写入 `.state/namespaces.json`。随后清理旧全局字段仍保留原队列、未知提交、限额和解锁保护。运行中的状态文件应随部署保留。

`HOURLY_LIMIT` 可为0–3（0停用），`MIN_INTERVAL_MINUTES` 为8–60，且组合必须能在每小时第4至50分钟安排。调低额度会取消多余的未领取时段；提高额度不补本小时错过的时段，下小时按新额度安排。实际点击发布前仍重新核对当前账号额度、间隔及有效期。

小红书图文保存独立标题、正文、来源与封面；保守限制标题20字、正文含来源1000字。当前 ADB 分享适配支持一张本机 PNG/JPEG 封面，多图明确拒绝。通过账号 `IMAGES` 指定图片路径JSON数组，或在人工预览/发送时提供图片：

```bash
rtk python3 x.py --profile main_red preview --file note.txt --title "这项技术解决了什么" --image cover.png
```

自动采集的图文若未配置配图，会用已核验的标题、正文和来源生成文字卡片。封面依赖可选 Pillow 与本机中文字体；用户提供有效图片时无需 Pillow：

```bash
rtk python3 -m pip install -e '.[cards]'
```

可通过账号 `FONT_PATH` 指定中文字体。生成图片只保存在账号的 `.state` 下，不抓取搬运网页图片。手动文案须提供本机封面。

小红书发布端通过标准 Android 图片分享和可见界面操作；应用可能忽略标题/正文分享信息，或不暴露稳定账号及媒体数量。任何核验失败都停止，不猜坐标、不盲输。只有本次发送现场导入且文件及草稿未变的封面才允许提交；已打开编辑器仅可预览核验，需自行退出或清理本人草稿后才能重新发起发送。成功仅依据可识别的发布成功回执，否则记 unknown 并冻结该账号。小红书目前仅通过合成界面与模拟 ADB 验证；在用户配置的目标真机完成预览适配前，不能视为可无人值守运行。[官方分享文档](https://agora.xiaohongshu.com/doc/android)

## 单账号手机与密码

`.env.example` 包含完整配置说明。已有的 `config.toml` 仍兼容，但新部署无需复制它。ADB 默认从 `PATH` 查找，也可在 `X_ADB_PATH` 填写绝对路径；Codex 同理。`connect` 显示的物理序列号对应 `adb shell getprop ro.serialno`，连接地址变化后也必须匹配同一设备身份，防止连错手机。

通过 `X_UNLOCK_METHOD` 明确选择 `pin` 或 `pattern`，并在需要自动解锁时设置 `X_ALLOW_UNLOCK=true`。本机 `.env` 或系统环境变量的 PIN 使用 `X_PHONE_PIN=...`（4–16 位数字）；图案使用 `X_PHONE_PATTERN=...`（4–9 个不重复点位）。两种方式均兼容 `pwd=...`。图案九宫格从左到右、从上到下编号 1–9，保存实际连线经过的点位顺序；不要发送到聊天。文件权限必须为 `0600`，不执行 shell 插值。`.env`、`config.toml`、`.state/` 已忽略，密码和配对码不传给内容生成进程，不进入命令行或日志。

```bash
rtk chmod 600 .env
rtk python3 x.py unlock-test
# 仅当确认本机凭据正确、并人工核对手机后解除一次失败保护：
rtk python3 x.py unlock-reset
```

解锁只尝试一次，失败即持久化阻止后续尝试。不会绕过手机锁屏。OPlus 的受保护密码界面不可读取时，校验当前系统密码面板显示信号后才输入。图案解锁须识别实际系统图案区域，按该区域计算点位，用一条连续触摸轨迹完成，不对全屏父容器猜坐标。自动唤醒的手机在任务结束后恢复息屏；若本来亮屏则保持亮屏。执行期间临时延长息屏时间，结束恢复原值。

发布前核对绑定账号、编辑框全文、唯一发布按钮及其父节点的可用状态。已有不同草稿时停止，避免覆盖。布局变化、断网、账号不符时跳过，不猜固定发布坐标。

## 自动运行

```bash
# 开启采集与正式自动发布：
rtk python3 x.py enable --publish
rtk python3 x.py status
rtk systemctl --user status x-project.timer
# 停止：
rtk python3 x.py disable
```

安装用户级 `x-project.timer`，每分钟检查持久化随机计划，到期时才检索、整理并发送一条。未到期只检查计划，每个时段进行一次研究（最多3个候选、仅采用1条）；研究提前完成但实际8分钟发送间隔不足时，在本窗口内等待，并持续检查取消，不会每分钟调用模型；存在未核对的 unknown 提交时停止采集和发布。查看记录：`.state/events.jsonl`；检索次数及状态：`.state/last-research.json`，核验拒绝原因：`.state/last-collect.json`；查看队列：`x.py queue`。

启停开关保存在 `.state/`，无需改写 `.env` 或 `config.toml`；`disable` 的持久化停止状态优先于配置中的发布标志。定时服务保留启用时的 `PATH`、Codex 登录目录、代理和已导出的项目变量；导出的锁屏凭据保存在权限为 `0600` 的本机运行文件中，不含临时配对码。`.env` 在每次执行时重新读取；如修改了已导出的环境变量或安装路径，请重新执行 `enable` 更新服务。

无线调试重启后端口可能变化；更新 `.env` 的连接端口后运行 `connect`，只有配对失效时才重新填写临时配对信息并运行 `pair`。电脑必须运行，手机须可达，账号保持登录。用户级任务是否能在退出登录后继续运行取决于宿主机的 user manager/linger 设置。

文章读取保留原始URL（含路径末尾斜杠），仅去重使用规范化URL；核对标准发布时间元数据、GitHub relative-time 或新闻稿明确标注的发布日期，不能用版权年份代替。

点击发布前按实际时间重新检查每小时3条、8分钟间隔与计划有效期，再记录 `submitting`。优先读取可识别的 X Toast/通知控件回执；短暂提示错过时打开绑定账号主页，要求提交前不存在相同正文，提交后在同一帖子容器核对作者、完整正文和本次提交窗口内的时间。不使用跨帖文字拼接；中断或结果不确定记为 `unknown`，冻结后续自动发布，避免重复：

```bash
rtk python3 x.py resolve --id 1 --sent
# 只在确认 X 上没有该帖后才允许重新排队：
rtk python3 x.py resolve --id 1 --not-sent
```

## 内容与视频

实时检索使用已有 Codex 登录的非交互、网页搜索和结构化输出，模型只负责研究与写作，不能控制发布。研究子进程通过 Linux Landlock 文件读取白名单，项目文件及 `.env` 不在可读范围；单元测试在真实受限子进程中验证模拟秘密文件无法读取。只提供系统运行库、研究临时目录及必要的 Codex 认证/程序目录，环境变量采用白名单，禁用模型 shell、浏览器控制、插件与子代理，关闭共享 daemon。隔离不可用时停止生成，不降级为不受限模式。搜索所依赖的 Code Mode host 必须启用，但不会开放模型本地 shell 或手机控制。读取 JSON 执行事件核对实际搜索并记录模型原文打开次数；候选必须再由程序逐个抓取所有引用原文，独立核对发布日期和逐字证据后才能入库。模型没有执行打开动作不会直接丢弃整个研究批次，原文抓取或核验失败的候选仍拒绝采用。工具错误、没有实际检索都明确报告 research-unavailable，不再当作无新闻。调用消耗当前模型账户的使用额度。

候选优先近期原始公告、论文、开源发布和作者原帖。每条保存可逐字核对的短证据；正文表达事实、自己的判断及实际意义，不冒充亲测或虚构履历。核对来源链接、可读证据、原始发布日期、时效和长度，再按来源/事件/正文去重。X 原帖日期独立按 status ID 核对，YouTube 与文章核对原始发布时间元数据；无法证明日期则不作为近期新闻。仅有日期时保持日期粒度，不编造具体发布时间。过期/不合格草稿退休，不会永久堵塞队首。

视频分享 X/YouTube 原链接并保留作者来源。能读取字幕才总结视频，仅读到简介则只据简介；只有标题时必须配文字来源，不假装看完视频。YouTube 字幕读取可选安装 `yt-dlp`，未安装或字幕不可用时拒绝该种候选，仍可使用原视频标题作为辅助引用。不会下载或重传视频本体。

X 当前规则限制非 API 自动化，并禁止重复、垃圾内容及自动追逐趋势榜；本项目按用户要求提供手机界面适配，但该方式存在账号受限风险。长期运行优先替换为官方 API 发布端。[X 自动化规则](https://help.x.com/en/rules-and-policies/x-automation) · [Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)

## 测试与边界

```bash
rtk env PYTHONPATH=src python3 -m unittest discover -s tests -v
```

覆盖中文/换行及 shell 字符、账号/全文/按钮校验、单次解锁保护、来源证据与日期、持久化随机计划、小时上限、并发事务和提交崩溃后的防重复。真实发布与主页核验的设备验证结果及当前运行状态见 `CHANGELOG.md`。
