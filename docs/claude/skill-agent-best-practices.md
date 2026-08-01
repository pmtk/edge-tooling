# Writing Effective Skills and Agents

> **Last updated:** 2026-07-31
> **Update mechanism:** Run `/best-practices update` to refresh this document with new research.
> **Companion:** [SKILL-GUIDELINES.md](../../plugins/docs/SKILL-GUIDELINES.md) covers structural quality (frontmatter, linting, safety). This document covers *cognitive effectiveness* — how to write instructions that LLMs follow reliably.

---

## Core Principle: Every Token Competes

LLMs have a finite attention budget. Every token in a skill — instructions, templates, edge cases, formatting rules — competes with the actual task for that budget [^1]. The best skill achieves its goal with the **minimum necessary structure** [^2].

Key numbers that govern skill design:

| Constraint | Value | Implication |
|---|---|---|
| Startup load | Name + description only (~50-100 tokens) [^3] | Descriptions must be self-sufficient triggers |
| Description cap | 1,536 characters [^3] | Front-load the "when to use" signal |
| Post-compaction budget | 5,000 tokens per skill, 25,000 total [^3] | Only the first ~100 lines survive compaction |
| Practical safe context | 150K-400K tokens for high-accuracy work [^4] | Skills share this with task content, tool output, and conversation |
| Instruction-following accuracy | <30% perfect in agentic scenarios [^5] | Fewer, clearer instructions beat comprehensive coverage |

## The Brevity Imperative

Reasoning performance starts degrading at ~3,000 tokens of instruction [^6]. Agent instructions of 1,000-1,500 characters (250-375 tokens) consistently outperform 6,000+ character instructions across orchestration accuracy, tool selection, and response coherence [^7]. Most teams find agents perform better after removing roughly 60% of instruction content [^7].

**The litmus test:** For every line, ask "Would the agent get this wrong without this instruction?" [^8]. If removing the line wouldn't cause a mistake, cut it.

What to cut:

- **Things the model already knows** — HTTP, YAML syntax, how `grep` works, coding conventions it was trained on [^8]
- **Restated instructions** — the same rule said in the intro, again in a step, and again in "Notes" at the bottom. Say it once, in the step where it matters [^9]
- **Tone and personality guidance** — devoting ~40% of instructions to "be helpful, professional" reduces available signal for actual task execution [^7]
- **Menus of equivalent options** — pick one default, mention alternatives in one line [^8]
- **Vague aspirations** — "write clean code" and "handle errors properly" are not actionable instructions [^10]

What to keep:

- **Gotchas** — the highest-signal content in any skill. Environment-specific corrections to mistakes the agent *will* make [^11]
- **Exact commands for fragile operations** — match specificity to task fragility. Open field = general direction. Narrow bridge with cliffs = exact commands [^12]
- **The "why" behind constraints** — explaining motivation helps the model generalize to edge cases [^2] [^13]
- **Stop conditions** — without them, agents run indefinitely. "If you don't find the perfect source, that's okay" [^14]

## Position and Structure

### Lost in the Middle

LLMs exhibit a U-shaped attention curve: they attend most strongly to content at the **beginning** and **end** of input, with a 15-25 percentage point accuracy drop for content in the middle [^15]. This persists in 2025-2026 frontier models [^4] [^16]. The architectural cause — positional encoding decay — means this is structural, not a training gap [^17].

**Practical implications:**

- Put the most critical rules in the first 3-5 lines of the skill body
- If a rule is truly non-negotiable, repeat it at the end (but sparingly — redundancy has costs too)
- Structure long skills so the middle contains reference material (tables, templates), not critical instructions
- For non-negotiable behaviors, use hooks instead of instructions [^18]

### Formatting That Helps

- **Markdown headers** create semantic boundaries the model uses for parsing [^2]
- **Bulleted lists** improve instruction following over prose paragraphs [^19]
- **Tables** are denser and less ambiguous than prose for decision logic, configuration, and argument mapping
- **Bold** for emphasis is unreliable — "an LLM's ability to interpret what emphasis means is surprisingly weak" [^19]. Reserve `IMPORTANT`/`CRITICAL` for 2-3 rules maximum [^18] [^20]
- **Consistent terminology** — pick one term and use it throughout. Alternating between "API endpoint", "URL", "API route", and "path" confuses the parser [^12]

### What NOT to Use

- **ALL-CAPS shouting** — Opus 4.5/4.6 are more responsive to system prompts than previous models. Aggressive emphasis now causes *overtriggering* [^20]
- **JSON for document/instruction content** — Markdown is ~15% more token-efficient and performs better for reasoning tasks [^21]. JSON performs "particularly poorly" for document collections [^22]

## Progressive Disclosure

Think of the file system as context engineering [^11]. The SKILL.md should be the executive summary; detailed reference material goes in adjacent files that the skill reads conditionally.

**Pattern:**

```text
skill-name/
  SKILL.md          ← Core workflow (~100-300 lines)
  references/
    api-errors.md   ← Read only if API returns non-200
    templates.md    ← Read only when generating output
```

Keep references **one level deep** from SKILL.md — Claude partially reads files referenced from other referenced files [^12].

### Orchestrator/Sub-Agent Separation

For complex workflows, the orchestrator skill handles data fetching and control flow; analysis goes to a sub-agent skill with `user-invocable: false` and limited `allowed-tools`. The sub-agent gets a clean, focused context (~80 lines) instead of inheriting the orchestrator's full state [^23].

Each sub-agent uses tens of thousands of tokens internally but returns only a condensed summary (1,000-2,000 tokens), showing substantial improvement over single-agent systems [^1].

## Positive Over Negative

Tell the model what TO do, not what NOT to do [^2] [^22]. Negations are harder to follow and the model recognizes irrelevant content but cannot fully ignore it [^6].

| Instead of | Write |
|---|---|
| "Do not use markdown in your response" | "Write smoothly flowing prose paragraphs" |
| "NEVER use `any` in TypeScript" | "Use `unknown` and narrow with type guards" |
| "Don't embed raw Jira data" | "Persist API responses to `$WORKDIR/*.json`, read selectively" |

Every prohibition needs a replacement. A bare "don't do X" leaves the model guessing what to do instead [^10].

## Examples in Skills

For standard LLMs, examples are "pictures worth a thousand words" [^1]. But for agents and reasoning models, the guidance diverges:

- **Heuristics and principles outperform few-shot examples** for agent tasks — showing exact processes may limit the model's ability to leverage its full capabilities [^14]
- Performance follows a hill-shaped curve: it rises to a peak at 5-20 examples, then declines [^24]
- **4 diverse examples outperform 8 homogeneous ones** — cover edge cases, not variations of the happy path [^24]
- Place the most representative example **last** (leveraging the recency effect) [^24]

Use examples for output format specification (show the template once) and gotchas (show the subtle failure). Don't use examples to explain how to code.

## Context Hygiene at Runtime

- **Tool output bloat** is a major source of context waste. When tools return large responses, frontier models' error rates jump from 7% to 91% [^25]. Design skills so tool outputs are filtered or persisted to files rather than kept in conversation
- **~2% context retention loss per agent step** — after 5 cycles, under 60% of original context remains accessible [^26]. Keep multi-step workflows short, or checkpoint to files between phases
- **Error compounding** — at 85% per-step accuracy, a 10-step workflow succeeds only 20% of the time [^26]. Decompose tasks so each step is completable in 1-3 tool calls [^27]
- **Optimal context utilization is ~75%** — the remaining 25% serves as working memory for reasoning [^16]

## Skill Categories

Skills that try to do too much confuse the agent. Effective skills fit exactly one of these categories [^11]:

1. Library/API reference
2. Product verification
3. Data fetching
4. Business process automation
5. Code scaffolding
6. Code quality/review
7. CI/CD
8. Runbooks
9. Infrastructure operations

If a skill spans multiple categories, split it.

## This Repository's Patterns

**Observed from auditing 70 skills in this repo:**

| Range | Tendency |
|---|---|
| Under 170 lines | Well-focused, avoids anti-patterns |
| 200-350 lines | Mixed concerns creep in |
| Above 350 lines | Almost always embeds material that should be externalized |

**Top anti-patterns seen:**

1. **Inlined templates** — 100-250 lines of markdown/JSON templates embedded in the skill body. Move to `references/` or `assets/`
2. **Triple-stated instructions** — same rule in intro, step body, and "Notes" tail section. The tail is the most likely to be ignored after compaction
3. **Mixed concerns** — task logic, output formatting, and edge cases interleaved in a single step. Separate them into distinct sections

**Top good patterns:**

1. Orchestrator + sub-agent separation (e.g., `edge-scrum/release-health` → `release-planning-analysis`)
2. Tables for decision logic (argument mapping, auto-decision policies, error classification)
3. Concise skills that trust the model (e.g., `cluster-diagnostic` at 128 lines, `skills-review:lint` at 69 lines)

---

## Footnotes

[^1]: Anthropic, "Effective context engineering for AI agents" (2025). https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
[^2]: Anthropic, "Claude prompting best practices." https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
[^3]: Claude Code, "Skills documentation." https://code.claude.com/docs/en/skills
[^4]: Chroma "Context Rot" study (2025), summarized in https://brainbyteslab.org/articles/context-windows-are-a-lie/
[^5]: AGENTIF benchmark (2025). https://keg.cs.tsinghua.edu.cn/persons/xubin/papers/AgentIF.pdf
[^6]: "The Impact of Prompt Bloat on LLM Output Quality" (2024). https://home.mlops.community/public/blogs/the-impact-of-prompt-bloat-on-llm-output-quality
[^7]: Copilot Studio agent instruction analysis (2025). https://zenchong.substack.com/p/your-copilot-studio-agents-instructions
[^8]: Anthropic, "Skill authoring best practices." https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
[^9]: Internal audit of 70 skills in this repository (2026-07-31).
[^10]: Community testing, "Claude Code Best Practices 2026." https://thepromptshelf.dev/blog/claude-code-best-practices-2026/
[^11]: Anthropic, "Lessons from building Claude Code: how we use skills." https://claude.com/blog/lessons-from-building-claude-code-how-we-use-skills
[^12]: Anthropic, "Skill authoring best practices." https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
[^13]: Anthropic, "Steering Claude Code: skills, hooks, rules, subagents, and more." https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more
[^14]: Anthropic, "The Art of Agent Prompting." https://blog.agentailor.com/posts/the-art-of-agent-prompting
[^15]: Liu et al., "Lost in the Middle" (2023/TACL 2024). https://arxiv.org/abs/2307.03172
[^16]: Chroma "Context Rot" study (2025), summarized in https://reinteractive.com/articles/ai-real-world-use-cases/solving-ai-agent-amnesia-context-rot-and-lost-in-the-middle
[^17]: MorphLLM, "Lost in the Middle LLM: The U-Shaped Attention Problem." https://www.morphllm.com/lost-in-the-middle-llm
[^18]: Claude Code, "Best practices." https://code.claude.com/docs/en/best-practices
[^19]: Neural Buddies, "Marking Up the Prompt: How Markdown Formatting Influences LLM Responses." https://www.neuralbuddies.com/p/marking-up-the-prompt-how-markdown-formatting-influences-llm-responses
[^20]: Anthropic, "Claude prompting best practices." https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (re: Opus 4.5/4.6 overtriggering)
[^21]: "Why use Markdown in your Agents' System Prompt?" https://medium.com/@edprata/why-use-markdown-in-your-agents-system-prompt-41ad258a25c7
[^22]: OpenAI, "GPT-4.1 Prompting Guide." https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide
[^23]: Anthropic, "Claude Code sub-agents." https://code.claude.com/docs/en/sub-agents
[^24]: "The Few-shot Dilemma: Over-prompting Large Language Models" (2025). https://arxiv.org/html/2509.13196v1
[^25]: "Solving Context Window Overflow in AI Agents." https://arxiv.org/html/2511.22729
[^26]: "AI Agent Harness Failures: 13 Anti-Patterns." https://atlan.com/know/agent-harness-failures-anti-patterns/
[^27]: "AI Agents Prompting Guide." https://sureprompts.com/blog/ai-agents-prompting-guide
