#!/usr/bin/env bash
# ============================================================
# REBA - Setup one-time del VPS Oracle para el tunel reverso
# Ejecutar en el VPS:  bash vps_setup.sh
# Requiere sudo. Abre el puerto del tunel en el firewall LOCAL
# (la Security List de Oracle se configura aparte en la consola).
# ============================================================
set -euo pipefail

PORT=39173
SSH_DIR=/etc/ssh/sshd_config.d

echo "== [1/3] sshd: permitir bind publico en forwards remotos =="
sudo mkdir -p "$SSH_DIR"
echo "GatewayPorts clientspecified" | sudo tee "$SSH_DIR/99-reba-gatewayports.conf" >/dev/null
if sudo sshd -t 2>/dev/null; then
  sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd
  echo "    sshd recargado OK"
else
  echo "    AVISO: sshd -t fallo; revisar manualmente"
fi

echo "== [2/3] firewall local: abrir TCP $PORT =="
if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
  sudo ufw allow "$PORT/tcp" comment "REBA tunnel" || true
elif command -v firewall-cmd >/dev/null 2>&1; then
  sudo firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null
  sudo firewall-cmd --reload
else
  # OCI Ubuntu: iptables con reglas en /etc/iptables/rules.v4
  if ! sudo iptables -C INPUT -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 5 -p tcp --dport "$PORT" -j ACCEPT
  fi
  sudo mkdir -p /etc/iptables
  sudo iptables-save | sudo tee /etc/iptables/rules.v4 >/dev/null
  sudo ip6tables-save | sudo tee /etc/iptables/rules.v6 >/dev/null 2>&1 || true
fi

echo "== [3/3] verificacion =="
echo "    sshd efectivo: $(sudo sshd -T 2>/dev/null | grep -i gatewayports || echo 'sin sshd -T')"
echo "    LISTO. Recuerda abrir TCP $PORT en la Security List de Oracle (consola web)."
