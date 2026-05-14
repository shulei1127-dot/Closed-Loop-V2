# PTS Browser Profile Transport

PTS 执行链支持使用稳定的 Playwright 持久化 profile，在 PTS 页面同源上下文里发送 GraphQL 请求。

推荐配置：

```env
PTS_EXECUTION_TRANSPORT=browser_profile
PTS_BROWSER_PROFILE_ENABLED=true
PTS_BROWSER_PROFILE_DIR=.pts-browser-profile/chrome-profile
PTS_BROWSER_HEADLESS=true
PTS_DIRECT_HTTP_ENABLED=false
```

首次使用前运行：

```bash
.venv/bin/python scripts/pts_browser_login.py
```

登录成功后关闭脚本即可。后续执行会复用同一个 profile，不再依赖手工粘贴 `PTS_COOKIE_HEADER`。如需临时回退旧模式：

```env
PTS_EXECUTION_TRANSPORT=cookie_direct
PTS_DIRECT_HTTP_ENABLED=true
PTS_BROWSER_PROFILE_ENABLED=false
```
