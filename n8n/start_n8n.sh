#!/bin/zsh
# 在 Claude 桌面版的终端里跑：n8n 读 WhatsApp 数据库要借用这个 app 已经拿到的 macOS 权限。
# 打开 http://localhost:5678 看画面。只听本机（127.0.0.1），以后做内网穿透再改。
export PATH=/opt/homebrew/opt/node@24/bin:/opt/homebrew/bin:$PATH
export N8N_USER_FOLDER=$HOME/n8n-poultry          # 和 ~/.n8n 里 2 月那套分开
export N8N_LISTEN_ADDRESS=127.0.0.1
export N8N_PORT=5678
export GENERIC_TIMEZONE=Asia/Kuala_Lumpur
export TZ=Asia/Kuala_Lumpur
export NODES_EXCLUDE='[]'                           # 2.x 默认关掉 Execute Command，这里打开
export N8N_RESTRICT_FILE_ACCESS_TO=$HOME/Desktop/whatsapp-order-intake/output
export N8N_DIAGNOSTICS_ENABLED=false
export N8N_PERSONALIZATION_ENABLED=false
export N8N_VERSION_NOTIFICATIONS_ENABLED=false
exec $HOME/n8n-app/node_modules/.bin/n8n start
