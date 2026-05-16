"""Quick diagnostic: navigate to Library and try to take fire poker."""
import asyncio, json, time, subprocess, sys, os

HOST, PORT = "localhost", 8765
WS_URL = f"ws://{HOST}:{PORT}/ws"

async def diag():
    import websockets
    ws = await websockets.connect(WS_URL)

    in_combat = False

    async def drain(label):
        nonlocal in_combat
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                msg = json.loads(raw)
                t = msg.get("type","?")
                if t not in ("token",):
                    text = str(msg.get("text",""))[:120]
                    print(f"  [{label}] ← {t:20s} {text}")
                if t == "encounter_start":
                    in_combat = True
                elif t == "encounter_end":
                    in_combat = False
                elif t == "prompt":
                    if in_combat:
                        print(f"  [{label}] AUTO-ATTACK")
                        await ws.send("attack")
                    else:
                        break
                elif t in ("game_over", "game_won"):
                    break
            except asyncio.TimeoutError:
                print(f"  [{label}] TIMEOUT")
                break

    async def send(cmd):
        print(f"\n>>> {cmd}")
        await ws.send(cmd)
        return await drain(cmd[:20])

    await drain("intro")

    cmds = [
        "take health potion",
        "talk to aldric",
        "examine old book",  # get secret letter
        "give him the secret letter",
        "i speak the scholar's oath",
        "goodbye",
        "go north", "go west",        # Library
        "take fire poker", "equip fire poker",
        "fight the shadow",
        "go north",                   # Secret Room
        "take magic staff", "equip magic staff",
        "fight the wraith",
        "take strange amulet",
        "go south", "go east", "go south",  # back to Study
        "use health potion",
        "fight the ghost",             # ghost drops unmaking_flame
    ]
    for cmd in cmds:
        await send(cmd)

    await ws.close()

env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "server:fastapi_app",
     "--host", HOST, "--port", str(PORT), "--log-level", "error"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env
)
time.sleep(12)
print("Server ready, running diag...\n")
try:
    asyncio.run(diag())
finally:
    proc.terminate()
    proc.wait()
    print("\nDone.")
