from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

import numpy as np
from cython_extensions import cy_closest_to
from cython_extensions.geometry import cy_distance_to_squared
from cython_extensions.units_utils import cy_center
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from ares.consts import UnitTreeQueryType, ALL_STRUCTURES, VICTORY_MARGINAL_OR_BETTER

from ares.behaviors.combat.individual import (
    KeepUnitSafe,
    ShootTargetInRange,
    UseTransfuse,
    StutterUnitBack,
    StutterUnitForward,
    NydusPathUnitToTarget,
    QueenSpreadCreep,
)
from ares.behaviors.combat import CombatManeuver
from ares.managers.manager_mediator import ManagerMediator
from bot.consts import COMMON_UNIT_IGNORE_TYPES

from bot.unit_control.base_control import BaseControl

if TYPE_CHECKING:
    from ares import AresBot


@dataclass
class CombatQueens(BaseControl):
    """Execute behavior for queen combat.

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
        can_engage: bool = kwargs.get("can_engage", True)
        target: Point2 = kwargs.get("target", self.ai.enemy_start_locations[0])
        check_close_combat_result = kwargs.get("check_close_combat_result", False)

        ground_grid: np.ndarray = self.mediator.get_ground_grid
        avoid_grid: np.ndarray = self.mediator.get_ground_avoidance_grid
        all_close_enemy: dict[int, Units] = self.mediator.get_units_in_range(
            start_points=units,
            distances=13.5,
            query_tree=UnitTreeQueryType.AllEnemy,
            return_as_dict=True,
        )
        tumors: list[Unit] = self.mediator.get_own_structures_dict[
            UnitID.CREEPTUMORQUEEN
        ]

        # optional extra check
        can_fight: bool = True
        if check_close_combat_result:
            can_fight = (
                self.mediator.can_win_fight(
                    own_units=units,
                    enemy_units=[
                        u
                        for u in self.ai.enemy_units
                        if u.type_id not in COMMON_UNIT_IGNORE_TYPES
                        and cy_distance_to_squared(u.position, cy_center(units)) < 225.0
                    ],
                )
                in VICTORY_MARGINAL_OR_BETTER
            )
        for queen in units:
            near_enemy: list[Unit] = [
                u
                for u in all_close_enemy[queen.tag]
                if u.type_id not in COMMON_UNIT_IGNORE_TYPES
            ]

            only_enemy_units: list[Unit] = [
                u for u in near_enemy if u.type_id not in ALL_STRUCTURES
            ]
            ground, flying = self.ai.split_ground_fliers(
                only_enemy_units, return_as_lists=True
            )
            queen_pos: Point2 = queen.position
            maneuver: CombatManeuver = CombatManeuver()

            if self.ai.has_creep(queen_pos) and (
                len(tumors) == 0
                or not [
                    t
                    for t in tumors
                    if cy_distance_to_squared(t.position, queen_pos) < 144.0
                ]
            ):
                maneuver.add(QueenSpreadCreep(queen, queen_pos, target))
            maneuver.add(KeepUnitSafe(queen, avoid_grid))
            maneuver.add(UseTransfuse(queen, units))
            maneuver.add(ShootTargetInRange(queen, flying))
            maneuver.add(ShootTargetInRange(queen, ground))
            maneuver.add(ShootTargetInRange(queen, near_enemy))
            if near_enemy:
                if can_engage and can_fight:
                    tanks: list[Unit] = [
                        u
                        for u in only_enemy_units
                        if u.type_id in {UnitID.SIEGETANKSIEGED}
                    ]
                    if only_enemy_units:
                        closest_enemy: Unit = cy_closest_to(queen_pos, only_enemy_units)
                    else:
                        closest_enemy: Unit = cy_closest_to(queen_pos, near_enemy)
                    if not tanks and (
                        self.ai.has_creep(queen_pos)
                        or (
                            closest_enemy.can_attack_ground
                            and closest_enemy.ground_range < 4
                        )
                    ):
                        maneuver.add(StutterUnitBack(queen, closest_enemy))
                    else:
                        maneuver.add(StutterUnitForward(queen, closest_enemy))
                else:
                    maneuver.add(KeepUnitSafe(queen, ground_grid))
                    maneuver.add(
                        NydusPathUnitToTarget(queen, ground_grid, target=target)
                    )
            else:
                maneuver.add(KeepUnitSafe(queen, ground_grid))
                if cy_distance_to_squared(queen_pos, target) > 36.0:
                    maneuver.add(
                        NydusPathUnitToTarget(queen, ground_grid, target=target)
                    )

            self.ai.register_behavior(maneuver)
