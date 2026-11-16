#!/usr/bin/env bash
set -e

echo "=== 开始部署 RikkaHub 伴侣服务到 Debian ==="

# 1. 安装系统依赖
apt-get update
apt-get install -y python3 python3-venv python3-pip nginx curl

# 2. 准备 Python 虚拟环境与依赖
cd /opt/rikkahub/companion/server
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 3. 确保前端静态资源目录权限 (允许 Nginx 访问)
chmod -R 755 /opt/rikkahub/companion/web/dist 2>/dev/null || true

# 4. 部署 Systemd 服务
cp /opt/rikkahub/companion/deploy/companion.service /etc/systemd/system/companion.service
systemctl daemon-reload
systemctl enable companion
systemctl restart companion

# 5. 部署 Nginx 配置
cp /opt/rikkahub/companion/deploy/nginx.conf /etc/nginx/sites-available/rikkahub-companion
ln -sf /etc/nginx/sites-available/rikkahub-companion /etc/nginx/sites-enabled/rikkahub-companion
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

echo "=== 部署完成！伴侣服务已在后台常驻运行 ==="
echo "查看服务状态: systemctl status companion"
echo "查看服务日志: journalctl -u companion -f"
