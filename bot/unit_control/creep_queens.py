from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

import numpy as np
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from ares.behaviors.combat.individual import (
    KeepUnitSafe,
    ShootTargetInRange,
    NydusPathUnitToTarget,
)
from ares.behaviors.combat.individual.queen_spread_creep import QueenSpreadCreep
from ares.behaviors.combat import CombatManeuver
from ares.managers.manager_mediator import ManagerMediator

from bot.unit_control.base_control import BaseControl

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class CreepQueens(BaseControl):
    """Execute behavior for queen creep spreading.

    Called from `QueenManager`

    Parameters
    ----------
    ai : AresBot
        Bot object that will be running the game
    config : Dict[Any, Any]
        Dictionary with the data from the configuration file
    mediator : ManagerMediator
        Used for getting information from managers in Ares.
    """

    ai: "AresBot"
    config: dict
    mediator: ManagerMediator

    def execute(self, units: Union[list[Unit], Units], **kwargs) -> None:
        """Execute the behavior."""
        avoid_grid: np.ndarray = self.mediator.get_ground_avoidance_grid
        ground_grid: np.ndarray = self.mediator.get_ground_grid
        target: Point2 = (
            self.mediator.get_defensive_third
            if self.ai.time < 165.0
            else self.ai.mediator.get_enemy_nat
        )
        for queen in units:
            maneuver: CombatManeuver = CombatManeuver()
            maneuver.add(KeepUnitSafe(queen, avoid_grid))
            maneuver.add(ShootTargetInRange(queen, self.ai.enemy_units))
            maneuver.add(KeepUnitSafe(queen, ground_grid))
            maneuver.add(QueenSpreadCreep(queen, queen.position, target))
            self.ai.register_behavior(maneuver)
