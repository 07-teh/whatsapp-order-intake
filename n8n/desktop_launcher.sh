#!/bin/zsh
# 桌面"订单 n8n"图标双击时跑：n8n 和总控台没开就在后台开，再用 Chrome 开总控台（像客户端）
PROJECT=$HOME/Desktop/whatsapp-order-intake
LOGDIR=$HOME/n8n-poultry
URL=http://localhost:5680
up() { /usr/sbin/lsof -iTCP:$1 -sTCP:LISTEN >/dev/null 2>&1 }
cd $PROJECT
up 5678 || nohup ./n8n/start_n8n.sh >> $LOGDIR/n8n.log 2>&1 &
up 5680 || nohup /opt/homebrew/bin/python3 console/server.py >> $LOGDIR/console.log 2>&1 &
for i in {1..90}; do
  up 5678 && up 5680 && break
  sleep 1
done
if ! up 5680; then
  osascript -e 'display alert "总控台没有启动成功" message "看 ~/n8n-poultry/console.log 最后几行"'
  exit 1
fi
if [ -d "/Applications/Google Chrome.app" ]; then
  open -na "Google Chrome" --args --app=$URL --window-size=1100,760
else
  open $URL
fi
