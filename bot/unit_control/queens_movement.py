from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

import numpy as np
from cython_extensions import cy_center, cy_distance_to_squared
from sc2.ids.ability_id import AbilityId

from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import (
    KeepUnitSafe,
    ShootTargetInRange,
    UseTransfuse,
    UseAbility,
)
from ares.managers.manager_mediator import ManagerMediator
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

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

        point, exit_towards, nydus_tags = self.mediator.find_nydus_path_next_point(
            start=Point2(cy_center(units)),
            target=target,
            grid=ground_grid,
            sensitivity=10,
        )
        safe_nydus_exit: bool = True
        if nydus_tags and not self.ai.mediator.is_position_safe(
            grid=ground_grid,
            position=exit_towards,
            weight_safety_limit=exit_nydus_max_influence,
        ):
            safe_nydus_exit = False

        for queen in units:
            maneuver: CombatManeuver = CombatManeuver()
            maneuver.add(KeepUnitSafe(queen, avoid_grid))
            maneuver.add(UseTransfuse(queen, units))
            maneuver.add(ShootTargetInRange(queen, self.ai.enemy_units))
            maneuver.add(KeepUnitSafe(queen, ground_grid))

            maneuver.add(
                self._nydus_movement(
                    queen,
                    point,
                    exit_towards,
                    nydus_tags,
                    target,
                    safe_nydus_exit,
                )
            )

            self.ai.register_behavior(maneuver)

    def _nydus_movement(
        self,
        unit: Unit,
        point: Point2,
        exit_towards: Point2,
        nydus_tags: list[int],
        target: Point2,
        safe_nydus_exit: bool,
    ) -> CombatManeuver:
        maneuver: CombatManeuver = CombatManeuver()
        if (
            nydus_tags
            and safe_nydus_exit
            and unit.tag not in self.mediator.get_banned_nydus_travellers
        ):
            self.mediator.add_to_nydus_travellers(
                unit=unit,
                entry_nydus_tag=nydus_tags[0],
                exit_nydus_tag=nydus_tags[1],
                exit_towards=exit_towards,
            )
            if (
                cy_distance_to_squared(
                    Point2(point), self.ai.unit_tag_dict[nydus_tags[0]].position
                )
                < 36.0
            ):
                maneuver.add(
                    UseAbility(
                        AbilityId.SMART, unit, self.ai.unit_tag_dict[nydus_tags[0]]
                    )
                )
            else:
                maneuver.add(UseAbility(AbilityId.MOVE_MOVE, unit, point))

        else:
            if point:
                maneuver.add(UseAbility(AbilityId.MOVE_MOVE, unit, point))
            else:
                maneuver.add(UseAbility(AbilityId.MOVE_MOVE, unit, target))

        return maneuver
