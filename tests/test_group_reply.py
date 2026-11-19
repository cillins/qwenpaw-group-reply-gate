import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentscope.classifier import BinaryAnswer, ClassifierResponse
from aiohttp import web
from aiohttp.test_utils import TestServer
from qwenpaw.app.channels.isolated_proxy import IsolatedChannelProxy
from qwenpaw.channel_protocol.models import InboundEvent
from qwenpaw.providers.typesafe_provider import TypeSafeProvider
from qwenpaw.runtime.hooks import HookAction, HookRegistry
from qwenpaw.runtime.phases import Phase
from qwenpaw.runtime.runtime import Runtime
from qwenpaw.runtime.slash_command_registry import SlashCommandRegistry
from qwenpaw.schemas import AgentRequest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "group-reply-gate"))
import main as gate


def request(channel, text="谁能看一下？", **meta):
    return AgentRequest(
        channel=channel,
        session_id="room-1",
        user_id="user",
        channel_meta={"is_group": True, **meta},
        input=[{"role": "user", "content": [{"type": "text", "text": text}]}],
    )


class ParticipationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = HookRegistry()
        gate.plugin.register(
            SimpleNamespace(
                config={"aliases": {"atlas": ["阿特"]}},
                register_runtime_hook=self.registry.register,
            )
        )
        self.workspace = SimpleNamespace(
            config=SimpleNamespace(
                name="Atlas", description="支付", decision_model=None
            ),
            agent_id="atlas",
            plugins=SimpleNamespace(
                hook_registry=self.registry,
                slash_command_registry=SlashCommandRegistry(),
            ),
        )
        self.runtime = Runtime(workspace=self.workspace, app_services=None)
        self.provider = SimpleNamespace(
            evaluate=AsyncMock(
                return_value=ClassifierResponse(
                    model="test",
                    content={"participate": BinaryAnswer(probability=0.1)},
                )
            )
        )
        resolver = patch.object(
            gate, "resolve_decision_model", return_value=(self.provider, "test")
        )
        resolver.start()
        self.addCleanup(resolver.stop)

    async def decide(self, req):
        ctx = self.runtime._build_context(req)
        result = await self.registry.run(Phase.POST_DISPATCH, ctx)
        return result.action

    async def test_channels_share_gate_and_short_circuit_before_agent_build(self):
        for channel in (
            "matrix",
            "discord",
            "slack",
            "telegram",
            "dingtalk",
            "feishu",
            "custom",
        ):
            with self.subTest(channel=channel):
                meta = {"bot_mentioned": channel == "matrix"}
                with patch("qwenpaw.runtime.runtime.AgentBuilder") as builder:
                    events = [
                        e async for e in self.runtime.run(request(channel, **meta))
                    ]
                builder.assert_not_called()
                self.assertEqual(events[-1].status.value, "completed")
                self.assertFalse(events[-1].output)
        self.assertEqual(self.provider.evaluate.await_count, 7)

    async def test_direct_commands_and_explicit_mentions_bypass_classifier(self):
        for req in (
            request("discord", bot_mentioned=True),
            request("matrix", self_mentioned=True),
            request("wecom", "@阿特 看看"),
            request("matrix", "请@Atlas 看看", bot_mentioned=True),
            request("telegram", "/help"),
            request("slack", is_group=False),
            AgentRequest(user_id="u", session_id="dm"),
        ):
            self.assertEqual(await self.decide(req), HookAction.CONTINUE)
        self.provider.evaluate.assert_not_called()

    async def test_other_addressee_and_conversation_boundaries(self):
        await self.decide(request("matrix", "@Atlas2 请查一下", bot_mentioned=True))
        self.provider.evaluate.return_value = ClassifierResponse(
            model="test",
            content={"participate": BinaryAnswer(probability=0.6)},
        )
        self.assertEqual(await self.decide(request("matrix")), HookAction.CONTINUE)
        state = self.provider.evaluate.await_args.kwargs["state"]
        self.assertEqual(state["recent"][0]["decision"], "silent")
        self.assertEqual(state["agent"]["role"], "支付")
        for req in (request("discord"), request("matrix", thread_id="other")):
            await self.decide(req)
            self.assertFalse(
                self.provider.evaluate.await_args.kwargs["state"]["recent"]
            )
        self.workspace.agent_id = "nova"
        await self.decide(request("matrix"))
        self.assertFalse(self.provider.evaluate.await_args.kwargs["state"]["recent"])

    async def test_channel_driver_event_to_runtime(self):
        # Exercise the actual host conversion; no connector credentials needed.
        proxy = object.__new__(IsolatedChannelProxy)
        proxy.channel = "custom"
        for kind, self_mentioned, outcome in (
            ("dm", False, HookAction.CONTINUE),
            ("group", False, HookAction.SKIP_AGENT),
            ("group", True, HookAction.CONTINUE),
        ):
            event = InboundEvent(
                channel_key="custom",
                instance_id="atlas",
                generation=1,
                event_id="id",
                event_kind="message",
                conversation={"id": "room", "type": kind},
                sender_id="alice",
                acl_sender_id="alice",
                sender_name="Alice",
                content_parts=({"type": "text", "text": "午饭吃什么？"},),
                metadata={
                    "session_id": "room",
                    "debounce_key": "room",
                    "user_id": "alice",
                    "self_mentioned": self_mentioned,
                },
                response_handle="reply",
            )
            req = proxy.build_agent_request_from_native(proxy._normalize_event(event))
            self.assertEqual(await self.decide(req), outcome)
        self.provider.evaluate.assert_awaited_once()

    async def test_decision_provider_wire_protocol(self):
        received = []

        async def classify(req):
            received.append(await req.json())
            return web.json_response(
                {
                    "model": "test",
                    "answers": {"participate": {"type": "noul", "noul": 0.91}},
                    "usage": {"input_tokens": 5, "output_tokens": 1},
                }
            )

        app = web.Application()
        app.router.add_post("/v1/systemone", classify)
        async with TestServer(app) as server:
            provider = TypeSafeProvider(
                id="typesafe",
                name="TypeSafe",
                api_key="test-only",
                base_url=str(server.make_url("/")).rstrip("/"),
            )
            with patch.object(
                gate, "resolve_decision_model", return_value=(provider, "test")
            ):
                self.assertEqual(
                    await self.decide(request("custom", "支付超时")),
                    HookAction.CONTINUE,
                )
        self.assertEqual(received[0]["state"]["latest"]["text"], "支付超时")
        self.assertEqual(received[0]["questions"]["participate"]["type"], "noul")

    async def test_failure_is_quiet_and_direct_mentions_still_work(self):
        self.provider.evaluate.side_effect = TimeoutError()
        self.assertEqual(await self.decide(request("discord")), HookAction.SKIP_AGENT)
        self.assertEqual(
            await self.decide(request("discord", bot_mentioned=True)),
            HookAction.CONTINUE,
        )


if __name__ == "__main__":
    unittest.main()
