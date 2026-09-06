# RAG V2 Acceptance Cases

`cases-v1.jsonl` is the canonical Stage 10C-1 live retrieval acceptance case set. It contains exactly 12 cases: cases 1–10 are required, and cases 11–12 are optional.

`retrieval-check` and `post-activation-smoke` consume this file. Required-case failures fail the retrieval acceptance gate; optional-case failures are recorded but do not fail the required-case gate.

The query literals were approved by the Stage 10C-1 human gate after the original Task 3 artifact was found missing. Do not casually edit IDs, order, or query expectations because this file is an acceptance artifact.
