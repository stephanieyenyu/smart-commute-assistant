import httpx

from app.config import LINE_CHANNEL_ACCESS_TOKEN

LINE_REPLY_API = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_API = "https://api.line.me/v2/bot/message/push"


def _build_headers():
    return {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


async def reply_text(reply_token: str, text: str):
    headers = _build_headers()
    body = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text,
            }
        ],
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(LINE_REPLY_API, headers=headers, json=body)
        response.raise_for_status()
        return response.json() if response.content else {"ok": True}


async def push_text(line_user_id: str, text: str):
    headers = _build_headers()
    body = {
        "to": line_user_id,
        "messages": [
            {
                "type": "text",
                "text": text,
            }
        ],
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(LINE_PUSH_API, headers=headers, json=body)
        response.raise_for_status()
        return response.json() if response.content else {"ok": True}