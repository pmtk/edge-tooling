---
name: best-practices
description: "Review a skill or agent file against LLM effectiveness best practices, or update the best-practices reference document with new research. Use when writing, reviewing, or improving skills and agents"
user-invocable: true
argument-hint: "review <path/to/SKILL.md> | update"
allowed-tools: Read, Bash, WebFetch, WebSearch, Agent, Edit, Write, AskUserQuestion
---

# best-practices

Review skills/agents against LLM effectiveness best practices, or refresh the reference document.

## Synopsis

```text
/best-practices review <path>    # Audit a skill or agent file
/best-practices update           # Refresh the reference document with new research
```

## Steps

### Mode: `review <path>`

1. **Read the reference document** at `docs/claude/skill-agent-best-practices.md`.
2. **Read the target file** at `<path>`.
3. **Audit against each section** of the reference document. For each finding, cite the specific best practice violated and the footnote source. Check:

   | Check | What to look for |
   |---|---|
   | Token cost | Lines the agent wouldn't get wrong without. Things the model already knows |
   | Redundancy | Same instruction stated more than once |
   | Position | Critical rules buried in the middle or in a "Notes" tail section |
   | Mixed concerns | Task logic, formatting, and edge cases interleaved in one step |
   | Inlined bloat | Templates, schemas, or reference data that should be in separate files |
   | Negations | "Don't do X" without a replacement "do Y instead" |
   | Emphasis overuse | More than 3 uses of IMPORTANT/CRITICAL/MUST |
   | Specificity mismatch | Overly prescriptive for flexible tasks, or vague for fragile operations |
   | Missing gotchas | No environment-specific corrections for common mistakes |
   | Missing stop conditions | No clear exit for error paths |
   | Category sprawl | Skill tries to cover multiple of the 9 skill categories |
   | Example count | More than 5 examples, or homogeneous examples |

4. **Present findings** as a table:

   ```text
   | # | Line(s) | Issue | Best Practice | Severity |
   |---|---------|-------|---------------|----------|
   ```

   Severity: `high` (likely ignored by the model), `medium` (wastes context), `low` (style improvement).

5. **Estimate token savings** — approximate how many tokens could be saved by addressing the findings.
6. **Offer to fix** — if the user wants, propose concrete edits. Do not edit without confirmation.

### Mode: `update`

1. **Read the current reference document** at `docs/claude/skill-agent-best-practices.md`.
2. **Extract the "Last updated" date** from the document header.
3. **Research new information** published since that date. Search for:
   - New Anthropic blog posts about skills, agents, prompt engineering, or context engineering
   - Updates to Claude Code documentation (skills, agents, best practices, sub-agents)
   - New research papers on instruction following, context window effects, or agent performance
   - Community findings about skill effectiveness
4. **Present findings** to the user: what's new, what changed, what should be added or updated.
5. **On confirmation**, edit the reference document:
   - Update the "Last updated" date
   - Add new findings with footnote sources
   - Update any outdated claims (e.g., if token limits changed)
   - Remove claims whose sources are no longer valid
   - Keep the document under 250 lines (excluding footnotes)

## Rules

- **Read-only by default** — never edit the target file or reference document without explicit user confirmation.
- **Cite sources** — every finding must reference a specific section or footnote from the reference document (review mode) or a URL (update mode).
- **No generic advice** — "this could be shorter" is not a finding. "Lines 45-62 restate the JQL query format that Claude already knows (ref: Brevity Imperative, fn 8)" is.

## Examples

**User:** `/best-practices review plugins/edge-scrum/skills/release-health/SKILL.md`
**Claude:** Reads the reference doc, reads the skill, produces a findings table with line numbers, cites best practices, estimates ~800 tokens of savings from removing redundant instructions and externalizing the config block.

**User:** `/best-practices update`
**Claude:** Reads the doc, notes it was last updated 2026-07-31, searches for new Anthropic posts and research since then, presents 3 new findings, asks for confirmation, then edits the document.
