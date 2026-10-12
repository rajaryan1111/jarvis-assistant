"""Bounded agent loop (M2.0): the model proposes, the tool registry decides and executes.

Modules: ``provider`` (model boundary + OpenAI adapter), ``schema`` (argument validation),
``catalog`` (which tools the model may propose), ``runtime`` (the loop, limits, verification).
See docs/M2_0_AGENT_LOOP.md.
"""
