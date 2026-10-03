"""Stand-in for ``java -jar forge.jar sim ...`` that prints Forge-format output."""
import os
import sys
import time

args = sys.argv[1:]
if "-version" in args:
    print('openjdk version "17.0.99" 2026-01-01', file=sys.stderr)
    sys.exit(0)

games = int(args[args.index("-n") + 1]) if "-n" in args else 1
delay = float(os.environ.get("FAKE_FORGE_DELAY", "0"))
print("14:20:35 [INFO ] GuiBase: APP: Forge v.9.9.9-TEST", flush=True)
print('An unsupported card was requested: "Bogus Card" from "LTR". ', flush=True)
print("Ai(1)-Deck_A vs Ai(2)-Deck_B - games of Constructed", flush=True)
for n in range(1, games + 1):
    time.sleep(delay)
    print("Warning: default (ie. inherited from base class) implementation of chooseSingleCard is used by "
          "Gollum's Bite for forge.ai.ability.AlwaysPlayAi. Consider declaring an overloaded method", flush=True)
    print(f"Game Outcome: Turn {10 + n}", flush=True)
    if n % 3 == 0:
        print(f"\nGame Result: Game {n} ended in a Draw! Took 1000 ms.", flush=True)
    else:
        winner = "Ai(1)-Deck_A" if n % 3 == 1 else "Ai(2)-Deck_B"
        print(f"\nGame Result: Game {n} ended in 1234 ms. {winner} has won!\n", flush=True)
