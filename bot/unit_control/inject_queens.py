from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

import numpy as np
from cython_extensions import cy_closest_to
from sc2.ids.ability_id import AbilityId
from sc2.unit import Unit
from sc2.units import Units

from ares.behaviors.combat.individual import (
    KeepUnitSafe,
    ShootTargetInRange,
    UseAbility,
    PathUnitToTarget,
)
from ares.behaviors.combat.individual.queen_spread_creep import QueenSpreadCreep
from ares.behaviors.combat import CombatManeuver
from ares.managers.manager_mediator import ManagerMediator

from bot.unit_control.base_control import BaseControl

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class InjectQueens(BaseControl):
    """Execute behavior for queen injecting.

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
        inject_q_to_th_tags: dict[int, int] = kwargs.get("inject_q_to_th_tags", {})
        ground_grid: np.ndarray = self.mediator.get_ground_grid
        avoid_grid: np.ndarray = self.mediator.get_ground_avoidance_grid
        for queen in units:
            target_th_tag: int = inject_q_to_th_tags.get(queen.tag, None)
            maneuver: CombatManeuver = CombatManeuver()
            maneuver.add(KeepUnitSafe(queen, avoid_grid))
            maneuver.add(ShootTargetInRange(queen, self.ai.enemy_units))
            maneuver.add(KeepUnitSafe(queen, ground_grid))
            if target_th_tag and target_th_tag in self.ai.unit_tag_dict:
                target_th: Unit = self.ai.unit_tag_dict[target_th_tag]
                maneuver.add(
                    UseAbility(
                        AbilityId.EFFECT_INJECTLARVA,
                        queen,
                        target_th,
                    )
                )
                maneuver.add(
                    PathUnitToTarget(
                        queen, ground_grid, target_th.position, success_at_distance=4
                    )
                )

            self.ai.register_behavior(maneuver)
