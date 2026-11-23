"""Spike: does a photo work in a paused, then unstored, turn?

For the own loop (model=) and the prompt agent (agent_reference):
  1. A stored call that ends in a function call.
  2. A store=False call on the same conversation with
     [function_call_output (text), user message (label + input_image)].
  3. Record [function_call_output (same text), assistant message] with
     conversations.items.create.
  4. A normal stored turn that asks what was said.

Needs FOUNDRY_PROJECT_ENDPOINT and CHAT_DEPLOYMENT. Keyless (AzureCliCredential).
Temporary conversations are deleted at the end, also on failure.
"""

import asyncio
import base64
import io
import os

from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import AzureCliCredential
from PIL import Image, ImageDraw, ImageFont

SIGN_TEXT = "Tors 0-6"
TOOL_TEXT = "Photo taken."
PROMPT_AGENT = "voltrieve-prompt-web"
FONTS = ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"]


def draw_sign() -> str:
    """Draw a made-up parking sign, 1,600 px wide, as a data URL."""
    img = Image.new("RGB", (1600, 1000), (60, 90, 140))
    d = ImageDraw.Draw(img)
    d.rectangle((300, 200, 1300, 800), fill="white", outline="black", width=12)
    font = ImageFont.load_default(size=220)
    for path in FONTS:
        if os.path.exists(path):
            font = ImageFont.truetype(path, 220)
            break
    d.text((800, 500), SIGN_TEXT, fill="black", font=font, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def msg(text, *parts):
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}, *parts]}


async def run_case(oc, name, base, tools, ask, image, convs):
    conv = (await oc.conversations.create()).id
    convs.append(conv)
    # 1. Stored call that ends in a function call.
    r1 = await oc.responses.create(conversation=conv, input=ask, **base, **tools)
    calls = [o for o in r1.output if o.type == "function_call"]
    if not calls:
        return name, "no function call in step 1", None, None
    call_id = calls[0].call_id
    # 2. store=False: the tool output as text, then the photo as a user message.
    items = [
        {"type": "function_call_output", "call_id": call_id, "output": TOOL_TEXT},
        msg("Here is the photo from the tool. Copy the text on the sign.", {"type": "input_image", "detail": "high", "image_url": image}),
    ]
    r2 = await oc.responses.create(conversation=conv, store=False, input=items, **base, **tools)
    answer = r2.output_text
    read_ok = SIGN_TEXT.lower().replace(" ", "") in answer.lower().replace(" ", "")
    # 3. Record the paused turn without the photo.
    rec_ok = True
    try:
        await oc.conversations.items.create(
            conv,
            items=[
                {"type": "function_call_output", "call_id": call_id, "output": TOOL_TEXT},
                {"type": "message", "role": "assistant", "content": answer},
            ],
        )
    except Exception as x:
        rec_ok = False
        print(f"  {name}: record failed: {type(x).__name__}: {str(x)[:200]}")
    # 4. A normal stored turn that asks what was said.
    rem_ok = False
    if rec_ok:
        r4 = await oc.responses.create(
            conversation=conv,
            input="What did you just tell me about the sign? Repeat the sign text.",
            **base,
            **tools,
        )
        rem_ok = SIGN_TEXT.lower().replace(" ", "") in r4.output_text.lower().replace(" ", "")
        print(f"  {name} step 4 answer: {r4.output_text[:120]!r}")
    print(f"  {name} step 2 answer: {answer[:120]!r}")
    return name, None, (read_ok, rec_ok, rem_ok), None


async def main():
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    model = os.environ["CHAT_DEPLOYMENT"]
    image = draw_sign()
    take_photo = {
        "tools": [
            {
                "type": "function",
                "name": "take_photo",
                "description": "Take a photo with the car's camera.",
                "parameters": {"type": "object", "properties": {}},
                "strict": False,
            }
        ]
    }
    cases = [
        ("loop", dict(model=model, reasoning={"effort": "medium"}), take_photo, "Use the camera, then tell me only the text on the sign."),
        ("prompt", dict(extra_body={"agent_reference": {"name": PROMPT_AGENT, "type": "agent_reference"}}), {}, "Switch to the dark theme."),
    ]
    convs = []
    async with AzureCliCredential() as cred, AIProjectClient(endpoint=endpoint, credential=cred) as p:
        oc = p.get_openai_client()
        try:
            for name, base, tools, ask in cases:
                try:
                    _, err, res, _ = await run_case(oc, name, base, tools, ask, image, convs)
                except Exception as x:
                    err, res = f"{type(x).__name__}: {str(x)[:200]}", None
                if err or res is None:
                    print(f"{name}: FAILED ({err})")
                else:
                    word = lambda ok, good, bad: good if ok else bad
                    print(
                        f"{name}: {word(res[0], 'read ok', 'read FAILED')}, "
                        f"{word(res[1], 'recorded ok', 'recorded FAILED')}, "
                        f"{word(res[2], 'remembered ok', 'remembered FAILED')}"
                    )
        finally:
            for c in convs:
                try:
                    await oc.conversations.delete(c)
                except Exception as x:
                    print("cleanup failed:", str(x)[:80])
            print("conversations deleted:", len(convs))
            await oc.close()


asyncio.run(main())
