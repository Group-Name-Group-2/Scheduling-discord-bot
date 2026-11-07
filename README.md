# Scheduling-discord-bot
A discord bot which allows for the coordination of users and finding games to play

### Scrum Roles:
* Scrum Master - Gokul Nair
* Product Owner - Brandon Potter
* Developer - Akhil Bollepally, Tristan Gebeaux, and Zea Hanson

### Instructions

```
install ngrok
install discord
npm install
npm run register
npm run start
install discord bot to server
ngrok http 3000
save forwarding url
on app page on general info go to interactive endpoint url and paste forwarding url and append /interactions
```

#run
pip install uvicorn
winget install --id astral-sh.uv -e
restart shell
uv venv
#allow scripts
.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
uvicorn main:app --reload
go to 127.0.0.1:8000 in browser