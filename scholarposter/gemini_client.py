"""Re-export from gemini-acp shared package.

scholarposter imports summarize_via_gemini from here; the actual implementation
lives in the gemini-acp package (gemini_acp.client).
"""
from gemini_acp import summarize_via_gemini, ACP_AVAILABLE, GeminiUsage

__all__ = ["summarize_via_gemini", "ACP_AVAILABLE", "GeminiUsage"]
