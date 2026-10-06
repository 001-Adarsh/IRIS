FROM python:3.11-slim

# Install system utilities needed by web tools and git.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces runs the application as UID 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR $HOME/app

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user:user . .

EXPOSE 7860

CMD ["sh", "-c", "uvicorn api_server:app --host 0.0.0.0 --port ${PORT:-7860}"]
