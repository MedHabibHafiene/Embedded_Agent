import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

# Ollama API key for OpenAI-compatible chat completions
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")

# Ollama base URL (local daemon by default; https://ollama.com/v1 for direct cloud API access)
OLLAMA_BASE_URL = os.getenv("BASE_URL", "http://localhost:11434/v1")

# Which model to ask Ollama for, e.g. "llama3.1" or a cloud model like "gemma4:cloud"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

# PlatformIO upload port (auto-detected if left empty)
# Set this manually if auto-detection fails, e.g. COM3 or /dev/ttyUSB0
PLATFORMIO_UPLOAD_PORT = os.getenv("PLATFORMIO_UPLOAD_PORT")

# Validate required configuration
if not OLLAMA_API_KEY:
    raise RuntimeError(
        "OLLAMA_API_KEY environment variable not set. "
        "Add it to the .env file."
    )