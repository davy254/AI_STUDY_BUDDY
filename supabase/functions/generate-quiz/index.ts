// POST /functions/v1/generate-quiz
// body: { "topic": "shared responsibility model", "domain"?: "cloud_concepts", "n"?: 5, "difficulty"?: "easy|medium|hard" }
// returns: { questions: [...saved rows...], skipped: <number of invalid LLM questions dropped> }
//
// Flow: embed topic -> match_chunks (RAG) -> prompt LLM with the chunks -> validate JSON -> save to quiz_questions.
// The LLM is any OpenAI-compatible endpoint, so you can swap providers with secrets only (no code change):
//   Groq   : LLM_URL=https://api.groq.com/openai/v1/chat/completions   LLM_MODEL=openai/gpt-oss-120b
//   Gemini : LLM_URL=https://generativelanguage.googleapis.com/v1beta/openai/chat/completions  LLM_MODEL=<a Gemini model id>
import { createClient } from "jsr:@supabase/supabase-js@2";
import { buildQuizUserPrompt, type Chunk, QUIZ_SYSTEM } from "../_shared/prompts.ts";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });

const LLM_URL = Deno.env.get("LLM_URL") ?? "https://api.groq.com/openai/v1/chat/completions";
const LLM_MODEL = Deno.env.get("LLM_MODEL") ?? "openai/gpt-oss-120b";
const LLM_API_KEY = Deno.env.get("LLM_API_KEY") ?? "";

const session = new Supabase.ai.Session("gte-small");
const LETTERS = ["A", "B", "C", "D"];

// Returns a clean row, or null if the LLM's question breaks the rules.
// deno-lint-ignore no-explicit-any
function validate(q: any, allowedChunkIds: Set<number>) {
  if (!q || typeof q.question !== "string" || !q.options) return null;
  if (!LETTERS.every((l) => typeof q.options[l] === "string" && q.options[l].trim())) return null;
  if (!LETTERS.includes(q.correct)) return null;
  if (typeof q.why_correct !== "string" || !q.why_correct.trim()) return null;

  const wrongLetters = LETTERS.filter((l) => l !== q.correct);
  const why_wrong: Record<string, string> = {};
  for (const l of wrongLetters) {
    const w = q.why_wrong?.[l];
    if (typeof w !== "string" || !w.trim()) return null;
    why_wrong[l] = w;
  }

  const chunkId = Number(q.chunk_id);
  return {
    question: q.question.trim(),
    options: Object.fromEntries(LETTERS.map((l) => [l, q.options[l].trim()])),
    correct: q.correct,
    why_correct: q.why_correct.trim(),
    why_wrong,
    chunk_id: allowedChunkIds.has(chunkId) ? chunkId : null,
  };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });

  try {
    if (!LLM_API_KEY) return json({ error: "LLM_API_KEY secret is not set" }, 500);

    const { topic, domain = null, n = 5, difficulty = "medium" } = await req.json();
    if (!topic || typeof topic !== "string") return json({ error: "topic is required" }, 400);
    const count = Math.min(Math.max(Number(n) || 5, 1), 10);

    const sb = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    );

    // 1. RAG: find the notes that cover this topic
    const embedding = await session.run(topic, { mean_pool: true, normalize: true });
    const { data: chunks, error: rpcError } = await sb.rpc("match_chunks", {
      query_embedding: Array.from(embedding as ArrayLike<number>),
      match_count: 6,
      filter_domain: domain,
    });
    if (rpcError) return json({ error: rpcError.message }, 500);
    if (!chunks?.length) return json({ error: "No matching notes found for that topic" }, 404);

    // 2. Ask the LLM, grounded in those chunks
    const res = await fetch(LLM_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${LLM_API_KEY}` },
      body: JSON.stringify({
        model: LLM_MODEL,
        temperature: 0.4,
        response_format: { type: "json_object" },
        messages: [
          { role: "system", content: QUIZ_SYSTEM },
          {
            role: "user",
            content: buildQuizUserPrompt({ topic, difficulty, count, chunks: chunks as Chunk[] }),
          },
        ],
      }),
    });
    if (!res.ok) return json({ error: `LLM error ${res.status}: ${await res.text()}` }, 502);

    const completion = await res.json();
    const raw: string = completion.choices?.[0]?.message?.content ?? "";
    const cleaned = raw.replace(/```json|```/g, "").trim();

    // deno-lint-ignore no-explicit-any
    let parsed: any;
    try {
      parsed = JSON.parse(cleaned);
    } catch {
      return json({ error: "LLM did not return valid JSON", raw: cleaned.slice(0, 500) }, 502);
    }

    // 3. Validate and save
    const allowed = new Set<number>((chunks as Chunk[]).map((c) => c.id));
    const topDomain = domain ?? (chunks as Chunk[])[0].domain ?? "unknown";
    const rows = (parsed.questions ?? [])
      .map((q: unknown) => validate(q, allowed))
      .filter(Boolean)
      // deno-lint-ignore no-explicit-any
      .map((q: any) => ({ ...q, domain: topDomain, topic, difficulty }));

    const skipped = (parsed.questions?.length ?? 0) - rows.length;
    if (!rows.length) return json({ error: "No valid questions produced, try again", skipped }, 502);

    const { data: saved, error: insertError } = await sb
      .from("quiz_questions")
      .insert(rows)
      .select();
    if (insertError) return json({ error: insertError.message }, 500);

    return json({ questions: saved, skipped });
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});
