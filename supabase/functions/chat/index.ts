// POST /functions/v1/chat
// body: { "message": "...", "mode"?: "simple" | "african" | "swahili", "history"?: [{role, content}] }
// returns: { answer, sources: [{ id, unit, heading, source_url, domain, similarity }] }
import { createClient } from "jsr:@supabase/supabase-js@2";
import { buddySystem, buildBuddyUserPrompt, type Chunk } from "../_shared/prompts.ts";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });

const LLM_URL = Deno.env.get("LLM_URL") ?? "https://api.groq.com/openai/v1/chat/completions";
const LLM_MODEL = Deno.env.get("LLM_MODEL") ?? "openai/gpt-oss-120b";
const LLM_API_KEY = Deno.env.get("LLM_API_KEY") ?? "";
const session = new Supabase.ai.Session("gte-small");

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  try {
    if (!LLM_API_KEY) return json({ error: "LLM_API_KEY secret is not set" }, 500);
    const { message, mode = "simple", history = [] } = await req.json();
    if (!message || typeof message !== "string") return json({ error: "message is required" }, 400);

    const sb = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
    const embedding = await session.run(message, { mean_pool: true, normalize: true });
    const { data: chunks, error } = await sb.rpc("match_chunks", {
      query_embedding: Array.from(embedding as ArrayLike<number>),
      match_count: 4,
      filter_domain: null,
    });
    if (error) return json({ error: error.message }, 500);

    const recent = Array.isArray(history) ? history.slice(-6) : [];
    const res = await fetch(LLM_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${LLM_API_KEY}` },
      body: JSON.stringify({
        model: LLM_MODEL,
        temperature: 0.3,
        messages: [
          { role: "system", content: buddySystem(mode) },
          ...recent,
          { role: "user", content: buildBuddyUserPrompt(message, (chunks ?? []) as Chunk[]) },
        ],
      }),
    });
    if (!res.ok) return json({ error: `LLM error ${res.status}: ${await res.text()}` }, 502);

    const completion = await res.json();
    const answer = completion.choices?.[0]?.message?.content ?? "";
    // deno-lint-ignore no-explicit-any
    const sources = (chunks ?? []).map((c: any) => ({
      id: c.id, unit: c.unit, heading: c.heading, source_url: c.source_url,
      domain: c.domain, similarity: c.similarity,
    }));
    return json({ answer, sources });
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});
