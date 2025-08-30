from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

import numpy as np

from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from ares.behaviors.combat.individual import (
    KeepUnitSafe,
    ShootTargetInRange,
    UseTransfuse,
    NydusPathUnitToTarget,
)
from ares.behaviors.combat import CombatManeuver
from ares.managers.manager_mediator import ManagerMediator

from bot.unit_control.base_control import BaseControl

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class QueensMovement(BaseControl):
    """Reusable behavior for moving queens.

    Called from other classes in `bot/unit_control`

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
        target: Point2 = kwargs.get("target", self.mediator.get_own_nat)
        exit_nydus_max_influence: float = kwargs.get("exit_nydus_max_influence", 10.0)
        ground_grid: np.ndarray = self.mediator.get_ground_grid
        avoid_grid: np.ndarray = self.mediator.get_ground_avoidance_grid
        for queen in units:
            maneuver: CombatManeuver = CombatManeuver()
            maneuver.add(KeepUnitSafe(queen, avoid_grid))
            maneuver.add(UseTransfuse(queen, units))
            maneuver.add(ShootTargetInRange(queen, self.ai.enemy_units))
            maneuver.add(KeepUnitSafe(queen, ground_grid))

            maneuver.add(
                NydusPathUnitToTarget(
                    queen,
                    ground_grid,
                    target=target,
                    exit_nydus_max_influence=exit_nydus_max_influence,
                )
            )

            self.ai.register_behavior(maneuver)
