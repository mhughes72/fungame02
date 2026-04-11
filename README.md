# Fungame - Gothic Text Adventure

A gothic text adventure game built with LangGraph and OpenAI.

## Check what branch
git branch

## Switch between branches
git checkout main
git checkout web-app

### Save to Main branch and switch to web-app
git add .
git commit -m "your change"
git checkout web-app

### And go back to main
git add .
git commit -m "your change"
git checkout main

git checkout web-app #web-app version


## Setup

1. Clone the repo
   git clone https://github.com/YOUR_USERNAME/fungame.git
   cd fungame

2. Create and activate virtual environment
   python -m venv .venv
   .venv\Scripts\activate

3. Install dependencies
   pip install -r requirements.txt

4. Create a .env file in the root folder with your API key
   OPENAI_API_KEY=your-openai-api-key-here

5. Run the game
   python main.py

## Branches

| Branch | Description |
|--------|-------------|
| main   | Terminal version of the game |
| web-app | Web browser version (in progress) |

## Branch Commands

### Switch to terminal version
git checkout main

### Switch to web app version
git checkout web-app

### Create a new branch
git checkout -b branch-name

### Merge a branch into main
git checkout main
git merge branch-name

## Saving Changes

git add .
git commit -m "describe what you changed"
git push origin main

## Game Commands

| Command | Description |
|---------|-------------|
| north / go north | Move in a direction |
| take [item] | Pick up an item |
| examine [item] | Examine something in the room |
| inventory | Check what you're carrying |
| talk [npc] | Talk to an NPC |
| attack [monster] | Attack a monster |
| room | Show current room state |
| look | Re-describe the current room |
| quit | Exit the game |