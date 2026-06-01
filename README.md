# PlatePoints staging deploy

This folder is a single Python web service that serves `index.html`, static assets, HEIC/HEIF conversion, OpenAI vision analysis, and nutrition lookup.

## Environment variables

Required for full staging behavior:

- `OPENAI_API_KEY` - OpenAI API key for vision food identification.
- `USDA_API_KEY` - USDA FoodData Central API key for nutrition lookup.

Optional:

- `OPENAI_VISION_MODEL` - defaults to `gpt-4.1-mini`.
- `PORT` - set by most hosting platforms.
- `HOST` - defaults to `0.0.0.0`.

## Local run

```bash
cd outputs/food-fit-site
python3 -m pip install -r requirements.txt
export OPENAI_API_KEY="..."
export USDA_API_KEY="..."
python3 server.py
```

Open `http://localhost:8123`.

## Render

Use `render.yaml` or create a Python web service manually:

- Build command: `pip install -r requirements.txt`
- Start command: `python server.py`
- Add secret environment variables: `OPENAI_API_KEY`, `USDA_API_KEY`

## Railway / Fly.io

Use the same build/start commands. Make sure the service exposes the platform-provided `PORT`.
