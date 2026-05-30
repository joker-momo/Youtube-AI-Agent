#!/usr/bin/env bash
set -euo pipefail

VNC_HOME="${HOME:-/home/pwuser}/.vnc"
VNC_PASSWD_FILE="${KASMVNC_PASSWORD_FILE:-${HOME:-/home/pwuser}/.kasmpasswd}"
VNC_USERNAME="${KASMVNC_USERNAME:-operator}"
VNC_PASSWORD="${KASMVNC_PASSWORD:-}"
DISABLE_BASIC_AUTH="${KASMVNC_DISABLE_BASIC_AUTH:-1}"
VNC_CERT_FILE="${VNC_HOME}/self.pem"
VNC_KEY_FILE="${VNC_HOME}/self.key"

mkdir -p "${VNC_HOME}"

# Remove stale X locks/sockets left by an unclean previous shutdown. KasmVNC
# refuses to bind :1 when these exist ("A VNC server is already running as :1"),
# which leaves the container with no X server and crash-loops Chromium. These
# accumulate across container stop/start cycles, so clear them on every boot.
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 "${VNC_HOME}"/*.pid 2>/dev/null || true

if [[ -z "${VNC_PASSWORD}" ]]; then
  VNC_PASSWORD="$(dd if=/dev/urandom bs=32 count=1 2>/dev/null | base64 | tr -dc 'A-Za-z0-9' | cut -c1-20)"
  echo "KasmVNC generated password for ${VNC_USERNAME}: ${VNC_PASSWORD}"
  echo "Set KASMVNC_PASSWORD to keep a stable password across restarts."
fi

printf '%s\n%s\n' "${VNC_PASSWORD}" "${VNC_PASSWORD}" \
  | /usr/bin/kasmvncpasswd -u "${VNC_USERNAME}" -wo "${VNC_PASSWD_FILE}" >/dev/null

if [[ ! -s "${VNC_CERT_FILE}" || ! -s "${VNC_KEY_FILE}" ]]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -subj "/CN=localhost" \
    -keyout "${VNC_KEY_FILE}" \
    -out "${VNC_CERT_FILE}" >/dev/null 2>&1
  chmod 600 "${VNC_KEY_FILE}" "${VNC_CERT_FILE}"
fi

cat > "${VNC_HOME}/kasmvnc.yaml" <<EOF
network:
  protocol: http
  interface: 0.0.0.0
  websocket_port: 7900
  ssl:
    pem_certificate: ${VNC_CERT_FILE}
    pem_key: ${VNC_KEY_FILE}
    require_ssl: false
server:
  advanced:
    kasm_password_file: ${VNC_PASSWD_FILE}
command_line:
  prompt: false
desktop:
  allow_resize: false
  resolution:
    width: 1920
    height: 1080
runtime_configuration:
  allow_client_to_override_kasm_server_settings: false
EOF

cmd=(/usr/bin/vncserver :1 -fg -select-de manual -xstartup "${VNC_HOME}/xstartup" -SecurityTypes None -KasmPasswordFile "${VNC_PASSWD_FILE}" -interface 0.0.0.0 -websocketPort 7900 -httpd /usr/share/kasmvnc/www -geometry 1920x1080 -depth 24)

if [[ "${DISABLE_BASIC_AUTH}" == "1" ]]; then
  cmd+=(-disableBasicAuth)
fi

exec "${cmd[@]}"
