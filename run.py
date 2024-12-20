import platform
import random
import sys
from os import path
from pathlib import Path
from typing import List

from loguru import logger
from sc2 import maps
from sc2.data import AIBuild, Difficulty, Race
from sc2.main import run_game
from sc2.player import Bot, Computer

sys.path.append("ares-sc2/src/ares")
sys.path.append("ares-sc2/src")
sys.path.append("ares-sc2")
sys.path.append("queens-sc2")
sys.path.append("queens-sc2/queens_sc2")

import yaml

from bot.main import MyBot
from ladder import run_ladder_game

plt = platform.system()
# change if non default setup / linux
# if having issues with this, modify `map_list` below manually
if plt == "Windows":
    MAPS_PATH: str = "C:\\Program Files (x86)\\StarCraft II\\Maps"
elif plt == "Darwin":
    MAPS_PATH: str = "/Applications/StarCraft II/Maps"
elif plt == "Linux":
    # path would look a bit like this on linux after installing
    # SC2 via lutris
    MAPS_PATH: str = (
        "/home/tom/Games/battlenet/drive_c/Program Files (x86)/StarCraft II/maps"
    )
else:
    logger.error(f"{plt} not supported")
    sys.exit()

# change if non default setup / linux
# if having issues with this, modify `map_list` below manually
MAPS_PATH: str = "C:\\Program Files (x86)\\StarCraft II\\Maps"
CONFIG_FILE: str = "config.yml"
MAP_FILE_EXT: str = "SC2Map"
MY_BOT_NAME: str = "MyBotName"
MY_BOT_RACE: str = "MyBotRace"


def main():
    bot_name: str = "MyBot"
    race: Race = Race.Random

    __user_config_location__: str = path.abspath(".")
    user_config_path: str = path.join(__user_config_location__, CONFIG_FILE)
    # attempt to get race and bot name from config file if they exist
    if path.isfile(user_config_path):
        with open(user_config_path) as config_file:
            config: dict = yaml.safe_load(config_file)
            if MY_BOT_NAME in config:
                bot_name = config[MY_BOT_NAME]
            if MY_BOT_RACE in config:
                race = Race[config[MY_BOT_RACE].title()]

    bot1 = Bot(race, MyBot(), bot_name)

    if "--LadderServer" in sys.argv:
        # Ladder game started by LadderManager
        print("Starting ladder game...")
        result, opponentid = run_ladder_game(bot1)
        print(result, " against opponent ", opponentid)
    else:
        # Local game
        # map_list: List[str] = [
        #     p.name.replace(f".{MAP_FILE_EXT}", "")
        #     for p in Path(MAPS_PATH).glob(f"*.{MAP_FILE_EXT}")
        #     if p.is_file()
        # ]
        # alternative example code if finding the map path is problematic
        map_list: List[str] = [
            # "Equilibrium513AIE",
            # "Gresvan513AIE",
            "GoldenAura513AIE",
            # "HardLead513AIE",
            # "Oceanborn513AIE",
            # "SiteDelta513AIE"
        ]

        random_race = random.choice([Race.Zerg, Race.Terran, Race.Protoss])
        print("Starting local game...")
        run_game(
            maps.get(random.choice(map_list)),
            [
                bot1,
                Computer(random_race, Difficulty.CheatVision, ai_build=AIBuild.Macro),
            ],
            realtime=False,
        )


# Start game
if __name__ == "__main__":
    main()
