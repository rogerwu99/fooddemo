import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const supabaseUrl = Deno.env.get("SUPABASE_URL") || "";
const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
const resendApiKey = Deno.env.get("RESEND_API_KEY") || "";
const emailFrom = Deno.env.get("EMAIL_FROM") || "Nomi <hello@feednomi.com>";
const appUrl = Deno.env.get("APP_URL") || "https://feednomi.com/#analyzer";
const cronSecret = Deno.env.get("CRON_SECRET") || "";

type ReminderSetting = {
  user_id: string;
  email: string;
  last_reminder_sent_at: string | null;
  reminder_streak: number | null;
};

function jsonResponse(body: Record<string, unknown>, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function hoursSince(value: string | null) {
  if (!value) return Infinity;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return Infinity;
  return (Date.now() - timestamp) / (60 * 60 * 1000);
}

async function latestMealForUser(userId: string) {
  const supabase = getSupabaseClient();
  const { data, error } = await supabase
    .from("food_logs")
    .select("created_at, name")
    .eq("user_id", userId)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) throw error;
  return data as { created_at: string; name: string | null } | null;
}

function getSupabaseClient() {
  return createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false },
  });
}

async function sendReminder(email: string) {
  const response = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      authorization: `Bearer ${resendApiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      from: emailFrom,
      to: email,
      subject: "Nomi is getting hungry",
      html: `
        <div style="font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #17211d; line-height: 1.5;">
          <h1 style="font-size: 24px; margin: 0 0 12px;">Nomi has not eaten today</h1>
          <p style="margin: 0 0 16px;">Snap your next meal to help her perk back up.</p>
          <p style="margin: 0 0 20px;">
            <a href="${appUrl}" style="background: #17211d; color: #fffaf2; padding: 10px 14px; border-radius: 8px; text-decoration: none; font-weight: 700;">Feed Nomi</a>
          </p>
          <p style="color: #607067; font-size: 13px; margin: 0;">You can turn reminders off from your FeedNomi food log.</p>
        </div>
      `,
      text: `Nomi has not eaten today. Snap your next meal to help her perk back up: ${appUrl}\n\nYou can turn reminders off from your FeedNomi food log.`,
    }),
  });
  if (!response.ok) {
    throw new Error(`Resend failed with ${response.status}: ${await response.text()}`);
  }
}

Deno.serve(async (request) => {
  if (!supabaseUrl || !serviceRoleKey || !resendApiKey || !cronSecret) {
    return jsonResponse({ error: "Missing Supabase or email secrets" }, 500);
  }
  if (request.headers.get("x-cron-secret") !== cronSecret) {
    return jsonResponse({ error: "Unauthorized" }, 401);
  }
  const supabase = getSupabaseClient();

  const { data, error } = await supabase
    .from("notification_settings")
    .select("user_id, email, last_reminder_sent_at, reminder_streak")
    .eq("reminders_enabled", true)
    .limit(500);
  if (error) return jsonResponse({ error: error.message }, 500);

  let checked = 0;
  let sent = 0;
  let skipped = 0;
  const errors: string[] = [];

  for (const setting of (data || []) as ReminderSetting[]) {
    checked += 1;
    try {
      if (!setting.email || Number(setting.reminder_streak || 0) >= 3 || hoursSince(setting.last_reminder_sent_at) < 20) {
        skipped += 1;
        continue;
      }

      const latestMeal = await latestMealForUser(setting.user_id);
      if (!latestMeal || hoursSince(latestMeal.created_at) < 24) {
        if (latestMeal && setting.last_reminder_sent_at && Date.parse(latestMeal.created_at) > Date.parse(setting.last_reminder_sent_at)) {
          await supabase.from("notification_settings").update({ reminder_streak: 0 }).eq("user_id", setting.user_id);
        }
        skipped += 1;
        continue;
      }

      await sendReminder(setting.email);
      await supabase
        .from("notification_settings")
        .update({
          last_reminder_sent_at: new Date().toISOString(),
          reminder_streak: Number(setting.reminder_streak || 0) + 1,
          updated_at: new Date().toISOString(),
        })
        .eq("user_id", setting.user_id);
      sent += 1;
    } catch (error) {
      errors.push(`${setting.user_id}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return jsonResponse({ checked, sent, skipped, errors });
});
