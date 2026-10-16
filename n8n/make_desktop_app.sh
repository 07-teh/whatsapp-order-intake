#!/bin/zsh
# 重新生成桌面上的"订单 n8n.app"：python3 不用，直接 zsh n8n/make_desktop_app.sh
cd "$(dirname "$0")/.."
APP="$HOME/Desktop/订单 n8n.app"
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script \"$PWD/n8n/desktop_launcher.sh > /dev/null 2>&1 &\""
cp n8n/n8n.icns "$APP/Contents/Resources/applet.icns"
/usr/libexec/PlistBuddy -c "Set :CFBundleName 订单 n8n" "$APP/Contents/Info.plist" 2>/dev/null
codesign --force --deep -s - "$APP" 2>/dev/null
touch "$APP"
echo "已生成 $APP"
