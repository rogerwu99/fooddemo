# FeedNomi staging deploy

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
- `SUPABASE_NOTIFICATION_SETTINGS_TABLE` - defaults to `notification_settings`.
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

Run `supabase-schema.sql` in the Supabase SQL editor. For an existing project that already has a public `food-photos` bucket, run `supabase-storage-private-migration.sql` after deploying the current app code. Before launch, run `supabase-security-audit.sql` to confirm the live table policies, bucket privacy, and photo URL shape.

Then enable Email and Google providers in Supabase Auth, and add your local/staging/live URLs to the Auth redirect URLs.

The app uses:

- Supabase Auth for email magic link and Google sign in.
- Supabase Storage bucket `food-photos` for saved meal images. New uploads are stored privately and displayed with signed URLs.
- Postgres table `food_logs` for per-user meal history.
- Postgres table `notification_settings` for opt-in Nomi reminder emails.

## Nomi reminder emails

Reminder emails are opt-in. Run `supabase-reminder-emails.sql` to create `notification_settings`.

To send reminders, deploy `supabase/functions/send-nomi-reminders` as a Supabase Edge Function and set these function secrets:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `RESEND_API_KEY`
- `EMAIL_FROM` such as `Nomi <hello@feednomi.com>`
- `APP_URL` such as `https://feednomi.com/#analyzer`
- `CRON_SECRET`

The SQL file includes a commented hourly `pg_cron` schedule. Enable it only after the function and secrets are deployed. The function sends at most one reminder every 20 hours, only after 24 hours without a saved meal, and stops after 3 missed reminders until the user feeds Nomi again.

## Render

Use `render.yaml` or create a Python web service manually:

- Build command: `pip install -r requirements.txt`
- Start command: `python server.py`
- Add secret environment variables: `OPENAI_API_KEY`, `USDA_API_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`

## Launch checklist

- Confirm `.env` is ignored and not committed.
- Confirm hosting env vars are set for OpenAI, USDA, Supabase URL, and Supabase anon key.
- Confirm Google OAuth has the staging/live callback URL configured in Google Cloud and Supabase.
- Deploy app code before running `supabase-storage-private-migration.sql`.
- Run `supabase-security-audit.sql` and confirm `food_logs` RLS is enabled, `food-photos` is private, and new rows store private photo paths rather than public URLs.
- Save a real photo while signed in, reload the food log, and confirm the thumbnail still appears.
- Sign out and confirm anonymous users cannot read/list `food-photos`.

## Railway / Fly.io

Use the same build/start commands. Make sure the service exposes the platform-provided `PORT`.
