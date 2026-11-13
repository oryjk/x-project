# 多手机、多账号与小红书图文

用户要求所有开发在 main，采用同一项目统一管理，直接实施；小红书首版图文，每个账号独立配置频率，新增账号默认停用。

## 配置与隔离
- 保持无 X_PROFILES 时现有单手机 X 配置和运行状态不变。
- X_DEVICES=phone_a,phone_b；设备变量 X_DEVICE_PHONE_A_ADB_HOST/ADB_PORT/ADB_SERIAL/DEVICE_ID/ALLOW_UNLOCK/UNLOCK_METHOD/PHONE_PIN/PHONE_PATTERN/ADB_PAIR_HOST/ADB_PAIR_PORT/ADB_PAIR_CODE。
- X_PROFILES=work_x,work_xhs；账号变量 X_PROFILE_WORK_X_PLATFORM/DEVICE/ACCOUNT/ENABLED/HOURLY_LIMIT/MIN_INTERVAL_MINUTES/PERSONA/IMAGES/FONT_PATH。
- 平台 x 或 xiaohongshu。新账号 ENABLED 默认 false，X 默认3条/小时、8分钟；小红书默认1条/小时、30分钟。通过显式 enable --publish 或明确启用配置后才运行，默认不自动发布。频率可降至0停用，最多每小时3条、最少8分钟；先保持现有可靠时间窗与核验约束，不扩张更高发布速率。
- --profile NAME 定向操作，--all 汇总/调度/启停，profiles 列出配置。手机配对命令通过账号选择关联设备。
- 状态按平台和账号隔离，同一账号重复配置拒绝；同一手机的 UI 操作共用互斥锁与解锁失败保护。账号重命名不重置配额。对应旧 X_ACCOUNT 的账号延续旧队列、去重和 unknown 状态。
- 手机密码仅 Android 边界按设备变量名读取；模型环境仍为严格白名单，定时运行环境不保存任何设备的临时配对码。

## 发布与内容
- 各账号到自己的时段才现场检索，最多3候选只采用1条，不从旧队列补位。单账号 unknown 不影响其他账号；离线/解锁失败分别记录，聚合运行继续。
- X 保持既有文字发布适配；小红书独立 XiaohongshuPoster，使用 ADB+可见界面，不使用私有签名 API。
- Draft 向后兼容新增 title:str、images:list[str]、platform:str（默认x），旧数据自动补默认值。小红书生成独立标题/正文，保留来源链接与核验，不把 X 的280预算套给图文。
- 本机图片通过 --image 或账号 IMAGES（JSON数组）提供；没有配图时可用可选 Pillow 与本机中文字体生成文字卡片封面，内容来自已核验的标题/正文/来源，不生成虚假截图或亲测。图片仅保存在.state，拒绝不存在或非PNG/JPEG文件；不下载搬运网络图片。
- 小红书数据结构保守限制标题20字、正文含来源不超过1000字、最多9张图片；当前可验证ADB发布路径仅支持单封面，传入多图明确拒绝。发布前核对绑定账号、标题、正文、图片数量、唯一可用发布按钮；只能提交本次现场导入且摘要核对未变的封面，已有编辑器只供预览。界面无法识别或注入内容不完整时停止，不猜坐标。
- 同一次提交前持久化 submitting，失败/不明回执一律 unknown，禁止自动重复；模型永远不能控制手机。
- 新平台、新账号不自动公开发测试内容。使用合成UI测试，真机适配只做preview且清理本次测试草稿；未有对应真机配置时明确标注未验证。

## 方案与界限
选择统一设备注册+账号配置+平台适配器；独立复制项目会分散配置与升级，单一共享队列会混淆限额和回执，均不采用。实现不包含视频上传、自动切换同一平台登录账号、下载安装键盘或小红书APK。

## 固定接口
- Config.load(root, profile=None), Config.load_all(root)；新增 platform, profile, device_name, credential_prefix, hourly_limit, min_interval_minutes, images, font_path；state 按账号，device_state 按物理设备。
- PhonePoster 保持原构造并增加 keyword credential_prefix，session共享设备锁。
- XiaohongshuPoster(PhonePoster): prepare_note(draft), send_note(draft,before_submit)，返回同现有 sent/unknown 协议；纯文字 prepare/send 对该平台拒绝。
- ensure_note_images(draft,state_dir,font_path='') -> Draft，Pillow可选，无渲染依赖/中文字体则清楚拒绝，不降级为空图片。
- CodexResearcher(..., platform='x')，generate 仍使用原有签名；schema/prompt按平台选择。

## 验证
配置映射、凭据隔离、跨账号队列与配额、设备互斥、旧状态兼容、离线/unknown隔离、频率变更、小红书账号/全文/媒体/按钮与回执、卡片生成、全量回归；不动真实手机。
