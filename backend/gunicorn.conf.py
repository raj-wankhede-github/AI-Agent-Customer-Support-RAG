"""Gunicorn settings for production (Uvicorn workers)."""

import multiprocessing
import os

bind = os.getenv("BIND", "0.0.0.0:8000")
workers = int(os.getenv("WEB_CONCURRENCY", str(min(4, multiprocessing.cpu_count() * 2 + 1))))
worker_class = "uvicorn_worker.UvicornWorker"
# A chat turn may wait on an LLM provider; keep this above CHAT_TIMEOUT_SECONDS.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "180"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = 5
max_requests = 2000
max_requests_jitter = 200
accesslog = None  # the application emits structured access logs
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
