set appPath to path to me as string
tell application "Finder"
	set parentFolder to container of alias appPath
	set parentPath to POSIX path of (parentFolder as alias)
end tell

set scriptPath to parentPath & "scripts/use_cloud.sh"
do shell script "bash " & quoted form of scriptPath
display notification "Nexus переключен на Gemini Cloud" with title "Nexus Mode"
