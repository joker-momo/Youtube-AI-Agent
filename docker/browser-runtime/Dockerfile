FROM mcr.microsoft.com/playwright:v1.49.0-jammy

USER root

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    fluxbox \
    novnc \
    socat \
    supervisor \
    websockify \
    x11vnc \
    xvfb \
  && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data/profile /var/log/supervisor \
  && chown -R pwuser:pwuser /data

COPY docker/browser-runtime/supervisord.conf /etc/supervisord.conf
COPY docker/browser-runtime/launch-chromium.sh /usr/local/bin/launch-chromium.sh
RUN chmod +x /usr/local/bin/launch-chromium.sh

EXPOSE 7900 9222

CMD ["supervisord", "-c", "/etc/supervisord.conf", "-n"]
