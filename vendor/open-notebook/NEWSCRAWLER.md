# NewsCrawler Notebook AI — vendored from https://github.com/lfnovo/open-notebook
#
# Free LLM defaults (compose service notebook-bootstrap):
#   Chat: Groq llama-3.1-8b-instant (+ llama-3.3-70b-versatile)
#   Tools / transform: Ollama Qwen qwen2.5:3b
#   Embedding: Ollama nomic-embed-text
#
# Default runtime uses the published Docker image (see docker-compose.yml).
# Rebuild patched image:
#   docker compose -f docker-compose.yml -f deploy/notebook/compose.build.yml build notebook-ai
