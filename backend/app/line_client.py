<<<<<<< HEAD
from linebot.v3.messaging import (
    Configuration,
    AsyncApiClient,
    AsyncMessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
)
=======
import httpx

>>>>>>> cb646c664c1b63374efeeb9cc188560a21e05b4a
from app.config import LINE_CHANNEL_ACCESS_TOKEN

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

<<<<<<< HEAD
async def reply_text(reply_token: str, text: str) -> None:
    async with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        await line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)]
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
=======

def _chunk_text(text: str, limit: int = 4800) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
        current += line

    if current:
        chunks.append(current.rstrip())

    if not chunks:
        chunks = [text[:limit]]

    return chunks[:5]


def _build_headers():
    return {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


async def reply_text(reply_token: str, text: str):
    headers = _build_headers()
    messages = [{"type": "text", "text": chunk} for chunk in _chunk_text(text)]

    body = {
        "replyToken": reply_token,
        "messages": messages,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0), verify=False) as client:
        response = await client.post(LINE_REPLY_API, headers=headers, json=body)
        if response.status_code >= 400:
            print(f"[line-reply] failed status={response.status_code} body={response.text}")
        response.raise_for_status()
        return response.json() if response.content else {"ok": True}


async def push_text(line_user_id: str, text: str):
    headers = _build_headers()
    messages = [{"type": "text", "text": chunk} for chunk in _chunk_text(text)]

    body = {
        "to": line_user_id,
        "messages": messages,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0), verify=False) as client:
        response = await client.post(LINE_PUSH_API, headers=headers, json=body)
        if response.status_code >= 400:
            print(f"[line-push] failed status={response.status_code} body={response.text}")
        response.raise_for_status()
        return response.json() if response.content else {"ok": True}
>>>>>>> cb646c664c1b63374efeeb9cc188560a21e05b4a
