# Paste this into Lovable.dev (or Bolt.new)

Build a mobile-friendly web app called **"AZ-900 Study Buddy (Unofficial)"** for learners preparing for the Microsoft Azure Fundamentals exam. Show a small footer: "Unofficial study tool. Not affiliated with Microsoft. Notes from Microsoft Learn." Do NOT use any Microsoft logos. Clean, friendly, modern design, dark-blue and teal accents, rounded cards.

## Backend (already built, connect to my existing Supabase project)
Use the Supabase JS client with my project URL and anon key.
- On first load call `supabase.auth.signInAnonymously()` so every learner gets a private user id.
- Tables: `quiz_questions(id, domain, topic, difficulty, question, options jsonb {A,B,C,D}, correct, why_correct, why_wrong jsonb)`, `attempts(question_id, domain, topic, selected, is_correct)`, `flashcards(id, domain, topic, front, back, ease, interval_days, repetitions, due_at, lapses)`.
- Views: `readiness_by_domain(domain, attempts, accuracy_pct)` and `readiness_by_topic(domain, topic, attempts, accuracy_pct)`.
- Edge function `chat` via `supabase.functions.invoke('chat', { body: { message, mode, history } })` returns `{ answer, sources: [{id, unit, heading, source_url, domain}] }`. `mode` is "simple", "african" or "swahili".
- Domains: `cloud_concepts` = "Cloud Concepts", `architecture_services` = "Azure Architecture & Services", `management_governance` = "Management & Governance".

## Screens (bottom tab bar on mobile)
1. **Quiz**: choose a domain (or "All") and a topic. Load 5 random questions from `quiz_questions`. Show one at a time with 4 options. On answer: instantly highlight correct (green) and wrong (red), then slide open a panel with `why_correct` and the `why_wrong` text for every wrong option. Insert a row into `attempts`. Show a progress bar, and confetti on a streak of 3 correct. At the end show the score. **If the answer is wrong, also insert a flashcard** (front = question, back = correct option text + why_correct) into `flashcards` unless one with the same front already exists.
2. **Flashcards**: show cards where `due_at <= now()`, wrongest first (order by `lapses` desc). Tap to flip with a 3D flip animation. Buttons "Hard" / "Good" / "Easy" that update the card with simple SM-2 spaced repetition (Hard: lapses+1, interval 1 day, ease -0.2; Good: interval = previous*ease; Easy: interval = previous*ease*1.3, ease +0.15; set `due_at`). Show a "cards due today" counter.
3. **Study Buddy**: chat UI calling the `chat` function. A toggle above the input: "Simple" / "🌍 African example" / "Kiswahili". Under each answer show tappable **source chips** (the `unit` names) that link to `source_url`. Show a typing indicator while waiting and a few example question chips ("What is the shared responsibility model?", "Difference between IaaS and PaaS?").
4. **Readiness**: an animated circular progress ring with overall readiness % (average of `readiness_by_domain.accuracy_pct`, shown as "Not enough data yet" below 10 attempts). Below it, three domain progress bars, and a radar chart across the three domains. List the 3 weakest topics from `readiness_by_topic` (lowest accuracy, at least 2 attempts) as a "Weak spots" card with a "Practice this" button that opens the Quiz filtered to that topic. Label: ≥75% "Exam ready", 50-74% "Almost there", <50% "Keep practicing".

## Weak-spot banner
After a quiz ends, if the learner missed questions, show a banner: "You missed N questions on <topic>. We added N flashcards." with a button to open Flashcards.

Keep the code simple and readable. Handle loading and empty states.
