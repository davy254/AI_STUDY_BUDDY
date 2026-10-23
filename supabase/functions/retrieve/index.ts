// POST /functions/v1/retrieve
// body: { "query": "what is the shared responsibility model?", "domain"?: "cloud_concepts", "k"?: 5 }
// returns: { chunks: [{ id, content, module, unit, heading, source_url, domain, similarity }] }
//
// Embeds the query with Supabase's built-in gte-small (free, no API key) and
// searches the pgvector table via the match_chunks() SQL function.
import { createClient } from "jsr:@supabase/supabase-js@2";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });

const session = new Supabase.ai.Session("gte-small");

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });

  try {
    const { query, domain = null, k = 5 } = await req.json();
    if (!query || typeof query !== "string") return json({ error: "query is required" }, 400);

    const embedding = await session.run(query, { mean_pool: true, normalize: true });

    const sb = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    );
    const { data, error } = await sb.rpc("match_chunks", {
      query_embedding: Array.from(embedding as ArrayLike<number>),
      match_count: Math.min(Math.max(Number(k) || 5, 1), 12),
      filter_domain: domain,
    });
    if (error) return json({ error: error.message }, 500);

    return json({ chunks: data });
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});
