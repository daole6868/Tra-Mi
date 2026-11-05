#!/usr/bin/env bash
# Cài trạm trung chuyển Subvideo lên VPS (Ubuntu 22.04 / 24.04, Debian 12) – điện thoại ở ngoài mạng nhà điều khiển
# tool trên máy tính qua đây. Trạm không lưu gì (không mật khẩu, không video); đăng nhập do tool trên máy tính kiểm.
#
#   Có tên miền (đã trỏ bản ghi A về IP của VPS):   sudo bash cai_vps.sh sub.tenmien.com
#   Không có tên miền (dùng IP của VPS):             sudo bash cai_vps.sh
#   Gỡ bỏ:                                           sudo bash cai_vps.sh --go
#
# Chạy lại = cập nhật, GIỮ NGUYÊN mã kết nối cũ. Xong sẽ in 1 dòng "subvideo-vps://…" -> chép dòng đó vào tool:
# Cài đặt → Điều khiển từ xa → ô "Chuỗi kết nối VPS" → Lưu.
set -euo pipefail

DIR=/opt/subvideo-relay
SVC=subvideo-relay
IP_PORT="${SUBVIDEO_PORT:-8443}"
LOCAL_PORT=8770

say() { printf '\n\033[1;33m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "Chạy bằng quyền root: sudo bash $0 ${*:-}"

if [ "${1:-}" = "--go" ]; then
  systemctl disable --now "$SVC" 2>/dev/null || true
  rm -f "/etc/systemd/system/$SVC.service" /etc/caddy/subvideo.caddy
  [ -f /etc/caddy/Caddyfile ] && sed -i '/import \/etc\/caddy\/subvideo.caddy/d' /etc/caddy/Caddyfile && \
    (systemctl reload caddy 2>/dev/null || true)
  rm -rf "$DIR"
  systemctl daemon-reload
  echo "Đã gỡ trạm Subvideo."
  exit 0
fi

DOMAIN="${1:-}"
DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%%/*}"

say "Cài gói cần thiết (python3, aiohttp, openssl)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-aiohttp openssl curl ca-certificates >/dev/null
python3 -c "import aiohttp" || die "Không cài được python3-aiohttp"

say "Chép chương trình trạm"
mkdir -p "$DIR"
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$HERE/relay.py" ]; then
  cp "$HERE/relay.py" "$DIR/relay.py"
else   # bản 1 file (tool tạo bằng nút "Lưu file cài VPS…"): chương trình trạm nằm ở cuối file này
  sed -n '/^#__RELAY_BEGIN__$/,/^#__RELAY_END__$/p' "$0" | sed '1d;$d' | sed 's/^#//' | base64 -d > "$DIR/relay.py" || true
fi
[ -s "$DIR/relay.py" ] || die "Thiếu relay.py – đặt relay.py cùng thư mục với file này (hoặc dùng file cài 1 file do tool tạo)"
id subvideo >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin subvideo

SECRET=""
if [ -f "$DIR/config.json" ]; then
  SECRET="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['secret'])" "$DIR/config.json" 2>/dev/null || true)"
fi
[ -n "$SECRET" ] || SECRET="$(openssl rand -hex 24)"

PUBIP="$(curl -4s --max-time 6 https://api.ipify.org || true)"
[ -n "$PUBIP" ] || PUBIP="$(hostname -I | awk '{print $1}')"

port_busy() {   # cổng đang bị chương trình KHÁC caddy giữ
  ss -ltnp 2>/dev/null | awk -v p=":$1" '$4 ~ p"$" {print}' | grep -qv caddy
}

MODE=ip
if [ -n "$DOMAIN" ]; then
  DNSIP="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}' || true)"
  if [ -z "$DNSIP" ]; then
    echo "⚠ Tên miền $DOMAIN chưa trỏ về đâu – dùng IP $PUBIP thay (trỏ bản ghi A xong thì chạy lại file này)."
    DOMAIN=""
  elif [ -n "$PUBIP" ] && [ "$DNSIP" != "$PUBIP" ]; then
    echo "⚠ $DOMAIN đang trỏ về $DNSIP, không phải IP VPS này ($PUBIP) – nếu qua Cloudflare hãy tắt đám mây cam (DNS only)."
  fi
fi
if [ -n "$DOMAIN" ]; then
  if port_busy 80 || port_busy 443; then
    echo "⚠ Cổng 80/443 đang có web server khác (nginx / apache…) – không cài Caddy; trạm chạy cổng $IP_PORT với"
    echo "  chứng chỉ tự ký, app ghim vân tay (vẫn an toàn)."
    MODE=selfsigned
  else
    MODE=caddy
  fi
fi

write_cert() {
  if [ ! -s "$DIR/cert.pem" ] || [ ! -s "$DIR/key.pem" ]; then
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -sha256 -days 3650 -nodes \
      -subj "/CN=subvideo-relay" -keyout "$DIR/key.pem" -out "$DIR/cert.pem" >/dev/null 2>&1
  fi
  FP="$(openssl x509 -in "$DIR/cert.pem" -outform der | sha256sum | cut -d' ' -f1)"
}

FP=""
if [ "$MODE" = caddy ]; then
  say "Cài Caddy (chứng chỉ HTTPS thật cho $DOMAIN, tự gia hạn)"
  if ! command -v caddy >/dev/null; then
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
      gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy >/dev/null
  fi
  cat > /etc/caddy/subvideo.caddy <<EOF
$DOMAIN {
	reverse_proxy 127.0.0.1:$LOCAL_PORT {
		flush_interval -1
	}
}
EOF
  touch /etc/caddy/Caddyfile
  # Caddyfile mặc định (chỉ có trang chào :80) -> thay; đã có trang web khác -> chỉ thêm 1 dòng import
  if grep -qE '^\s*:80\s*\{' /etc/caddy/Caddyfile && ! grep -qE '^[a-z0-9.-]+\.[a-z]{2,}' /etc/caddy/Caddyfile; then
    echo "import /etc/caddy/subvideo.caddy" > /etc/caddy/Caddyfile
  elif ! grep -q 'import /etc/caddy/subvideo.caddy' /etc/caddy/Caddyfile; then
    echo "import /etc/caddy/subvideo.caddy" >> /etc/caddy/Caddyfile
  fi
  cat > "$DIR/config.json" <<EOF
{"secret": "$SECRET", "listen": "127.0.0.1", "port": $LOCAL_PORT, "trust_proxy": true}
EOF
  HOSTPART="$DOMAIN"
else
  say "Tạo chứng chỉ tự ký (app ghim vân tay)"
  write_cert
  cat > "$DIR/config.json" <<EOF
{"secret": "$SECRET", "listen": "0.0.0.0", "port": $IP_PORT, "cert": "cert.pem", "key": "key.pem", "trust_proxy": false}
EOF
  HOSTPART="${DOMAIN:-$PUBIP}:$IP_PORT"
fi
chown -R subvideo:subvideo "$DIR"
chmod 700 "$DIR"
chmod 600 "$DIR/config.json"
[ -f "$DIR/key.pem" ] && chmod 600 "$DIR/key.pem"

say "Tạo dịch vụ chạy nền (tự chạy lại khi VPS khởi động)"
cat > "/etc/systemd/system/$SVC.service" <<EOF
[Unit]
Description=Subvideo relay (điều khiển tool từ xa)
After=network-online.target
Wants=network-online.target

[Service]
User=subvideo
Group=subvideo
ExecStart=/usr/bin/python3 $DIR/relay.py --config $DIR/config.json
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadOnlyPaths=$DIR
MemoryMax=300M

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SVC" >/dev/null 2>&1
systemctl restart "$SVC"
[ "$MODE" = caddy ] && { systemctl enable caddy >/dev/null 2>&1 || true; systemctl reload caddy 2>/dev/null || systemctl restart caddy; }

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  if [ "$MODE" = caddy ]; then ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  else ufw allow "$IP_PORT/tcp" >/dev/null; fi
fi

sleep 2
systemctl is-active --quiet "$SVC" || { journalctl -u "$SVC" -n 30 --no-pager; die "Trạm không chạy được (xem lỗi phía trên)"; }
if [ "$MODE" = caddy ]; then
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://$DOMAIN/api/ping" || true)"
else
  CODE="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 "https://127.0.0.1:$IP_PORT/api/ping" || true)"
fi
LINK="subvideo-vps://$HOSTPART?k=$SECRET"
[ -n "$FP" ] && LINK="$LINK&fp=$FP"

echo
echo "======================================================================"
echo " ✓ Trạm Subvideo đã chạy ($([ "$MODE" = caddy ] && echo "https://$DOMAIN" || echo "https://$HOSTPART, chứng chỉ tự ký"))"
[ "$CODE" = 503 ] || echo " (kiểm tra thử trả mã '$CODE' – bình thường là 503 'máy tính chưa kết nối')"
echo
echo " Chép NGUYÊN dòng dưới vào tool: Cài đặt → Điều khiển từ xa → Chuỗi kết nối VPS → Lưu"
echo
echo "   $LINK"
echo
echo " Giữ bí mật dòng này (ai có nó thì giả được máy tính của bạn – không vào được tool)."
if [ "$MODE" != caddy ]; then
  echo " Nhà cung cấp VPS có tường lửa riêng (Security group / Firewall) thì mở cổng TCP $IP_PORT."
else
  echo " Nhà cung cấp VPS có tường lửa riêng thì mở cổng TCP 80 và 443."
fi
echo "======================================================================"
exit 0
#__RELAY_BEGIN__
#__RELAY_END__
