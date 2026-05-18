FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV NODE_VERSION=22

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
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
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt \
  && pip install --no-cache-dir -e .

COPY remotion/package.json remotion/package.json
RUN npm --prefix remotion install

COPY . .

CMD ["python", "-m", "video_agent.cli", "run", "--channel", "configs/vida-plena-45/channel.yaml", "--idea", "inputs/manual_idea.json"]
