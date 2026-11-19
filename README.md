# QwenPaw 群聊参与插件

`group-reply-gate` 在 QwenPaw 的标准 Agent 运行时统一处理各频道送达的群消息。它在斜杠命令分派之后、主 Agent 创建之前执行，复用当前 Agent 或全局配置的决策模型。已用 QwenPaw 2.3.0.post1 验证安装、加载、静默结束请求和 TypeSafe 客户端的请求格式。

## 安装

在已安装 QwenPaw 的环境中，从本仓库执行：

```sh
qwenpaw plugin install ./group-reply-gate
```

在 QwenPaw 的模型设置中配置决策模型，例如 TypeSafe Jev，并给各 Agent 填写名称和职责。使用云端决策模型时，它会收到该 Agent 的名称、职责、当前群消息文本和近期群消息；私聊默认直接进入原有处理流程。如果希望 Agent 主动参与，频道及平台必须允许普通群消息送达，并关闭该群的“仅 @ 回复”（部分频道另有房间级开关）。

## 运行行为

- 私聊、缺少群聊标识的请求、命令和纯附件沿用 QwenPaw 原有处理。
- 明确点名当前 Agent 时直接放行。其他群文本由决策模型结合该 Agent 的职责判断；概率达到阈值才启动主 Agent。多个 Agent 各自判断。
- 为每个 Agent、频道、会话和可识别的话题缓存最近 8 条入站群消息及参与决定，每条最多 600 字符。缓存仅在内存中保存，最多 512 个会话。决定放行表示允许主 Agent 处理，实际发送情况由频道管理。
- 模型未配置、返回无效结果或在 4 秒内未完成时，未点名消息默认静默；点名仍然可以触发 Agent。

可在 QwenPaw 配置的 `plugins.group-reply-gate` 中调整阈值和每个 Agent 的文本提及别名。下面的 `atlas` 是 QwenPaw Agent ID。更新设置后重载插件或重启 QwenPaw。

```json
{
  "plugins": {
    "group-reply-gate": {
      "enabled": true,
      "threshold": 0.6,
      "aliases": {"atlas": ["支付助手", "atlas:example.org"]}
    }
  }
}
```

## 频道兼容性

插件根据运行时请求的 `channel_meta.is_group` 工作，使用同一运行时的自定义频道也可接入。频道可以提供准确的 `self_mentioned` 布尔值，或用 `bot_mentioned` 传递点名事实。缺少点名信息时插件识别文本中的 `@Agent名字` 和已配置别名。只有平台和频道实际转交的内容可供判断。例如当前 QQ 官方驱动的群聊收件类型是点名事件，平台没有推送的普通群消息不会触发插件。绕过 QwenPaw 标准运行时的独立后端需要接入其自身的消息入口。

QwenPaw 2.3.0.post1 的 Matrix 驱动把已通过频道过滤的消息统一标记为 `bot_mentioned=true`，企业微信也会把此标记用于群回调。插件对这两个频道优先使用明确的自身提及信息和文本别名。Matrix 富文本若只在事件元数据里标记目标，文本别名无法还原该信息。仓库附带 [Matrix 兼容补丁](compat/matrix-mentions.patch)，在官方 Matrix 频道向宿主转交事件时保留原生提及事实。需要该行为时，在对应版本的 QwenPaw 源码仓库应用补丁，然后用修改后的 Matrix 频道插件替换原安装：

```sh
git apply /path/to/qwenpaw-group-reply-gate/compat/matrix-mentions.patch
qwenpaw plugin install --force ./plugins/channel/matrix
```

## 测试

在装有 QwenPaw 2.3.0.post1 的 Python 环境中运行 `python -m unittest discover -s tests -v`。测试用真实 QwenPaw 运行时验证静默分支不会创建主 Agent，并用本地 HTTP 服务验证宿主的 TypeSafe 客户端；无需云端密钥。

## 许可证

本项目采用 [Apache License 2.0](LICENSE)。Matrix 兼容补丁基于 QwenPaw 2.3.0.post1，保留上游版权声明：Copyright 2025 The QwenPaw Authors。补丁新增了对单个账号提及的识别和相应测试。[QwenPaw 上游许可证](https://github.com/agentscope-ai/QwenPaw/blob/v2.3.0.post1/LICENSE)
