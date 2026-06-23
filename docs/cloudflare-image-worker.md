# Cloudflare AI Worker for `generate_image` (provider: cloudflare)

gaia's `generate_image` tool can use a custom **Cloudflare Worker** fronting
`@cf/stabilityai/stable-diffusion-xl-base-1.0` as an image backend. The tool POSTs

```json
{ "prompt": "...", "width": 1280, "height": 720, "negative_prompt": "...", "num_steps": 20, "guidance": 7.5, "seed": 123 }
```

with `Authorization: Bearer <token>` and expects **jpeg bytes** back.

The original worker only read `{ prompt }`, so it **ignored** every other field. Deploy the
version below to forward the SDXL knobs gaia sends (only the fields present in the request are
passed through, so it stays backward-compatible). Token check, POST-only routing, and the `json()`
helper are unchanged.

## gaia config

`.env`:
```
GAIA_CLOUDFLARE_AI_TOKEN=<the same value as the Worker's API_KEY>
```
`gaia.yaml`:
```yaml
tools:
  generate_image:
    provider: cloudflare
    cloudflare_url: https://<your-worker>.workers.dev
    # optional SDXL defaults (forwarded by the worker below):
    # num_steps: 20      # SDXL caps this at 20
    # guidance: 7.5
    # width: 1024        # else derived from the call's aspect_ratio
    # height: 1024
    # seed: 42
```

## worker.js

```javascript
export default {
    async fetch(request, env) {
        const API_KEY = env.API_KEY;
        const url = new URL(request.url);
        const auth = request.headers.get("Authorization");

        // 🔐 Simple API key check
        if (auth !== `Bearer ${API_KEY}`) {
            return json({ error: "Unauthorized" }, 401);
        }

        // 🚫 Only allow POST requests to /
        if (request.method !== "POST" || url.pathname !== "/") {
            return json({ error: "Not allowed" }, 405);
        }

        try {
            const body = await request.json();
            const { prompt, negative_prompt, num_steps, guidance, width, height, seed } = body;

            if (!prompt) return json({ error: "Prompt is required" }, 400);

            // Forward only the fields that were sent, clamped to SDXL's accepted ranges.
            const inputs = { prompt };
            if (negative_prompt) inputs.negative_prompt = negative_prompt;
            if (num_steps != null) inputs.num_steps = Math.min(Math.max(1, num_steps | 0), 20);
            if (guidance != null) inputs.guidance = Number(guidance);
            if (width != null) inputs.width = Math.min(Math.max(256, width | 0), 2048);
            if (height != null) inputs.height = Math.min(Math.max(256, height | 0), 2048);
            if (seed != null) inputs.seed = seed | 0;

            // 🧠 Generate image from prompt (SDXL returns a JPEG stream)
            const result = await env.AI.run(
                "@cf/stabilityai/stable-diffusion-xl-base-1.0",
                inputs
            );

            return new Response(result, {
                headers: { "Content-Type": "image/jpeg" },
            });
        } catch (err) {
            return json({ error: "Failed to generate image", details: err.message }, 500);
        }
    },
};

// 📦 Function to return JSON responses
function json(data, status = 200) {
    return new Response(JSON.stringify(data), {
        status,
        headers: { "Content-Type": "application/json" },
    });
}
```

Deploy (e.g. `wrangler deploy`), keep the `API_KEY` secret in the Worker env matching
`GAIA_CLOUDFLARE_AI_TOKEN`, and gaia's `generate_image` will render with your model.
