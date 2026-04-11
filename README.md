# The Haunted Mansion - A Gothic Text Adventure

A dark, atmospheric text adventure game set in a haunted mansion. 
Explore rooms, collect items, uncover secrets, battle monsters, 
and converse with mysterious NPCs — all through natural language commands.

## Requirements

- Python 3.10+
- An OpenAI API key
- A Tavily API key (free tier available)

## Setup

1. Clone the repo
   git clone https://github.com/YOUR_USERNAME/fungame.git
   cd fungame

2. Create and activate virtual environment
   python -m venv .venv
   .venv\Scripts\activate        (Windows)
   source .venv/bin/activate     (Mac/Linux)

3. Install dependencies
   pip install -r requirements.txt

4. Create a .env file in the root folder
   OPENAI_API_KEY=your-openai-api-key-here
   TAVILY_API_KEY=your-tavily-api-key-here

5. Run the game
   python main.py

## Getting API Keys

**OpenAI:** https://platform.openai.com → API Keys
**Tavily:** https://tavily.com → Sign up free (1,000 searches/month free)

## Controls

The game understands natural language — you don't need exact commands.
Just type what you want to do and the game will figure it out.

### Movement
| What you type | What happens |
|---------------|--------------|
| north / go north / walk north / head north | Move in that direction |
| south / east / west / up / down | Move in that direction |

### Items
| What you type | What happens |
|---------------|--------------|
| take rusty key / grab the key / pick up key | Pick up an item |
| examine old book / look at the portrait / inspect fireplace | Examine something |
| open chest / open the box / pry open lockbox | Open a container |
| inventory / what am I carrying / what do I have | Check your inventory and gold |

### Combat
| What you type | What happens |
|---------------|--------------|
| attack ghost / fight the rat / kill the vampire | Attack a monster |

### NPCs
| What you type | What happens |
|---------------|--------------|
| talk to aldric / speak with the oracle / chat with aldric | Start a conversation |
| goodbye / bye / farewell / done | End a conversation |

### Information
| What you type | What happens |
|---------------|--------------|
| room / where am i / current room | Show full room state including hidden items and containers |
| look / look around / examine room | Re-describe the current room |
| quit / exit | Exit the game |

## Tips

- Some items hide secrets — examine everything
- Containers may hold gold — open them to collect it
- Some NPCs have special knowledge — talk to them
- The Oracle can answer questions about the real world
- Your gold total is shown when you check your inventory

## Notes

- Each playthrough starts fresh
- Monsters defeated stay defeated for that session
- Gold collected carries over between rooms