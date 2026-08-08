"""A deliberately breakable grounded-RAG toy target.

This exists so the gates can be demonstrated failing. It answers
questions about a fictional city from a small bilingual corpus, refuses
harmful requests, escalates crisis content, and resists prompt injection,
until a named defect is injected, at which point exactly the failure a
gate is meant to catch appears. It must never be deployed as a real
assistant.
"""

from gauntlet.toy.target import DEFECT_NAMES, GATE_DEFECTS, Defects, ToyRag

__all__ = ["DEFECT_NAMES", "GATE_DEFECTS", "Defects", "ToyRag"]
