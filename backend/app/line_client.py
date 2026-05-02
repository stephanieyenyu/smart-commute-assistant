from linebot.v3.messaging import (
    Configuration,
    AsyncApiClient,
    AsyncMessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    LocationAction,
    MessageAction,
    DatetimePickerAction,
)
from app.config import LINE_CHANNEL_ACCESS_TOKEN

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
LINE_QUICK_REPLY_LIMIT = 13

PERSISTENT_QUICK_REPLIES = [
    {"type": "message", "label": "今日通勤建議", "text": "今日通勤建議"},
    {"type": "message", "label": "修改出門時間", "text": "修改出門時間"},
]


def _quick_reply_identity(item: dict) -> tuple:
    return (
        item.get("type"),
        item.get("label"),
        item.get("text"),
        item.get("data"),
    )


def with_persistent_quick_replies(items: list | None) -> list:
    merged = list(items or [])
    seen = {_quick_reply_identity(item) for item in merged}
    for item in PERSISTENT_QUICK_REPLIES:
        identity = _quick_reply_identity(item)
        if identity in seen:
            continue
        if len(merged) >= LINE_QUICK_REPLY_LIMIT:
            break
        merged.append(item)
        seen.add(identity)
    return merged


def _build_quick_reply_items(items: list) -> list[QuickReplyItem]:
    """
    Build QuickReplyItem list from dicts.
    Supported types:
      - type: 'location'      → opens LINE map
      - type: 'message'       → sends a text message
      - type: 'datetimepicker'→ opens time/date picker (requires 'data', 'mode')
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
        else:  # message
            action = MessageAction(label=item["label"], text=item["text"])
        quick_reply_items.append(QuickReplyItem(action=action))
    return quick_reply_items


async def reply_text(reply_token: str, text: str) -> None:
    quick_reply_items = _build_quick_reply_items(with_persistent_quick_replies([]))
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        await line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text, quick_reply=QuickReply(items=quick_reply_items))]
            )
        )


async def reply_with_quick_reply(reply_token: str, text: str, items: list) -> None:
    await reply_multi_messages_with_quick_reply(reply_token, [text], items)


async def reply_multi_messages_with_quick_reply(reply_token: str, texts: list[str], items: list) -> None:
    quick_reply_items = _build_quick_reply_items(with_persistent_quick_replies(items))
    messages = []
    for i, t in enumerate(texts):
        # Only the last message can have quick replies attached
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


async def push_text(user_id: str, text: str) -> None:
    quick_reply_items = _build_quick_reply_items(with_persistent_quick_replies([]))
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        await line_bot_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=text, quick_reply=QuickReply(items=quick_reply_items))]
            )
        )


async def push_with_quick_reply(user_id: str, text: str, items: list) -> None:
    quick_reply_items = _build_quick_reply_items(with_persistent_quick_replies(items))
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
