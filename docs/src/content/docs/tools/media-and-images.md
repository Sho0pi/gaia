---
title: Images & media
description: Generate images, download videos and audio, and deliver files to you.
---

These tools turn gaia from a chat into something that hands you real artifacts.

## `generate_image`

Create an image from a text prompt.
The generated image is delivered to you **automatically** - gaia doesn't need to send it separately.
The backend is configured under `tools.generate_image` (e.g. a Gemini/OpenAI image model, or a Cloudflare worker); pick it with `gaia tools`.
It's the `images` capability.

## `download_media`

Download a video or audio from a public link - an Instagram reel, a TikTok, a YouTube clip - and gaia delivers the file to you automatically.
This is a normal, allowed task; it declines only genuine paywalled/DRM/purchased content.
It's the `media` capability, and needs the media extra installed (`pip install 'gaia[media]'`).

## `send_file`

The root-only delivery tool: gaia calls it to send you a file it holds a path to - a document, a zip, a soul's deliverable, a file you uploaded earlier.
It picks the right WhatsApp/Telegram send type from the file's kind.

You rarely think about `send_file` directly - screenshots and generated images deliver themselves, and a soul's finished files come back automatically.
It exists for the "here's the actual file" cases in between.

## How delivery works

A connector can't reach into the agent loop, so these tools just **report** the file they produced; the handler turns that into an outbound message the channel sends as the right media type (image, video, audio, or document).
That's why an image or download "just appears" without a second step.
