"""Prompt templates for paper extraction."""

DEFAULT_SYSTEM_PROMPT = (
    "You are an information extraction assistant. "
    "Extract structured data from the provided markdown document. "
    "Return ONLY valid JSON that conforms to the given JSON Schema. "
    "The field 'paper_authors' MUST be an array of strings (each author name as one item). "
    "If a field is unknown, use an empty string or empty list per schema." 
)

DEFAULT_USER_PROMPT = """Document content:\n{content}\n\nJSON Schema:\n{schema}\n"""
