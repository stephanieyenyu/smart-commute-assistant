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
import httpx

from app.config import LINE_CHANNEL_ACCESS_TOKEN

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
LINE_QUICK_REPLY_LIMIT = 13

PERSISTENT_QUICK_REPLIES = [
    {"type": "message", "label": "今日通勤建議", "text": "今日通勤建議"},
    {"type": "message", "label": "修改到公司時間", "text": "修改到公司時間"},
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


def build_departure_check_flex_message() -> dict:
    return {
        "type": "flex",
        "altText": "您出門了嗎？",
        "contents": {
            "type": "bubble",
            "size": "mega",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": "您出門了嗎？",
                        "weight": "bold",
                        "size": "xl",
                        "color": "#111111",
                    },
                    {
                        "type": "text",
                        "text": "如果還需要時間，我會五分鐘後再提醒一次。",
                        "size": "sm",
                        "color": "#666666",
                        "wrap": True,
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#1fb86a",
                        "action": {
                            "type": "postback",
                            "label": "已經出門了",
                            "data": "action=departure_check&choice=left",
                            "displayText": "已經出門了",
                        },
                    },
                    {
                        "type": "button",
                        "style": "secondary",
                        "action": {
                            "type": "postback",
                            "label": "我還需要五分鐘",
                            "data": "action=departure_check&choice=need_5",
                            "displayText": "我還需要五分鐘",
                        },
                    },
                ],
            },
        },
    }


async def push_departure_check_message(user_id: str) -> None:
    payload = {
        "to": user_id,
        "messages": [build_departure_check_flex_message()],
    }
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
            response = await client.post(
                "https://api.line.me/v2/bot/message/push",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
    except Exception as e:
        print(f"[line] push_departure_check_message error: {e}")
        await push_with_quick_reply(
            user_id,
            "您出門了嗎？",
            [
                {"type": "message", "label": "已經出門了", "text": "已經出門了"},
                {"type": "message", "label": "我還需要五分鐘", "text": "我還需要五分鐘"},
            ],
        )
