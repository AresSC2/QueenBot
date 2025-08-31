from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

import numpy as np
from sc2.ids.ability_id import AbilityId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from ares.behaviors.combat.individual import (
    KeepUnitSafe,
    ShootTargetInRange,
    UseAbility,
)
from ares.behaviors.combat.individual.queen_spread_creep import QueenSpreadCreep
from ares.behaviors.combat import CombatManeuver
from ares.managers.manager_mediator import ManagerMediator

from bot.unit_control.base_control import BaseControl

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class OverlordCreepSpotters(BaseControl):
    """Execute behavior for overlords spotting creep edges.

    Called from `bot/main.py`

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
        spotter_positions: dict[
            int, Point2
        ] = self.mediator.get_overlord_creep_spotter_positions(
            overlords=units, target_pos=self.ai.enemy_start_locations[0]
        )

        grid: np.ndarray = self.mediator.get_air_grid
        for ol in units:
            creep_spotter_maneuver: CombatManeuver = CombatManeuver()
            creep_spotter_maneuver.add(KeepUnitSafe(ol, grid))
            creep_spotter_maneuver.add(
                UseAbility(AbilityId.BEHAVIOR_GENERATECREEPON, ol)
            )
            if ol.tag in spotter_positions:
                creep_spotter_maneuver.add(
                    UseAbility(AbilityId.MOVE_MOVE, ol, spotter_positions[ol.tag])
                )
            self.ai.register_behavior(creep_spotter_maneuver)
