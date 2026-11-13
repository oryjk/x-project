# Environment Configuration Implementation Plan

> 执行方式：用户要求直接实施；在当前聊天内执行并测试。

**Goal:** 下载后只配置环境变量即可运行，兼容原有部署。
**Architecture:** 独立配置模块负责默认值、TOML/.env/进程变量合并与私有运行开关；Android 边界独立读取凭据，CLI 提供显式配对与连接。
**Tech Stack:** Python 3.11+ 标准库、unittest、ADB、systemd。
**Spec:** `docs/superpowers/specs/2026-11-13-environment-config-design.md`

## Global Constraints
- 不读取真实手机凭据到聊天，不操作真机、不发布内容。
- 研究进程的环境白名单和 Landlock 保持有效；配对码不出现在 argv 或日志。
- 所有 shell 命令使用 RTK；保留已有本机配置与未提交文档修改。

## Review Focus
- 空值/错误端口/错误布尔值不隐式开启发布。
- 新下载目录没有 TOML 也能启动；环境覆盖不破坏停止开关。
- 导出变量启动的定时任务获得必要配置但不保存临时配对码。
- PIN/pattern/配对码不会进入研究子进程或 ADB 错误输出。
- 配对完成不等于绑定成功；发布仍必须校验 device_id。

### Task 1: 配置加载
- [x] 为 `.env` / 纯环境 / TOML 优先级、校验和运行开关先写测试并确认失败。
- [x] 新建 `src/x_project/config.py`，由 pipeline 复用 Config；保留既有构造兼容。
- [x] 运行配置测试及完整测试。

### Task 2: Android 与 CLI 部署
- [x] 为配对标准输入、不泄露错误、凭据环境覆盖、无 TOML 启停、定时配置传递写失败测试。
- [x] 实现 `pair` / `connect`、凭据读取、私有运行状态、定时环境传递，补充只读依赖检查。
- [x] 运行完整测试及 systemd 单元验证。

### Task 3: 交付
- [x] 更新 `.env.example`、README、FEATURES、CHANGELOG 与项目约束说明。
- [x] 使用临时目录和模拟 ADB 验证下载部署；检查 diff、敏感文件与完整测试。
- [x] 审查改动并修复发现；保留修改供用户审阅，不推送其他聊天的提交。

## 执行记录

- 基线68项测试通过。配置、设备部署与研究隔离按失败→实现→通过推进，最终94项通过。
- 独立审查三项问题：systemd忽略带引号环境文件、自定义Codex安装无法读取、空安全开关继承旧true；均有失败复现与通过回归。
- 额外回归：导出连接参数覆盖旧serial、独立配置目录的定时命令、配置丢失后的停止、不可序列化环境值明确失败。
- 运行说明保留外部前提：Linux/Landlock、Python 3.11+、ADB及已登录Codex；没有对真实手机、登录状态或定时服务进行操作。
