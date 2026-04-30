from linebot.v3.messaging import (
    Configuration,
    AsyncApiClient,
    AsyncMessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
)
from linebot.v3.messaging.models import (
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
      - label: str  (displayed on button, max 20 chars)
      - text: str   (only for type='message', the text sent when tapped)
    """
    quick_reply_items = []
    for item in items:
        try:
            if item["type"] == "location":
                action = LocationAction(label=item["label"])
            else:
                action = MessageAction(label=item["label"], text=item["text"])
            quick_reply_items.append(QuickReplyItem(action=action))
        except Exception as e:
            print(f"[quick-reply] item build failed: {e}")

    # Fallback to plain text if quick reply build failed
    if not quick_reply_items:
        await reply_text(reply_token, text)
        return

    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        try:
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
        except Exception as e:
            print(f"[quick-reply] send failed: {e}, falling back to plain text")
            await reply_text(reply_token, text)


async def push_text(user_id: str, text: str) -> None:
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        await line_bot_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=text)]
            )
        )
