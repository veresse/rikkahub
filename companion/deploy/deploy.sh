#!/usr/bin/env bash
set -e

echo "=== 开始部署 RikkaHub Companion Server（v2 服务端） ==="

# 1. 安装系统依赖
apt-get update
apt-get install -y python3 python3-venv python3-pip nginx curl

# 2. 创建专用非 root 运行用户
if ! id -u rikkahub >/dev/null 2>&1; then
    useradd --system --home /opt/rikkahub --shell /usr/sbin/nologin rikkahub
fi

# 3. 准备 Python 虚拟环境与依赖
cd /opt/rikkahub/companion/server
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 4. 数据目录与环境变量文件
install -d -m 750 -o rikkahub -g rikkahub /opt/rikkahub/companion/server/data
install -d -m 750 /etc/rikkahub
if [ ! -f /etc/rikkahub/companion.env ]; then
    cp /opt/rikkahub/companion/deploy/companion.env.example /etc/rikkahub/companion.env
    chmod 600 /etc/rikkahub/companion.env
    echo "已生成 /etc/rikkahub/companion.env，请填写 ADMIN_PASSWORD 与模型密钥后重新执行本脚本。"
    exit 1
fi

# 5. 迁移数据库（启动时也会自动执行，这里显式执行便于提前发现错误）
if [ -f /opt/rikkahub/companion/server/data/companion.db ]; then
    cp /opt/rikkahub/companion/server/data/companion.db \
       "/opt/rikkahub/companion/server/data/companion.db.bak.$(date +%Y%m%d%H%M%S)"
fi
PYTHONPATH=/opt/rikkahub .venv/bin/alembic -c alembic.ini upgrade head

# 6. 部署 Systemd 服务
cp /opt/rikkahub/companion/deploy/companion.service /etc/systemd/system/companion.service
systemctl daemon-reload
systemctl enable companion
systemctl restart companion

# 7. 部署 Nginx 配置
cp /opt/rikkahub/companion/deploy/nginx.conf /etc/nginx/sites-available/rikkahub-companion
ln -sf /etc/nginx/sites-available/rikkahub-companion /etc/nginx/sites-enabled/rikkahub-companion
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

echo "=== 部署完成！ ==="
echo "查看服务状态: systemctl status companion"
echo "查看服务日志: journalctl -u companion -f"
echo "回滚提示: 恢复 data/companion.db.bak.* 后，将代码切回旧版本并重启服务"
