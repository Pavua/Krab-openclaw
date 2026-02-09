set appPath to path to me as string
tell application "Finder"
	set parentFolder to container of alias appPath
	set parentPath to POSIX path of (parentFolder as alias)
end tell

set scriptPath to parentPath & "scripts/stop.sh"
do shell script "bash " & quoted form of scriptPath
