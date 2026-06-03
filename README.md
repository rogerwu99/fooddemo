# PlatePoints staging deploy

This folder is a single Python web service that serves `index.html`, static assets, HEIC/HEIF conversion, OpenAI vision analysis, and nutrition lookup.

## Environment variables

Required for full staging behavior:

- `OPENAI_API_KEY` - OpenAI API key for vision food identification.
- `USDA_API_KEY` - USDA FoodData Central API key for nutrition lookup.
- `SUPABASE_URL` - Supabase project URL for real auth, storage, and food logs.
- `SUPABASE_ANON_KEY` - Supabase public anon key.

Optional:

- `OPENAI_VISION_MODEL` - defaults to `gpt-4.1-mini`.
- `SUPABASE_FOOD_LOG_TABLE` - defaults to `food_logs`.
- `SUPABASE_PHOTO_BUCKET` - defaults to `food-photos`.
- `PORT` - set by most hosting platforms.
- `HOST` - defaults to `0.0.0.0`.

## Local run

```bash
cd outputs/food-fit-site
python3 -m pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your real keys.
python3 server.py
```

Open `http://localhost:8123`.

You can also use exported environment variables instead of `.env`; exported values take priority.

## Supabase setup

Run `supabase-schema.sql` in the Supabase SQL editor. Then enable Email and Google providers in Supabase Auth, and add your local/staging URL to the Auth redirect URLs.

The app uses:

- Supabase Auth for email magic link and Google sign in.
- Supabase Storage bucket `food-photos` for saved meal images.
- Postgres table `food_logs` for per-user meal history.

## Render

Use `render.yaml` or create a Python web service manually:

- Build command: `pip install -r requirements.txt`
- Start command: `python server.py`
- Add secret environment variables: `OPENAI_API_KEY`, `USDA_API_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`

## Railway / Fly.io

Use the same build/start commands. Make sure the service exposes the platform-provided `PORT`.
