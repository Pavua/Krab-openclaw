set appPath to path to me as string
tell application "Finder"
	set parentFolder to container of alias appPath
	set parentPath to POSIX path of (parentFolder as alias)
end tell
set scriptPath to parentPath & "scripts/restart.sh"
do shell script "nohup " & quoted form of scriptPath & " > /dev/null 2>&1 &"
display notification "Команда перезапуска отправлена..." with title "Krab"
