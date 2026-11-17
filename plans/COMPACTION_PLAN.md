# Conversation compaction — backlog

Deferred to the backlog at the owner’s request. No summarization or compaction is implemented. Persisted history and model context are distinct: the full transcript remains stored, while each request contains only a bounded recent window plus the active instruction and any newly retrieved sources.

## Recommendation

First use the explicit per-chat history window (default 20 messages / 10 exchanges) and inspect actual included counts and context size. For long-running conversations, a later optional compaction mode can retain a short derived memory of older turns while keeping recent exchanges verbatim. It is useful when important preferences, decisions or definitions would otherwise fall outside the recent window. It is not necessary for short chats and cannot guarantee perfect recall.

## Proposed context layout

1. Current trusted system instruction.
2. A clearly labelled derived summary of older conversation content, treated as untrusted conversational data rather than new instructions.
3. The most recent complete user/assistant exchanges, verbatim.
4. The new user question and the freshly retrieved RAG/web evidence, within a reserved source budget.

The exact message layout should match the grounding contract; summary text must not masquerade as newly retrieved evidence or gain system-level authority. The current implementation places fresh sources in the system prompt; a compaction design must explicitly keep their boundaries separate from summarized conversation data.

## Suggested future mechanics

- Opt in per chat through an admin policy; keep the original transcript immutable. Begin with an input-budget trigger rather than summarizing every message. A starting proposal is compaction when history would overflow its allocated budget, retaining the newest 4–6 exchanges verbatim and allowing roughly 1,500–2,000 characters for the summary. These are tuning proposals; they must fit the configured overall context and source reserves.
- Summarize facts supplied by the user, goals, preferences, decisions, unresolved questions and important definitions. Distinguish user assertions from assistant suggestions; preserve uncertainty and corrections. Preserve exact important identifiers only when justified and verify them against source turns.
- Store the summary separately with the covered message IDs/range, summarizer model/configuration, creation time and version. Never delete original messages as a side effect of compaction. Permit rebuilding or disabling derived memory.
- Prefer rebuilding from the original covered messages within a bounded summarization workload. Repeatedly summarizing a previous summary can compound omissions or errors. If a layered/incremental design is eventually needed, retain links back to raw turns and validate corrections rather than treating the prior summary as unquestioned truth.
- Budget and cache summarization calls explicitly. Reuse the unchanged summary for subsequent turns; no extra summary call per normal request. Include summary version/content in answer cache identity. Handle failures by using the normal bounded recent window with a visible status, not by dropping all recent history.
- Scope every summary to the owning chat and preserve existing role privacy. Admins may configure compaction without receiving private message or summary content. Invalidate/rebuild after relevant message edits/deletions or a reset; deleting a chat deletes its derived memory.

## Tradeoffs and limits

Compaction can improve continuity at a lower input size, but adds generation latency/cost, can omit a detail, and can propagate an incorrect summary. A summary is not an archive or a reliable substitute for exact quotations. Exact-history search or pinning selected facts can complement summaries later, but are separate features.

Historical RAG/web citations remain traceable in the transcript. A summary must not present their old text as current verified evidence: re-retrieve the cited source when needed, honour deleted/unavailable documents, and preserve web freshness rules. Summarizing a claim cannot make it true.

Retrieval follow-ups are a separate issue: current retrieval uses only the newest question. A phrase like “explain that more” can lack enough search terms despite a correct history window. A later contextual query builder can resolve references from recent turns for RAG; sending rewritten questions or conversation-derived details to web search needs an explicit policy because the current web privacy boundary sends only the current question. Compaction alone does not fix retrieval selection.

## Acceptance before implementation

Use scripted conversations to check preserved decisions, corrected facts, conflicting statements, exact identifiers, private data boundaries, deletions, failed summary calls and source freshness. Compare latency/reservations with an uncompacted recent window. Require summary traceability and prove summaries do not become privileged instructions. Keep this functional memory testing separate from the deferred broad answer-quality review.
