
try
    do shell script "/Users/pablito/.gemini/antigravity/scratch/start_krab.sh > /dev/null 2>&1 &"
on error
    display dialog "Krab Setup Failed to Start" buttons {"OK"} default button 1
end try
