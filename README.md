# AstrBot QQ 音乐与网易云助手

通过 `/qqyy` 命令，在 AstrBot 私聊或群聊中管理音乐账号：扫码登录 QQ 音乐与网易云、绑定多个账号，以及查询 QQ 音乐信息、发送听歌报告和管理刷时长任务。

插件通过自行部署的 `qqyy-php` HTTP 服务调用音乐平台接口。登录凭证和 Cookie 由服务端管理，插件保存账号标识、别名和所属用户；接口地址需显式配置，源码不提供默认站点。

| 平台 | 支持能力 |
| --- | --- |
| QQ 音乐 | QQ 音乐 App、QQ、微信扫码登录，多账号管理，信息查询，年/月/周/日报图片，手动与后台刷时长，每日任务和群通知。 |
| 网易云 | 网易云 App 扫码登录，按用户独立保存账号绑定和 UID，查看账户列表；自动维护由上游服务端执行。 |

## 项目定位

本项目负责聊天命令、账号归属和任务控制，适合通过统一的上游服务管理多个音乐账号。

核心边界如下：

- **AstrBot 插件侧：** 管理命令、权限、账号列表、默认账号、自动任务和报告图片发送。
- **HTTP API 服务侧：** 提供扫码登录、登录缓存刷新、听歌时长上报、账号信息查询、报告图片生成等能力。
- **本地持久化：** 保存账号别名、QQ 音乐 `uin`、网易云 `uid` 和用户归属关系，临时报告图片发送后清理。

## 功能概览

- 手机 QQ 音乐扫码登录，登录后自动绑定账号。
- 网易云 App 扫码登录，支持多账号别名和独立的账户列表。
- 一个用户可绑定多个 QQ 音乐账号，并设置默认账号。
- 刷新服务端登录缓存，减少反复扫码。
- 查询账号昵称、等级、成长值、好友排名和累计播放时长。
- 手动上报 10 分钟听歌时长。
- 后台自动刷时长，按 1-3 分钟随机间隔持续执行，直到信息显示 24 小时或连续 3 次上报失败。
- 每日 0 点自动刷新全部已绑定账号，刷新成功后自动签到积分并启动刷时长。
- 可在指定群订阅每日任务结果通知。
- 服务端已负责自动任务时，可用子指令关闭插件本地后台任务，重启后保持关闭。
- 支持年报、月报、周报、日报图片发送。
- 支持用户白名单、群白名单和受控的群内回复代查。
- 报告图片发送后自动清理，避免长期占用磁盘。

## 运行要求

- 已安装并可运行 AstrBot。
- Python 3.10 及以上，并安装 `requests`。
- AstrBot 所在环境可以访问 QQ 音乐 HTTP API 服务。
- HTTP API 服务需要支持本文档列出的 action，详见 [HTTP API 对接](#http-api-对接)。

## 安装

将插件目录放入 AstrBot 插件目录，例如：

```text
AstrBot/data/plugins/astrbot_plugin_qqyy
```

安装依赖：

```bash
pip install -r requirements.txt
```

启动或重载 AstrBot 后，在插件列表中确认 `qqyy` 已加载。

## 快速开始

1. 配置 HTTP API 地址。将下面的 `url` 填为你部署的接口地址，项目不提供默认站点。

```json
{
  "qqyy_api": {
    "url": "",
    "token": "",
    "mobile_url": ""
  }
}
```

2. 在聊天中扫码登录。

```text
/qqyy 登录
```

3. 查询账号信息。

```text
/qqyy 信息
```

4. 手动刷一次听歌时长。

```text
/qqyy 刷时长
```

5. 启动后台自动刷时长。

```text
/qqyy 自动刷时长
```

## 配置说明

插件配置由 AstrBot 根据 `_conf_schema.json` 生成，通常位于：

```text
data/config/astrbot_plugin_qqyy_config.json
```

### 完整配置示例

```json
{
  "access_control": {
    "enabled": false,
    "allowed_user_ids": [],
    "allowed_group_ids": [],
    "delegate_user_ids": [],
    "deny_message": "当前用户或群组未被允许使用 QQ 音乐听歌报告插件"
  },
  "qqyy_api": {
    "url": "",
    "token": "",
    "mobile_url": ""
  },
  "login": {
    "qq_login_type": "mobile",
    "netease_client_platform": "pc",
    "timeout_seconds": 180
  },
  "background_tasks": {
    "enabled": true
  },
  "daily_refresh": {
    "enabled": true,
    "notify_enabled": true
  }
}
```

### `qqyy_api`

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `url` | string | 空，需自行填写 | 通用 HTTP API 地址。可以填写完整接口地址，也可以填写以 `/` 结尾的部署目录。 |
| `token` | string | 空 | 接口访问令牌。非空时通过 `X-QQYY-API-Token` 请求头发送。 |
| `mobile_url` | string | 空 | 手机 QQ 音乐扫码接口地址，生成二维码和监听共用此入口。为空时按 `url` 同目录推导为 `qqmusic_mobile_login.php`。 |

`url` 和 `mobile_url` 只允许 `http` 或 `https`。地址不能包含 query 或 fragment。

未配置地址时，请求会提示填写 `qqyy_api.url`，不会自动连接预设站点。也可通过环境变量 `QQYY_API_URL`（兼容 `QQYY_PHP_API_URL`）显式指定地址；示例 `https://your-api.example.com/qqmusic_api.php` 需要替换为实际部署地址。

网易云使用同一个 `url`，请求携带 `platform=netease`。上游应先配置好网易 Enhanced 服务地址。

### `login`

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `qq_login_type` | string | `mobile` | `mobile` 用 QQ 音乐 App 扫码；`qq` 用手机 QQ 扫码；`wx` 用微信扫码。 |
| `netease_client_platform` | string | `pc` | 网易云二维码客户端类型，可选 `pc`、`web`。 |
| `timeout_seconds` | int | `180` | 两平台等待扫码的秒数，有效范围为 1–600。 |

QQ 音乐手机扫码采用 MQTT 长监听，插件先启动监听再发送二维码。其他 QQ 扫码方式和网易云使用状态轮询。用户取消、二维码过期、网易云风控或凭证不完整时会返回具体原因；接口连续失败 3 次会停止等待并反馈错误。

手机扫码接口单次 HTTP 请求可能持续整个等待窗口。部署时，Web 服务器和反向代理的请求超时应至少留出扫码等待时间加 30 秒，避免中途断开。

### `access_control`

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `false` | 是否启用白名单控制。 |
| `allowed_user_ids` | list | `[]` | 允许使用插件的用户 ID。 |
| `allowed_group_ids` | list | `[]` | 允许使用插件的群 ID。 |
| `delegate_user_ids` | list | `[]` | 允许通过回复消息代查群成员账号的用户 ID。为空时复用 `allowed_user_ids`。 |
| `deny_message` | string | 内置提示 | 未命中白名单时返回的提示。 |

白名单判定规则：

- `enabled = false` 时，所有用户和群组都可以使用插件。
- `enabled = true` 且两个白名单都为空时，插件拒绝所有请求。
- 私聊中，只检查当前用户 ID 是否在 `allowed_user_ids` 中。
- 群聊中，当前用户 ID 命中 `allowed_user_ids`，或当前群 ID 命中 `allowed_group_ids`，即可使用插件。
- 群白名单只表示“可以使用插件”，不会自动授予“回复代查他人账号”的权限。
- 回复代查要求发送者同时满足：平台识别为群管理员或群主，并且用户 ID 命中 `delegate_user_ids`，若 `delegate_user_ids` 为空则回退检查 `allowed_user_ids`。

### `background_tasks`

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 本地后台任务总开关，控制每日刷新、签到和自动刷时长。 |

服务端已负责凭证刷新、签到和刷时长时，AstrBot 管理员可执行 `/qqyy 关闭后台任务`，或在配置中将此开关设为 `false`。关闭指令会保存配置并停止当前本地后台任务，插件重载或 AstrBot 重启后保持关闭。

关闭后，登录、手动刷新、单次刷时长、信息查询和报告命令仍可使用。执行 `/qqyy 开启后台任务` 可恢复本地后台功能，每日调度仍取决于 `daily_refresh.enabled`；此前取消的刷时长任务需手动重新启动，或等待下次每日任务启动。

### `daily_refresh`

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 是否启用每日 0 点自动刷新，同时需要 `background_tasks.enabled = true`。 |
| `notify_enabled` | bool | `true` | 是否向已订阅的群主动发送每日任务结果。 |

每日任务会刷新所有已绑定账号的服务端登录缓存。刷新成功的账号会继续执行每日签到积分，并尝试启动后台自动刷时长。已经在运行的刷时长任务不会重复启动。

## 命令说明

所有命令都挂在 `/qqyy` 命令组下。

| 子命令 | 用法 | 说明 |
| --- | --- | --- |
| `登录` | `/qqyy 登录 [别名]` | 按 `qq_login_type` 配置扫码登录 QQ 音乐。默认别名为“大号”。 |
| `网易云登录` | `/qqyy 网易云登录 [别名]`（也可用 `网易登录`） | 用网易云 App 扫码登录，默认别名为“大号”。 |
| `网易云账户列表` | `/qqyy 网易云账户列表` | 查看当前用户绑定的网易云账号和 UID。 |
| `刷新` | `/qqyy 刷新 [别名]` | 刷新指定账号的服务端登录缓存。 |
| `全部刷新` | `/qqyy 全部刷新` | 刷新当前用户绑定的全部账号。 |
| `关闭后台任务` | `/qqyy 关闭后台任务`（简写 `/qqyy 关闭`） | AstrBot 管理员关闭本地每日刷新、签到和全部自动刷时长，保存关闭状态。 |
| `开启后台任务` | `/qqyy 开启后台任务`（简写 `/qqyy 开启`） | AstrBot 管理员恢复本地后台功能。 |
| `开启日更通知` | `/qqyy 开启日更通知` | 在当前群订阅每日任务结果通知。 |
| `关闭日更通知` | `/qqyy 关闭日更通知` | 在当前群取消每日任务结果通知。 |
| `刷时长` | `/qqyy 刷时长 [别名]` | 为指定账号上报一次 10 分钟听歌时长。 |
| `自动刷时长` | `/qqyy 自动刷时长 [别名]` | 启动后台刷时长任务，直到信息显示 24 小时或连续 3 次上报失败。 |
| `全部刷时长` | `/qqyy 全部刷时长` | 为当前用户全部账号各刷一次时长，账号之间默认错峰 3 秒。 |
| `全部自动刷时长` | `/qqyy 全部自动刷时长` | 为当前用户全部账号错峰启动后台刷时长任务。 |
| `账户列表` | `/qqyy 账户列表` | 查看当前用户绑定的全部账号。 |
| `切换` | `/qqyy 切换 <别名>` | 设置默认账号。 |
| `删除` | `/qqyy 删除 <别名>` | 删除指定账号绑定。 |
| `信息` | `/qqyy 信息 [别名]` | 查询账号信息。 |
| `全部信息` | `/qqyy 全部信息` | 查询当前用户绑定的全部账号信息。 |
| `年报` | `/qqyy 年报 [别名]` | 发送年度听歌报告图片。 |
| `月报` | `/qqyy 月报 [别名]` | 发送月度听歌报告图片。 |
| `周报` | `/qqyy 周报 [别名]` | 发送周度听歌报告图片。 |
| `日报` | `/qqyy 日报 [别名]` | 发送日度听歌报告图片。 |

## 使用流程

### 绑定第一个账号

```text
/qqyy 登录
```

插件会发送二维码图片。用手机 QQ 音乐 App 扫码并确认后，账号会被绑定到当前聊天用户名下。第一次绑定的账号会自动成为默认账号。

### 登录网易云

```text
/qqyy 网易云登录
/qqyy 网易云登录 小号
/qqyy 网易云账户列表
```

用网易云 App 扫描二维码并确认。上游保存完整登录 Cookie，插件只保存当前用户、账号别名和 UID；QQ 音乐与网易云可以使用相同别名，绑定不会互相覆盖。网易云登录成功后的刷新、签到和刷时长由上游服务端任务管理。

### 绑定多个账号

```text
/qqyy 登录 小号
/qqyy 账户列表
/qqyy 切换 小号
```

多个账号通过别名区分。未传别名的查询、刷新、刷时长和报告命令会使用默认账号。

### 刷新登录缓存

```text
/qqyy 刷新
/qqyy 全部刷新
```

当账号信息查询或刷时长失败，并提示登录缓存可能失效时，可以先执行刷新。刷新仍失败时，需要重新扫码登录。

### 刷时长

```text
/qqyy 刷时长
/qqyy 自动刷时长
/qqyy 全部自动刷时长
```

`刷时长` 只上报一次。`自动刷时长` 会创建后台任务，任务启动后先上报一次，然后等待查询播放进度。后续上报间隔为 1-3 分钟随机值。

自动刷时长停止条件：

- 信息接口显示播放时长已达到 24 小时。
- 连续 3 次上报失败。
- AstrBot 管理员执行 `/qqyy 关闭后台任务`。
- AstrBot 插件卸载或重载导致任务被取消。

### 由服务端负责自动任务

AstrBot 管理员执行：

```text
/qqyy 关闭后台任务
```

指令会取消本地每日调度和所有用户正在运行的自动刷时长任务。已发出的单次 HTTP 请求会自然结束，关闭后不再提交后续后台操作。关闭状态会持久保存，不影响服务端自身的自动任务。

需要恢复时执行：

```text
/qqyy 开启后台任务
```

### 每日任务通知

在需要接收通知的群里执行：

```text
/qqyy 开启日更通知
```

取消通知：

```text
/qqyy 关闭日更通知
```

每日任务结果会统计刷新成功、刷新失败、签到成功、签到失败、新启动刷时长任务和已运行刷时长任务数量。

`关闭日更通知` 只取消当前群的通知订阅；本地后台任务的启停使用 `关闭后台任务` 和 `开启后台任务`。

### 授权群管理员代查

代查用于群管理员帮成员查询其绑定账号信息或报告。建议只授予可信管理员。

配置示例：

```json
{
  "access_control": {
    "enabled": true,
    "allowed_group_ids": ["20001"],
    "allowed_user_ids": ["10001"],
    "delegate_user_ids": ["10001"]
  }
}
```

使用方式：

1. 目标成员先在群里发送一条消息。
2. 授权管理员回复这条消息。
3. 管理员发送 `/qqyy 信息`、`/qqyy 周报` 等命令。

如果发送者没有代查权限，或平台原始消息中无法解析回复目标，插件会回退为查询发送者自己的账号。

## 数据与文件

| 路径 | 说明 |
| --- | --- |
| `data/qqyy_accounts.json` | 账号绑定数据，包含用户归属、账号别名和 `uin`。 |
| `data/netease_accounts.json` | 网易云账号绑定数据，包含用户归属、账号别名和 `uid`，不保存 Cookie。 |
| `data/qqyy_daily_refresh_targets.json` | 已订阅每日任务通知的会话。 |
| `tmp/` | 临时二维码和报告图片目录。 |
| `data/config/astrbot_plugin_qqyy_config.json` | AstrBot 生成的插件配置文件。 |

账号存储文件使用原子写入，写入前会生成临时文件，写入完成后替换原文件。报告图片只用于当次消息发送，发送后会由 `after_message_sent` 钩子清理。

## 安全边界

- 插件保存 QQ 音乐 `uin`、网易云 `uid`、别名和用户归属，不保存登录密钥或 Cookie；二维码和报告图片在发送后清理。
- HTTP API 地址只允许 `http` 和 `https`。
- 报告图片优先接受 data/base64 或 HTTP/HTTPS URL。
- 服务端返回本地 `path` 时，插件只接受已位于插件输出目录内的路径，不读取任意本地文件。
- 回复代查必须显式授权；群白名单不会自动授予代查能力。
- 群内代查依赖平台 adapter 提供的原始消息结构。如果平台不提供可解析的回复目标，插件会回退到发送者本人。

## HTTP API 对接

插件默认请求两个端点：

- `qqmusic_api.php`：QQ 音乐和网易云共用的通用接口。
- `qqmusic_mobile_login.php`：手机 QQ 音乐二维码生成和 MQTT 监听接口。

请求方式为 `POST` 表单，包含 `action` 和对应参数；对象和数组参数编码为 JSON 字符串。设置访问令牌时，请求头携带 `X-QQYY-API-Token`。

| 操作 | 端点 | `action` | 主要参数 |
| --- | --- | --- | --- |
| 手机 QQ 音乐生成二维码 | 手机扫码接口 | `get_qrcode` | `login_type=mobile` |
| 手机 QQ 音乐监听登录 | 手机扫码接口 | `checking_mobile_qrcode` | `identifier`、`qrcode`、`timeout` |
| 手机 QQ 或微信生成二维码 | 通用接口 | `login.get_qrcode` | `platform=qq`、`login_type=qq/wx` |
| 手机 QQ 或微信检查登录 | 通用接口 | `login.check_qrcode` | `platform=qq`、`identifier`、`login_type` |
| 网易云生成二维码 | 通用接口 | `login.get_qrcode` | `platform=netease`、`client_platform=pc/web` |
| 网易云检查登录 | 通用接口 | `login.check_qrcode` | `platform=netease`、`identifier` |
| 刷新 QQ 音乐缓存 | 通用接口 | `login.refresh_credential` | `uin` |
| 上报一次 QQ 音乐时长 | 通用接口 | `report.report_play_duration` | `uin`、`format=text` |

JSON 响应兼容 `code=0/data` 和 `success=true/data` 两种封装。网易云动作还包含平台封装，客户端读取其中的业务 `data`；扫码成功需要有效的该平台账号标识。手机扫码默认保持长监听直到成功、取消或超时。播放上报使用文本响应，并检查成功标记 `RR`。

完整动作和参数以部署上游的文档接口 `action=docs&platform=qq` 或 `action=docs&platform=netease` 为准。

## 常见问题

### 提示“你还没有绑定 QQ 音乐账号”

当前聊天用户还没有绑定记录。先执行：

```text
/qqyy 登录
```

### 提示“QQ 音乐接口请求失败”

常见原因：

- `qqyy_api.url` 配置错误。
- AstrBot 无法访问 HTTP API 服务。
- HTTP API 服务端登录缓存已失效。
- 对应 `uin` 无效或服务端没有该账号缓存。

建议按顺序排查：

```text
/qqyy 刷新
/qqyy 登录
```

如果仍失败，检查 HTTP API 服务日志。

### 报告图片发送失败

优先让服务端返回 data/base64 或可访问的 HTTP/HTTPS 图片 URL。插件不会读取任意本地路径。如果服务端返回 `path`，该路径必须位于插件允许的输出目录内。

### 回复别人消息后仍然查到自己

可能原因：

- 当前用户未命中 `delegate_user_ids`。
- 当前用户不是平台识别的群管理员或群主。
- 只配置了群白名单，但没有配置代查用户。
- 当前平台 adapter 没有在原始消息中提供可解析的回复目标。

### 自动刷时长一直失败

当前版本会在连续 3 次上报失败后停止，不会无限重试。失败后建议先执行：

```text
/qqyy 信息
/qqyy 刷新
```

如果信息查询和刷新都失败，通常需要重新扫码登录。

## 开发结构

```text
astrbot_plugin_qqyy/
├── main.py                 # AstrBot 入口与命令注册
├── metadata.yaml           # 插件元数据
├── _conf_schema.json       # 配置描述
├── requirements.txt        # 运行依赖
├── pyproject.toml          # 本项目 Ruff 配置
├── api/                    # HTTP 传输和平台接口适配
│   ├── http.py
│   ├── qqmusic.py
│   ├── netease.py
│   ├── credentials.py
│   └── images.py
├── handlers/               # 命令业务与访问控制
│   ├── qqmusic.py
│   ├── netease.py
│   ├── login.py
│   └── access.py
├── tasks/                  # 后台任务生命周期与批量执行
│   ├── background.py
│   ├── auto_play.py
│   └── jobs.py
├── storage/                # 账号绑定和群通知订阅持久化
│   ├── accounts.py
│   └── notifications.py
├── shared/                 # 并发工具、消息文案与公共异常
│   ├── concurrency.py
│   ├── messages.py
│   └── errors.py
└── LICENSE                 # 开源许可证
```

`main.py` 装配组件，把命令交给 `handlers/`，把后台任务交给 `BackgroundTaskManager`。接口客户端负责请求和响应解析；存储层只负责 JSON 数据，不依赖命令或任务。

运行数据仍使用插件根目录下的 `data/` 和 `tmp/`，目录整理不迁移账号和通知订阅。旧 `qqyy_php` 配置与旧环境变量输入继续兼容。Python 最低版本为 3.10。

## 开发验证

检查 Python 语法：

```bash
python -m compileall -q main.py api handlers tasks storage shared
```

格式化与静态检查：

```bash
ruff format .
ruff check .
```

检查 JSON 配置描述：

```bash
python -m json.tool _conf_schema.json
```

## 版本信息

- 插件名：`astrbot_plugin_qqyy`
- 命令组：`/qqyy`
- 当前版本：`5.9.0`
- 作者：`27xk`
- 仓库：[27xk/astrbot_plugin_qqyy](https://github.com/27xk/astrbot_plugin_qqyy)

## 参考链接

- [AstrBot 项目](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档（中文）](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Development Docs (English)](https://docs.astrbot.app/en/dev/star/plugin-new.html)
