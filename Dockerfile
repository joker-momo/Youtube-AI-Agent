FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV NODE_VERSION=22
ENV HF_HOME=/app/caches/huggingface
ENV XDG_CACHE_HOME=/app/caches
ENV WHISPER_CACHE_DIR=/app/caches/whisper

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    espeak-ng \
    ffmpeg \
    fonts-dejavu-core \
    git \
    gnupg \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcairo2 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnss3 \
    libpango-1.0-0 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
  && curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml README.md ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
RUN pip install --no-cache-dir -e .

# Best-effort warmup for local TTS/ASR models so runtime is less likely to
# stall on first download. Build must remain resilient if network is limited.
RUN python -c "from pathlib import Path; Path('/app/caches/huggingface').mkdir(parents=True, exist_ok=True); Path('/app/caches/whisper').mkdir(parents=True, exist_ok=True)" \
  && python -c "import whisper; whisper.load_model('tiny'); print('Whisper tiny warmup: ok')" || true \
  && python -c "from kokoro import KPipeline; KPipeline(lang_code='e', repo_id='hexgrad/Kokoro-82M'); print('Kokoro warmup: ok')" || true

COPY remotion/package.json remotion/package.json
RUN npm --prefix remotion install \
  && npx --prefix remotion remotion browser ensure

COPY . .

CMD ["python", "-m", "video_agent.cli", "run", "--channel", "configs/vida-plena-45/channel.yaml", "--idea", "inputs/manual_idea.json"]
