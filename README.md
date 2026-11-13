# x-project

为自己的 X 账号收集近期 AI/科技信息、用专业中文整理，并通过已配对的 Android 手机发布。

目标每小时 3 条，小时内随机分散安排，跨小时也至少间隔 8 分钟。**到发送时段才现场检索、核验和整理，随后立即发送**，不会提前囤稿。计划时间是采集开始时间，实际发出会晚几分钟。每次最多研究3个候选，只保存本次选中的1条，自动发布明确使用该新id；旧队列不会用于自动补位。

时刻写入 SQLite，重启不重新洗牌；研究与提交最多10分钟且不跨小时，最后一个时段最迟安排在第50分钟。错过时段不补发。外部网络/手机故障或没有通过核验的内容时仍可能少于3条，失败原因保留在执行记录，不能靠编造和重复发送凑数。

## 运行

需要 Linux（启用 Landlock）、Python 3.11+、ADB，以及已登录且支持网页搜索的 Codex CLI。Python 部分没有第三方必装依赖。

```bash
cd /home/betalpha/projects/x-project
rtk python3 x.py doctor
rtk python3 x.py enable --publish
rtk python3 x.py status
```

手动文案支持 UTF-8 文件、中文及换行：

```bash
rtk python3 x.py preview --file post.txt
rtk python3 x.py post --file post.txt --publish
```

`preview --file` 不入自动发布队列，也不点击发布。`post` 未带 `--publish` 时同样只预览。`collect --count 3` 是人工研究/入队命令，队列可用 `queue` 查看、以 `preview --id` 预览或 `post --id --publish` 手动发送。自动流程到时独立采集，不消费这类预存草稿。

## 手机与密码

复制 `config.example.toml` 为本机 `config.toml`，配置无线调试连接地址、设备序列号及 X 用户名。设备序列号取自 `adb -s IP:PORT shell getprop ro.serialno`，地址变化后也必须匹配同一设备身份，防止连错手机。

通过 `unlock_method` 明确选择 `pin` 或 `pattern`。本机 `.env` 的 PIN 使用 `X_PHONE_PIN=...`（4–16 位数字）；图案使用 `X_PHONE_PATTERN=...`（4–9 个不重复点位）。两种方式均兼容 `pwd=...`。图案九宫格从左到右、从上到下编号 1–9，保存实际连线经过的点位顺序；不要发送到聊天。文件权限必须为 `0600`，不执行 shell 插值。`.env`、`config.toml`、`.state/` 已忽略，密码不传给内容生成进程，不进入命令行或日志。

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

无线调试重启后端口可能变化；用 `adb pair` / `adb connect` 重新连接并更新本机配置。电脑必须运行，手机须可达，账号保持登录。用户级任务是否能在退出登录后继续运行取决于宿主机的 user manager/linger 设置。

文章读取保留原始URL（含路径末尾斜杠），仅去重使用规范化URL；核对标准发布时间元数据、GitHub relative-time 或新闻稿明确标注的发布日期，不能用版权年份代替。

点击发布前按实际时间重新检查每小时3条、8分钟间隔与计划有效期，再记录 `submitting`。优先读取可识别的 X Toast/通知控件回执；短暂提示错过时打开绑定账号主页，要求提交前不存在相同正文，提交后在同一帖子容器核对作者、完整正文和本次提交窗口内的时间。不使用跨帖文字拼接；中断或结果不确定记为 `unknown`，冻结后续自动发布，避免重复：

```bash
rtk python3 x.py resolve --id 1 --sent
# 只在确认 X 上没有该帖后才允许重新排队：
rtk python3 x.py resolve --id 1 --not-sent
```

## 内容与视频

实时检索使用已有 Codex 登录的非交互、网页搜索和结构化输出，模型只负责研究与写作，不能控制发布。研究子进程通过 Linux Landlock 文件读取白名单，项目文件及 `.env` 不在可读范围；单元测试在真实受限子进程中验证模拟秘密文件无法读取。只提供系统运行库、研究临时目录及必要的 Codex 认证/程序目录，环境变量采用白名单，禁用模型 shell、浏览器控制、插件与子代理，关闭共享 daemon。隔离不可用时停止生成，不降级为不受限模式。搜索所依赖的 Code Mode host 必须启用，但不会开放模型本地 shell 或手机控制。读取 JSON 执行事件核对实际搜索/原文打开；工具错误、没有实际检索都明确报告 research-unavailable，不再当作无新闻。调用消耗当前模型账户的使用额度。

候选优先近期原始公告、论文、开源发布和作者原帖。每条保存可逐字核对的短证据；正文表达事实、自己的判断及实际意义，不冒充亲测或虚构履历。核对来源链接、可读证据、原始发布日期、时效和长度，再按来源/事件/正文去重。X 原帖日期独立按 status ID 核对，YouTube 与文章核对原始发布时间元数据；无法证明日期则不作为近期新闻。仅有日期时保持日期粒度，不编造具体发布时间。过期/不合格草稿退休，不会永久堵塞队首。

视频分享 X/YouTube 原链接并保留作者来源。能读取字幕才总结视频，仅读到简介则只据简介；只有标题时必须配文字来源，不假装看完视频。YouTube 字幕读取可选安装 `yt-dlp`，未安装或字幕不可用时拒绝该种候选，仍可使用原视频标题作为辅助引用。不会下载或重传视频本体。

X 当前规则限制非 API 自动化，并禁止重复、垃圾内容及自动追逐趋势榜；本项目按用户要求提供手机界面适配，但该方式存在账号受限风险。长期运行优先替换为官方 API 发布端。[X 自动化规则](https://help.x.com/en/rules-and-policies/x-automation) · [Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)

## 测试与边界

```bash
rtk env PYTHONPATH=src python3 -m unittest discover -s tests -v
```

覆盖中文/换行及 shell 字符、账号/全文/按钮校验、单次解锁保护、来源证据与日期、持久化随机计划、小时上限、并发事务和提交崩溃后的防重复。真实发布与主页核验的设备验证结果及当前运行状态见 `CHANGELOG.md`。
