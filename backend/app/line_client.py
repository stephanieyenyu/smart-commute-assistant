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

PERSISTENT_QUICK_REPLIES = []
LEGACY_COMMUTE_SUGGESTION_TEXT = "通勤建議"
SETTING_OVERVIEW_MARKER = "目前設定"
SETTING_OVERVIEW_HIDDEN_PREFIXES = (
    "目的地位置",
    "固定到目的地時間",
    "固定到達時間",
    "固定抵達時間",
    "固定到校時間",
    "固定到公司時間",
)


def _quick_reply_identity(item: dict) -> tuple:
    return (
        item.get("type"),
        item.get("label"),
        item.get("text"),
        item.get("data"),
    )


def _commute_suggestion_quick_reply(day_label: str) -> dict:
    text = f"{day_label}{LEGACY_COMMUTE_SUGGESTION_TEXT}"
    return {"type": "message", "label": text, "text": text}


def _is_legacy_commute_suggestion_reply(item: dict) -> bool:
    return (
        item.get("type") == "message"
        and (
            item.get("label") == LEGACY_COMMUTE_SUGGESTION_TEXT
            or item.get("text") == LEGACY_COMMUTE_SUGGESTION_TEXT
        )
    )


def _normalise_commute_suggestion_quick_replies(items: list | None) -> list:
    normalised = []
    seen = set()
    for item in list(items or []):
        replacement_items = (
            [_commute_suggestion_quick_reply("今日"), _commute_suggestion_quick_reply("明日")]
            if _is_legacy_commute_suggestion_reply(item)
            else [item]
        )
        for replacement in replacement_items:
            identity = _quick_reply_identity(replacement)
            if identity in seen:
                continue
            normalised.append(replacement)
            seen.add(identity)
    return normalised


def with_persistent_quick_replies(items: list | None) -> list:
    merged = _normalise_commute_suggestion_quick_replies(items)
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


def _quick_reply_model(items: list | None) -> QuickReply | None:
    try:
        quick_reply_items = _build_quick_reply_items(with_persistent_quick_replies(items))
    except Exception as e:
        print(f"[line] quick reply model error: {e}")
        return None
    if not quick_reply_items:
        return None
    return QuickReply(items=quick_reply_items)


def _quick_reply_action_payload(item: dict) -> dict:
    t = item["type"]
    if t == "location":
        return {"type": "location", "label": item["label"]}
    if t == "datetimepicker":
        return {
            "type": "datetimepicker",
            "label": item["label"],
            "data": item.get("data", "postback"),
            "mode": item.get("mode", "time"),
        }
    return {"type": "message", "label": item["label"], "text": item["text"]}


def _quick_reply_payload(items: list | None) -> dict | None:
    try:
        quick_reply_items = [
            {"type": "action", "action": _quick_reply_action_payload(item)}
            for item in with_persistent_quick_replies(items)
        ]
    except Exception as e:
        print(f"[line] quick reply payload error: {e}")
        return None
    if not quick_reply_items:
        return None
    return {"items": quick_reply_items}


def _clean_settings_line_start(line: str) -> str:
    return line.strip().lstrip("-•・▪️ ").strip()


def _is_hidden_settings_overview_line(line: str) -> bool:
    clean_line = _clean_settings_line_start(line)
    return any(clean_line.startswith(prefix) for prefix in SETTING_OVERVIEW_HIDDEN_PREFIXES)


def _looks_like_settings_overview(text: str) -> bool:
    return (
        SETTING_OVERVIEW_MARKER in text
        and (
            "住家" in text
            or "提醒" in text
            or "排程" in text
            or "交通" in text
        )
    )


def sanitise_outbound_text(text: str) -> str:
    if not isinstance(text, str) or not _looks_like_settings_overview(text):
        return text
    return "\n".join(
        line for line in text.splitlines()
        if not _is_hidden_settings_overview_line(line)
    ).strip()


async def reply_flex_with_quick_reply(reply_token: str, alt_text: str, contents: dict, items: list | None = None) -> None:
    alt_text = sanitise_outbound_text(alt_text)
    message = {
        "type": "flex",
        "altText": alt_text,
        "contents": contents,
    }
    quick_reply = _quick_reply_payload(items)
    if quick_reply:
        message["quickReply"] = quick_reply

    payload = {
        "replyToken": reply_token,
        "messages": [message],
    }
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
            response = await client.post(
                "https://api.line.me/v2/bot/message/reply",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
    except Exception as e:
        print(f"[line] reply_flex_with_quick_reply error: {e}")
        await reply_with_quick_reply(reply_token, alt_text, items or [])


async def reply_text(reply_token: str, text: str) -> None:
    text = sanitise_outbound_text(text)
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}],
    }
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
            response = await client.post(
                "https://api.line.me/v2/bot/message/reply",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
    except Exception as e:
        print(f"[line] reply_text error: {e}")


async def reply_with_quick_reply(reply_token: str, text: str, items: list) -> None:
    await reply_multi_messages_with_quick_reply(reply_token, [text], items)


async def reply_multi_messages_with_quick_reply(reply_token: str, texts: list[str], items: list) -> None:
    quick_reply = _quick_reply_payload(items)
    messages = []
    for i, t in enumerate(texts):
        qr = quick_reply if i == len(texts) - 1 else None
        msg = {"type": "text", "text": sanitise_outbound_text(t)}
        if qr:
            msg["quickReply"] = qr
        messages.append(msg)

    payload = {
        "replyToken": reply_token,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
            response = await client.post(
                "https://api.line.me/v2/bot/message/reply",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
    except Exception as e:
        print(f"[line] reply_multi_messages_with_quick_reply error: {e}")
        # Fallback: try sending just the first text without quick replies
        if texts:
            fallback_payload = {
                "replyToken": reply_token,
                "messages": [{"type": "text", "text": sanitise_outbound_text(texts[0])}],
            }
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0)) as client:
                    await client.post(
                        "https://api.line.me/v2/bot/message/reply",
                        json=fallback_payload,
                        headers=headers,
                    )
            except Exception as fallback_error:
                print(f"[line] reply_multi fallback error: {fallback_error}")


async def push_text(user_id: str, text: str) -> None:
    text = sanitise_outbound_text(text)
    payload = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}],
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
        print(f"[line] push_text error: {e}")


async def push_with_quick_reply(user_id: str, text: str, items: list) -> None:
    quick_reply = _quick_reply_payload(items)
    msg = {"type": "text", "text": sanitise_outbound_text(text)}
    if quick_reply:
        msg["quickReply"] = quick_reply

    payload = {
        "to": user_id,
        "messages": [msg],
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
