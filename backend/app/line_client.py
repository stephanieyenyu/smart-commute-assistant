import json
import os
from urllib.parse import urlencode
from linebot.v3.messaging import (
    Configuration,
    AsyncApiClient,
    AsyncMessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    AudioMessage,
    FlexMessage,
    QuickReply,
    QuickReplyItem,
    LocationAction,
    MessageAction,
    DatetimePickerAction,
    URIAction,
)
from linebot.v3.messaging.models import FlexContainer
from app.config import LINE_CHANNEL_ACCESS_TOKEN

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)


def _build_quick_reply_items(items: list) -> list[QuickReplyItem]:
    """
    Build QuickReplyItem list from dicts.
    Supported types:
      - type: 'location'       → opens LINE map
      - type: 'message'        → sends a text message
      - type: 'datetimepicker' → opens time/date picker (requires 'data', 'mode')
      - type: 'uri'            → opens a URL (requires 'uri')
    """
    quick_reply_items = []
    for item in items:
        t = item["type"]
        if t == "location":
            action = LocationAction(label=item["label"])
        elif t == "datetimepicker":
            action = DatetimePickerAction(
                label=item["label"],
                data=item.get("data", "postback"),
                mode=item.get("mode", "time"),
            )
        elif t == "uri":
            action = URIAction(label=item["label"], uri=item["uri"])
        else:  # message
            action = MessageAction(label=item["label"], text=item["text"])
        quick_reply_items.append(QuickReplyItem(action=action))
    return quick_reply_items


async def reply_text(reply_token: str, text: str) -> None:
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        await line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)]
            )
        )


async def reply_with_quick_reply(reply_token: str, text: str, items: list) -> None:
    await reply_multi_messages_with_quick_reply(reply_token, [text], items)


async def reply_multi_messages_with_quick_reply(reply_token: str, texts: list[str], items: list) -> None:
    quick_reply_items = _build_quick_reply_items(items)
    messages = []
    for i, t in enumerate(texts):
        qr = QuickReply(items=quick_reply_items) if i == len(texts) - 1 else None
        messages.append(TextMessage(text=t, quick_reply=qr))

    try:
        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            await line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=messages
                )
            )
    except Exception as e:
        print(f"[line] reply_multi_messages_with_quick_reply error: {e}")
        if texts:
            await reply_text(reply_token, texts[0])


async def reply_flex_message(reply_token: str, alt_text: str, flex_contents: dict | list[dict]) -> None:
    """
    發送 Flex Message。
    flex_contents 可為單張 Flex Bubble dict，或多張 Bubble dict 組成的 Carousel。
    """
    if isinstance(flex_contents, dict):
        container_payload = flex_contents
    elif len(flex_contents) == 1:
        container_payload = flex_contents[0]
    else:
        container_payload = {
            "type": "carousel",
            "contents": flex_contents,
        }
    try:
        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            flex_container = FlexContainer.from_dict(container_payload)
            await line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        FlexMessage(
                            alt_text=alt_text,
                            contents=flex_container,
                        )
                    ]
                )
            )
    except Exception as e:
        print(f"[line] reply_flex_message error: {e}")
        await reply_text(reply_token, alt_text)


async def push_text(user_id: str, text: str) -> None:
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        await line_bot_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=text)]
            )
        )


def build_tts_audio_url(text: str) -> str:
    configured_base = os.getenv("TTS_AUDIO_BASE_URL", "").strip()
    query_text = (text or "出門提醒").replace("\n", " ")
    if configured_base:
        separator = "&" if "?" in configured_base else "?"
        return f"{configured_base}{separator}{urlencode({'text': query_text})}"
    return "https://translate.google.com/translate_tts?" + urlencode({
        "ie": "UTF-8",
        "client": "tw-ob",
        "tl": "zh-TW",
        "q": query_text[:180],
    })


def estimate_tts_duration_ms(text: str) -> int:
    return max(5000, min(60000, len(text or "") * 220))


async def push_audio_message(user_id: str, original_content_url: str, duration_ms: int) -> None:
    if not original_content_url.startswith("https://"):
        raise ValueError("LINE AudioMessage requires an HTTPS original_content_url")
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        await line_bot_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[
                    AudioMessage(
                        original_content_url=original_content_url,
                        duration=duration_ms,
                    )
                ]
            )
        )


async def push_with_quick_reply(user_id: str, text: str, items: list) -> None:
    quick_reply_items = _build_quick_reply_items(items)
    try:
        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            await line_bot_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[
                        TextMessage(
                            text=text,
                            quick_reply=QuickReply(items=quick_reply_items)
                        )
                    ]
                )
            )
    except Exception as e:
        print(f"[line] push_with_quick_reply error: {e}")
        await push_text(user_id, text)


async def push_flex_message(
    user_id: str,
    alt_text: str,
    flex_contents: list[dict],
    quick_reply_items: list | None = None,
) -> None:
    """
    推播 Flex Message（Bubble 或 Carousel）給用戶。
    若提供 quick_reply_items，在 Flex 之後附加一則純文字訊息帶 QuickReply。
    """
    if isinstance(flex_contents, list):
        container_dict = {"type": "carousel", "contents": flex_contents}
    else:
        container_dict = flex_contents

    messages = []
    try:
        flex_container = FlexContainer.from_dict(container_dict)
        messages.append(FlexMessage(alt_text=alt_text, contents=flex_container))
    except Exception as e:
        print(f"[line] push_flex_message build container error: {e}")
        await push_text(user_id, alt_text)
        return

    if quick_reply_items:
        qr_items = _build_quick_reply_items(quick_reply_items)
        messages.append(
            TextMessage(
                text="請選擇下一步：",
                quick_reply=QuickReply(items=qr_items),
            )
        )

    try:
        async with AsyncApiClient(configuration) as api_client:
            line_bot_api = AsyncMessagingApi(api_client)
            await line_bot_api.push_message(
                PushMessageRequest(to=user_id, messages=messages)
            )
        print(f"[line] push_flex_message sent to user_id={user_id}")
    except Exception as e:
        print(f"[line] push_flex_message error: {e}")
        await push_text(user_id, alt_text)
