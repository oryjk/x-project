# X Automation Implementation Plan

> For agentic workers: use superpowers:executing-plans task by task.

**Goal:** 真实来源驱动的中文 AI/科技内容，每小时最多三个随机时刻通过用户手机发布。
**Architecture:** 内容生成与来源验证 → SQLite 队列 → 时段去重 → 可替换发布端。Android 负责单次解锁、界面验证与结果确认。
**Tech Stack:** Python 标准库、Codex CLI、ADB/UIAutomator、SQLite、systemd user timer。
**Spec:** ../specs/2026-11-13-x-automation-design.md

## Global Constraints
- 默认每小时 3 条，小时内随机安排，至少间隔8分钟；不补发。
- .env/.state 不提交；凭据不输出、不传给模型；解锁失败停机。
- 只能发布绑定账号的完全匹配文案，未知结果不可自动重试。

## Review Focus
- shell 元字符/中文/换行不得改变文案或执行命令。
- 旧草稿/错账号/按钮禁用时不能提交。
- 崩溃在提交前后时不得重复发帖。
- 时钟边界与并发 worker 不得超频，断网/息屏不凑量。
- 旧闻、假链接、无字幕视频或提示注入不得变成虚构事实。

## Tasks
- [x] 写失败测试：内容/URL校验、UI定位与全文一致、时段和崩溃去重、凭据不泄漏。
- [x] 实现 `content.py` / `store.py`：结构化草稿、真实来源、去重与不可重复提交状态。
- [x] 实现 `android.py`：ADB参数转义、单次 PIN/图案解锁、账号/编辑框/按钮核对、回执判定。
- [x] 实现 `pipeline.py` / `cli.py`：生成、预览、单条发送、tick、解锁重置。
- [x] 实现安装/暂停/状态的 systemd user 定时器与说明文档。
- [x] 单元测试、真实内容生成、原手机预览及独立审查；38项测试通过。
- [x] 新手机图案解锁、账号/全文预览和首条正式内容实机验证；主页同帖作者/全文/33秒时间已确认。
- [x] 维护 FEATURES/CHANGELOG，核对定时采集与正式发布已启用，凭据已忽略。

## Verification ledger
- 初始测试按模块缺失失败，随后对随机排程、预览不入队等回归逐项 RED→GREEN。
- 最终审查：3项P1、4项P2全部已修复；对应回归覆盖读取隔离、严格回执、绑定、实际提交限频、陈旧队列、在途取消和原始发布日期。
- Landlock真实子进程拒绝读取模拟秘密，隔离后的在线研究进程可正常完成结构化调用。
- systemd原生解析曾发现WorkingDirectory引号错误，增加真实解析回归后修复，定时器已核实active/waiting。
- 新手机根因：实际使用图案锁，早先误按数字 PIN 处理；用户明确九宫格编码后新增 pattern，单次真机验证成功，未输出路径。
- 46项测试通过；首条正式内容已发布并核对。短暂回执错过问题已补严格同帖主页核验，实际捕获界面也通过该核验函数。
- 定时采集与正式发布已启用；手机已恢复息屏。
