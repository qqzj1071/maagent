"""Agent layer: LLM-driven perception/action on top of the existing MAA workflows.

Keep this package init import-light: the chat launcher must not pull in
MaaFw/OCR just to talk. Import submodules explicitly where needed.
"""
