import httpx
from app.config import LINE_CHANNEL_ACCESS_TOKEN

LINE_REPLY_API = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_API = "https://api.line.me/v2/bot/message/push"

async def reply_text(reply_token: str, text: str) -> None:
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text,
            }
        ],
    }

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            LINE_REPLY_API,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

async def push_text(user_id: str, text: str) -> None:
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": text,
            }
        ],
    }

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            LINE_PUSH_API,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()