# Multi-account and Xiaohongshu Implementation Plan

> User authorized direct implementation on main. Use independently owned modules for adapter/content work, integrate and review in this chat.

**Goal:** Unified multiple-device/account operation and sourced Xiaohongshu image notes.
**Spec:** docs/superpowers/specs/2026-11-13-multi-account-xiaohongshu-design.md
**Architecture:** Config registry maps accounts to devices. Queues/schedules are account scoped, physical sessions device locked. Platform adapters and content policies select X or image notes.
**Tech Stack:** Python3.11+ standard library, ADB/UIAutomator, optional Pillow, unittest.

## Global Constraints
- Develop on main, no branches/worktrees. Preserve existing Android edits from another ongoing task.
- RTK for every shell command. No real phone/publish, no actual .env/config.toml/.state reads into chat. Model receives no phone credentials.
- New profiles disabled, independent schedules, no old-queue fallback, unknown freezes affected account. Same physical device serialized.

## Review Focus
- Aliases or config rename must not bypass quotas or unknown freeze; duplicate accounts refused.
- Device PIN must never fall back to another device's credential; pairing codes never persisted to timer env.
- Disabled profile must not consume research; one failure must not block other phones.
- Missing image/font/unsupported UI must stop before submit; unknown never retried.
- Existing single-account CLI, state and tests remain compatible.

### Task 1: Content and media
Owned: content.py, research.py, cards.py, tests/test_platform_content.py, pyproject optional dependency.
- [x] Test first: X budget retained, note title/body policy, old draft decode, image validation, optional cover, schema/prompt selection, model credential isolation.
- [x] Extend Draft with title='', images=[] (factory), platform='x'; validate source/date separately from platform length. Keep SourceVerifier contract.
- [x] CodexResearcher platform keyword; generate signature unchanged. Add ensure_note_images(draft,state_dir,font_path='')->Draft with optional Pillow; never download images.
- [x] Targeted tests, report assumptions and results.

### Task 2: Xiaohongshu device adapter
Owned: xiaohongshu.py, tests/test_xiaohongshu.py. No android.py edits.
- [x] Test first against synthetic UI and fake ADB: stable account, full title/body, selected media count, effective enabled unique submit, draft preservation, callback-before-tap, no retry after tap, receipt conservative.
- [x] XiaohongshuPoster inherits PhonePoster/session; prepare_note(draft), send_note(draft,before_submit). Explicit package com.xingin.xhs; metadata Intent plus semantic UI progression; unsupported layouts stop.
- [x] Require local PNG/JPEG media, publish via visible UI; no private APIs/no hardcoded coordinates. Preserve existing draft. Return sent/unknown protocol.
- [x] Targeted tests; document true-device limits.

### Task 3: Registry and routing
Owned by parent: config.py, cli.py, pipeline.py, store.py, schedule.py, device_setup.py, android.py minimal integration, new profile/coordinator tests.
- [x] Test first devices/profiles validation, default disabled, env priority, per-device secret prefix, account-state naming and legacy adoption.
- [x] Implement Config.load_all/load(profile), per-account state and device state; no secrets in config repr.
- [x] Parameterize Store/Schedule hourly limit and minimum spacing; preserve 10minute noncrosshour send window, no catchup; reject unsupported excessive cadence.
- [x] Add device-session locking and phone secret prefix; route note adapters/media, revalidate current profile publish switch before tap.
- [x] CLI selection/all/profile list, aggregate coordinator with independent workers; common timer snapshots all configured env namespaces excluding pairing codes; no config-dependent disable regression.
- [x] Integrate adapter/content; full tests.

### Task 4: Documentation and review
- [x] Add env examples, multi-device/account CLI and XHS media prerequisite docs. Update AGENTS/FEATURES/CHANGELOG.
- [x] Independent review of account isolation, device concurrency, credentials and platform checks. Reproduce/fix important findings.
- [x] Full suite, diff/sensitive-file checks; explicitly report synthetic-only XHS verification. Do not publish test messages.


## Execution record
- User chose direct implementation on main. Restored preexisting Android edits, synced origin/main, and retained prior local fixes; no new development branch/worktree.
- Content/media and XHS adapters implemented in independently owned modules; integration handled in main.
- Baseline single-account service output regression reproduced and fixed by selecting aggregate service mode only for profile deployments.
- Review fixes: persistent account/device namespace adoption survives obsolete env-key cleanup; endpoint aliases and subsequent binding retain the device namespace; unowned existing XHS editors are preview-only, and sends require fresh import plus content/media digest checks.
- Existing Adb.check already rejects unbound UI operations. The endpoint alias fix additionally ensures consistent device locks/state before binding.
- Final suite: 181 tests pass, including synthetic XHS UI, real Landlock child, MediaStore/Intent boundary, PNG/JPEG validation, render, independent scheduling and cancellation. No true-device action or public test post.
- Delivery limit: XHS single cover only; target-device preview and actual metadata/identity/receipt selectors remain unverified. Documentation states this explicitly. No claim of verified unattended XHS operation.
