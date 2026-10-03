# Generates docs/assets/demo.gif — run from the repo root:
#   vhs docs/demo/demo.tape

Output docs/assets/demo.gif

Require curl
Require bat

Set Shell bash
Set FontFamily "JetBrainsMono Nerd Font"
Set FontSize 18
Set Width 1200
Set Height 720
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set WindowBar Colorful

Hide
Type "cd docs/demo && export PS1='\[\e[1;35m\]❯\[\e[0m\] '" Enter
Type `export CURL_HOME=$(mktemp -d) && echo 'write-out = "\\n"' > $CURL_HOME/.curlrc` Enter
Type "../../.venv/bin/python -m uvicorn app:app --no-server-header --no-date-header --log-level warning >/dev/null 2>&1 &" Enter
Sleep 2s
Type "clear" Enter
Show

Type "# A payment endpoint, protected by one middleware line" Sleep 400ms Enter
Type "bat -p app.py" Sleep 300ms Enter
Sleep 4.5s

Hide
Type "clear" Enter
Show

Type "# 1st request: the customer is charged" Sleep 400ms Enter
Type `curl -i localhost:8000/payments -H "Idempotency-Key: order-42" --json '{"amount": 100}'` Sleep 300ms Enter
Sleep 2.5s

Type "# Network dropped! The client retries with the same key..." Sleep 400ms Enter
Type `curl -i localhost:8000/payments -H "Idempotency-Key: order-42" --json '{"amount": 100}'` Sleep 300ms Enter
Sleep 1.5s
Type "# ✅ Same txn replayed, no double charge" Sleep 3.5s

Hide
Ctrl+U
Type "clear" Enter
Show

Type "# Same key, different payload? Rejected." Sleep 400ms Enter
Type `curl -i localhost:8000/payments -H "Idempotency-Key: order-42" --json '{"amount": 999}'` Sleep 300ms Enter
Sleep 4s

Hide
Type "kill %1" Enter
