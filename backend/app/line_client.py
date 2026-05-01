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
)
from app.config import LINE_CHANNEL_ACCESS_TOKEN

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)


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
    """
    items: list of dicts with keys:
      - type: 'location' | 'message'
      - label: str  (max 20 chars for display)
      - text: str   (only for type='message')
    """
    quick_reply_items = []
    for item in items:
        if item["type"] == "location":
            action = LocationAction(label=item["label"])
        else:
            action = MessageAction(label=item["label"], text=item["text"])
        quick_reply_items.append(QuickReplyItem(action=action))

    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        await line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[
                    TextMessage(
                        text=text,
                        quick_reply=QuickReply(items=quick_reply_items)
                    )
                ]
            )
        )


async def push_text(user_id: str, text: str) -> None:
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        await line_bot_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=text)]
            )
        )


async def push_with_quick_reply(user_id: str, text: str, items: list) -> None:
    """
    items: list of dicts with keys:
      - type: 'location' | 'message'
      - label: str  (max 20 chars for display)
      - text: str   (only for type='message')
    """
    quick_reply_items = []
    for item in items:
        if item["type"] == "location":
            action = LocationAction(label=item["label"])
        else:
            action = MessageAction(label=item["label"], text=item["text"])
        quick_reply_items.append(QuickReplyItem(action=action))

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
