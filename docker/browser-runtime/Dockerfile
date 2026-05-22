FROM mcr.microsoft.com/playwright:v1.49.0-jammy

USER root

ENV DEBIAN_FRONTEND=noninteractive

ARG KASMVNC_VERSION=1.3.4

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    dbus-x11 \
    fluxbox \
    socat \
    supervisor \
    xterm \
  && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
  arch="$(dpkg --print-architecture)"; \
  case "$arch" in \
    amd64) kasm_arch="amd64" ;; \
    arm64) kasm_arch="arm64" ;; \
    *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
  esac; \
  curl -fL -o /tmp/kasmvncserver.deb "https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/kasmvncserver_jammy_${KASMVNC_VERSION}_${kasm_arch}.deb"; \
  apt-get update; \
  apt-get install -y --no-install-recommends /tmp/kasmvncserver.deb; \
  rm -f /tmp/kasmvncserver.deb; \
  rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data/profile /var/log/supervisor \
  && chown -R pwuser:pwuser /data

COPY docker/browser-runtime/supervisord.conf /etc/supervisord.conf
COPY docker/browser-runtime/xstartup /home/pwuser/.vnc/xstartup
COPY docker/browser-runtime/start-kasmvnc.sh /usr/local/bin/start-kasmvnc.sh
COPY docker/browser-runtime/launch-chromium.sh /usr/local/bin/launch-chromium.sh
RUN chmod +x /usr/local/bin/launch-chromium.sh /usr/local/bin/start-kasmvnc.sh /home/pwuser/.vnc/xstartup \
  && chown -R pwuser:pwuser /home/pwuser/.vnc

EXPOSE 7900 7901 9222

CMD ["supervisord", "-c", "/etc/supervisord.conf", "-n"]
