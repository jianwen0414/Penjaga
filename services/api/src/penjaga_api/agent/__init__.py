"""The Penjaga agent (PRD §18): an LLM with tools over the Core API's facts, behind guardrails
enforced in code. The templated cases (step 3) stay the fallback whenever the LLM is off,
unavailable, not consented to, or fails a guardrail."""
