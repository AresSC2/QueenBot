from typing import List, Set
from sc2.ids.unit_typeid import UnitTypeId as UnitID

BUILD_ORDER: List[UnitID] = [
    UnitID.OVERLORD,
    UnitID.DRONE,
    UnitID.DRONE,
    UnitID.DRONE,
    UnitID.HATCHERY,
    UnitID.DRONE,
    UnitID.DRONE,
    UnitID.DRONE,
    UnitID.SPAWNINGPOOL,
    UnitID.DRONE,
    UnitID.DRONE,
    UnitID.DRONE,
    UnitID.OVERLORD,

]

ATTACK_TARGET_IGNORE: Set[UnitID] = {
    UnitID.SCV,
    UnitID.DRONE,
    UnitID.PROBE,
    UnitID.MULE,
    UnitID.LARVA,
    UnitID.EGG,
    UnitID.CHANGELING,
    UnitID.CHANGELINGMARINE,
    UnitID.CHANGELINGMARINESHIELD,
    UnitID.CHANGELINGZEALOT,
    UnitID.CHANGELINGZERGLING,
    UnitID.CHANGELINGZERGLINGWINGS,
    UnitID.REAPER,
}
PROXY_STATIC_DEFENCE: Set[UnitID] = {
    UnitID.BUNKER,
    UnitID.SPINECRAWLER,
    UnitID.PYLON,
    UnitID.COMMANDCENTER,
    UnitID.PHOTONCANNON,
}
