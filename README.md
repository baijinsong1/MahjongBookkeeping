# 棋牌室经营管理系统

基于 Flask 的轻量经营管理系统，覆盖包间开台、订单结算、会员、商品、支出和营业报表。

## 功能概览

- 包间看板：包间 `555`、`666`、`777`、`888`、`999` 的开台与订单入口
- 订单管理：添加普通人员、会员和商品，结账闭台，查看及删除历史订单
- 会员管理：普通会员、超级会员、会员费补充、余额扣台费
- 商品管理：新增、启停和删除商品
- 支出登记：记录今日支出并从报表净收入中扣除
- 营业报表：今日、本周、本月、本年收入与服务明细

## 目录结构

```text
.
├── app.py                 # Flask 应用、数据模型和路由
├── instance/data.db       # 默认 SQLite 数据库（运行时生成）
├── migrations/            # Flask-Migrate / Alembic 迁移
├── static/css/
│   ├── base.css           # 全局设计基础与通用组件
│   ├── pages.css          # 首页、开台、商品、支出页样式
│   ├── members.css        # 会员页样式
│   ├── orders.css         # 订单与订单列表样式
│   └── report.css         # 营业报表样式
├── static/js/app.js       # 表单二次确认等前端交互
├── templates/
│   ├── base.html          # 基础模板
│   └── *.html             # 各业务页面
├── tests/                 # 自动化测试
├── requirements.txt       # 运行依赖
└── requirements-dev.txt   # 开发和测试依赖
```

## 环境与安装

推荐 Python 3.9 及以上版本。

```bash
cd /Users/a1/PyCharmMiscProject/工作
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

如果项目继续使用现有虚拟环境：

```bash
cd /Users/a1/PyCharmMiscProject/工作
source /Users/a1/PyCharmMiscProject/.venv/bin/activate
python -m pip install -r requirements-dev.txt
```

## 初始化管理员

首次运行前先创建管理员账号。密码至少 12 个字符，只以 PBKDF2-SHA256 哈希形式保存到 `instance/admin.json`：

```bash
cd /Users/a1/PyCharmMiscProject/工作
source /Users/a1/PyCharmMiscProject/.venv/bin/activate
flask --app app create-admin
```

需要重置密码时再次执行该命令即可。

## 启动服务

默认仅监听本机回环地址，适合本机浏览器和 ngrok 转发：

```bash
cd /Users/a1/PyCharmMiscProject/工作
source /Users/a1/PyCharmMiscProject/.venv/bin/activate
flask --app app run --host 127.0.0.1 --port 8080
```

也可以直接运行：

```bash
python app.py
```

浏览器访问 `http://127.0.0.1:8080/`，未登录时会跳转到管理员登录页。首次启动会自动创建数据库表、补齐兼容字段，并可通过首页的初始化入口创建默认包间和商品。

如果确实需要同一局域网内的设备直接访问，才显式开放全部网卡：

```bash
HOST=0.0.0.0 python app.py
```

不要在本机直接暴露 `0.0.0.0:8080` 到公网；外部访问优先通过 ngrok、Tailscale 或反向代理提供的 HTTPS 入口。

## 后台常驻与外部访问

如果希望关闭终端、退出 PyCharm 后服务仍保持运行，可安装 macOS `launchd` 后台服务。安装后 Flask 和 ngrok 会在登录时自动启动，异常退出后自动重启。

停止当前终端或 PyCharm 中运行的 Flask 和 ngrok 后执行。若尚未初始化管理员，安装脚本会先提示创建账号和密码：

```bash
cd /Users/a1/PyCharmMiscProject/工作
chmod +x deploy/install_launch_agents.sh deploy/uninstall_launch_agents.sh
./deploy/install_launch_agents.sh
```

如果已在 ngrok 后台预留静态域名，可以避免每次重启后公网地址变化：

```bash
NGROK_STATIC_URL=https://your-domain.ngrok-free.app ./deploy/install_launch_agents.sh
```

常用管理命令：

```bash
launchctl kickstart -k gui/$(id -u)/com.majiang.app   # 重启 Flask
launchctl kickstart -k gui/$(id -u)/com.ngrok.app     # 重启 ngrok
tail -f logs/flask.log                                # 查看 Flask 日志
tail -f logs/ngrok.log                                # 查看公网地址和连接日志
./deploy/uninstall_launch_agents.sh                   # 停止并移除后台服务
```

`launchd` 只能解决“终端必须常开”的问题。Mac 休眠、断网或退出登录仍会中断访问；长期对外使用建议让 Mac 保持开机联网，或迁移到云服务器。若只在同一局域网使用，可直接访问 `http://Mac的局域网IP:8080/`，无需 ngrok。

## 安全说明

- 除登录页、静态资源和健康检查外，所有页面都要求管理员登录。
- 所有 POST 表单都校验 CSRF Token；缺少或伪造 Token 会返回 `400`。
- 连续 5 次登录失败会限制该来源 IP 5 分钟。
- 登录会话默认 12 小时过期，Cookie 使用 `HttpOnly` 和 `SameSite=Lax`。
- 响应包含 CSP、禁止 iframe 嵌入、`nosniff` 和禁止缓存等安全头。
- 未设置 `SECRET_KEY` 时会自动生成 `instance/secret_key`（权限 `600`），不要把它提交到版本库。
- 公网访问必须使用 HTTPS。仅在所有访问都经过 HTTPS 时，才设置 `SESSION_COOKIE_SECURE=1`。
- 如果本机直接暴露在公网，仅靠登录仍不足；建议结合防火墙、VPN 或云平台访问控制。

### 可配置环境变量

- `DATABASE_URL`：数据库连接地址，默认 `sqlite:///data.db`
- `HOST`：监听地址，`app.py` 默认 `127.0.0.1`
- `PORT`：监听端口，默认 `8080`
- `SECRET_KEY`：Flask 会话密钥；未设置时自动生成到 `instance/secret_key`
- `SESSION_COOKIE_SECURE=1`：仅通过 HTTPS 时发送会话 Cookie
- `TRUST_PROXY_HEADERS=1`：位于 ngrok/反向代理后方时启用代理头处理
- `ADMIN_USERNAME`：管理员用户名，默认 `admin`
- `ADMIN_PASSWORD`：管理员明文密码，仅建议临时开发使用
- `ADMIN_PASSWORD_HASH`：管理员密码哈希，优先于 `ADMIN_PASSWORD`
- `ADMIN_AUTH_FILE`：管理员凭据文件路径，默认 `instance/admin.json`
- `SKIP_DB_BOOTSTRAP=1`：跳过启动时的自动建表和兼容升级，仅建议迁移或测试工具使用

PowerShell 示例：

```powershell
$env:FLASK_APP = "app.py"
flask --app app run --debug --host 127.0.0.1 --port 8080
```

## 测试

测试使用独立的临时 SQLite 数据库，不会修改 `instance/data.db`。

```bash
cd /Users/a1/PyCharmMiscProject/工作
source /Users/a1/PyCharmMiscProject/.venv/bin/activate
python -m unittest discover -s tests -v
```

安装开发依赖后也可以使用：

```bash
pytest -q
```

## 数据库与迁移

默认数据库为 `instance/data.db`。修改表结构前请先备份：

```bash
cp instance/data.db instance/data.db.bak
```

应用启动时仍保留历史数据结构兼容逻辑，便于兼容已有数据库。正式迁移使用 Flask-Migrate：

```bash
flask --app app db current
flask --app app db migrate -m "describe change"
flask --app app db upgrade
```

如果现有数据库已经通过自动建表创建，并且首次接入迁移，请先确认迁移状态，再决定是否执行 `flask --app app db stamp head`；不要在不了解当前数据库状态时直接执行升级。

## 会员与台费规则

- 普通会员开卡和补充的会员费计入当天会员费收入。
- 普通会员上桌时，个人台费从会员费余额扣除。
- 超级会员无需会员费，上桌不参与台费分摊。
- 台费按三人分摊：`台费 80` 时每人 `20`，`台费 100` 时每人 `25`。
- 会员只抵扣本人台费；超级会员减免对应台费，不占用其他人员分摊额度。
- 营业总收入 = 订单实收 + 会员费收入，支出从净收入中扣除。

## 样式维护

页面统一继承 `templates/base.html`。通用组件放在 `static/css/base.css`，页面专属样式放在对应静态 CSS 文件中。新增页面请避免再次写入内嵌 `<style>`，优先复用 `.page`、`.topbar`、`.card`、`.btn`、`.form-control` 和 `.summary` 等基础类。
