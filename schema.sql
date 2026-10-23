-- AZ-900 Study App (UNOFFICIAL) - Supabase schema
-- Run this in Supabase Dashboard > SQL Editor.
-- Embeddings are 384-dim (gte-small) so they match Supabase's built-in
-- Edge Function embedder and the sentence-transformers model in ingest.py.

create extension if not exists vector;

-- 1. RAG notes ---------------------------------------------------------------
create table if not exists chunks (
  id           bigint generated always as identity primary key,
  content      text not null,
  module       text,                 -- e.g. "describe-azure-compute"
  unit         text,                 -- file / unit title
  heading      text,                 -- nearest heading
  chunk_index  int  default 0,       -- order inside the file
  source_file  text not null,        -- for idempotent re-ingestion
  source_url   text,                 -- link back to the original note
  domain       text,                 -- cloud_concepts | architecture_services | management_governance
  embedding    vector(384)
);

create index if not exists chunks_embedding_idx
  on chunks using hnsw (embedding vector_cosine_ops);
create index if not exists chunks_domain_idx on chunks (domain);
create index if not exists chunks_source_idx on chunks (source_file);

-- Similarity search used by the /retrieve edge function
create or replace function match_chunks(
  query_embedding vector(384),
  match_count     int  default 5,
  filter_domain   text default null
)
returns table (
  id bigint, content text, module text, unit text,
  heading text, source_url text, domain text, similarity float
)
language sql stable
as $$
  select c.id, c.content, c.module, c.unit, c.heading, c.source_url, c.domain,
         1 - (c.embedding <=> query_embedding) as similarity
  from chunks c
  where filter_domain is null or c.domain = filter_domain
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

-- "Retrieve all notes": the full course in reading order
create or replace view all_notes as
  select id, domain, module, unit, heading, chunk_index, content, source_url
  from chunks
  order by domain, module, unit, chunk_index;

-- 2. Pre-generated quiz bank --------------------------------------------------
create table if not exists quiz_questions (
  id           bigint generated always as identity primary key,
  domain       text not null,
  topic        text not null,
  difficulty   text default 'medium',
  question     text not null,
  options      jsonb not null,       -- {"A":"..","B":"..","C":"..","D":".."}
  correct      text not null,        -- "A" | "B" | "C" | "D"
  why_correct  text not null,
  why_wrong    jsonb not null,       -- {"A":"..","C":"..","D":".."}
  chunk_id     bigint references chunks(id) on delete set null,
  created_at   timestamptz default now()
);
create index if not exists quiz_topic_idx on quiz_questions (domain, topic);

-- 3. Attempts (feeds Readiness Tracker + weak-spot logic) ----------------------
create table if not exists attempts (
  id           bigint generated always as identity primary key,
  user_id      uuid not null default auth.uid() references auth.users(id) on delete cascade,
  question_id  bigint references quiz_questions(id) on delete cascade,
  domain       text not null,
  topic        text not null,
  selected     text not null,
  is_correct   boolean not null,
  created_at   timestamptz default now()
);
create index if not exists attempts_user_idx on attempts (user_id, domain, topic);

-- 4. Flashcards with SM-2 spaced repetition ------------------------------------
create table if not exists flashcards (
  id            bigint generated always as identity primary key,
  user_id       uuid default auth.uid() references auth.users(id) on delete cascade, -- null = shared starter deck
  domain        text not null,
  topic         text not null,
  front         text not null,
  back          text not null,
  chunk_id      bigint references chunks(id) on delete set null,
  ease          real default 2.5,
  interval_days int  default 0,
  repetitions   int  default 0,
  due_at        timestamptz default now(),
  lapses        int  default 0       -- times marked "hard"/wrong; drives weak-spot priority
);
create index if not exists flashcards_due_idx on flashcards (user_id, due_at);

-- 5. Readiness view: accuracy per domain and topic per user ---------------------
create or replace view readiness_by_topic as
  select user_id, domain, topic,
         count(*)                                           as attempts,
         round(100.0 * avg(is_correct::int), 1)             as accuracy_pct
  from attempts
  group by user_id, domain, topic;

create or replace view readiness_by_domain as
  select user_id, domain,
         count(*)                                           as attempts,
         round(100.0 * avg(is_correct::int), 1)             as accuracy_pct
  from attempts
  group by user_id, domain;

-- 6. Row Level Security -----------------------------------------------------------
alter table chunks         enable row level security;
alter table quiz_questions enable row level security;
alter table attempts       enable row level security;
alter table flashcards     enable row level security;

create policy "notes are public read"     on chunks         for select using (true);
create policy "questions are public read" on quiz_questions for select using (true);

create policy "own attempts"  on attempts   for all
  using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy "own or shared flashcards read" on flashcards for select
  using (user_id = auth.uid() or user_id is null);
create policy "own flashcards write"  on flashcards for all
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- Views run with the caller's rights so RLS on attempts applies
alter view readiness_by_topic  set (security_invoker = on);
alter view readiness_by_domain set (security_invoker = on);
