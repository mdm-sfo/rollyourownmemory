import os

DEFAULT_LLM_MODEL = os.environ.get("MEMORY_LLM_MODEL", "nemotron-3-super")
OLLAMA_BASE_URL = os.environ.get("MEMORY_OLLAMA_URL", "http://localhost:11434")
LLM_TIMEOUT = int(os.environ.get("MEMORY_LLM_TIMEOUT", "600"))
