import os

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

SERVICES_TABLE = os.getenv("SERVICES_TABLE", "purezen_services")
AVAILABILITY_TABLE = os.getenv("AVAILABILITY_TABLE", "purezen_availability")
BOOKINGS_TABLE = os.getenv("BOOKINGS_TABLE", "purezen_bookings")
