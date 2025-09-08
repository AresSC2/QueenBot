from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

import numpy as np
from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import (
    KeepUnitSafe,
    ShootTargetInRange,
    StutterUnitBack,
    StutterUnitForward,
    UseAbility,
    UseTransfuse,
)
from ares.consts import ALL_STRUCTURES, VICTORY_MARGINAL_OR_BETTER, UnitTreeQueryType
from ares.managers.manager_mediator import ManagerMediator
from cython_extensions import cy_closest_to
from cython_extensions.geometry import cy_distance_to_squared
from cython_extensions.units_utils import cy_center
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from bot.consts import COMMON_UNIT_IGNORE_TYPES
from bot.unit_control.base_control import BaseControl

if TYPE_CHECKING:
    from ares import AresBot

STATIC_DEFENCE: set[UnitID] = {
    UnitID.BUNKER,
    UnitID.PLANETARYFORTRESS,
    UnitID.SPINECRAWLER,
    UnitID.PHOTONCANNON,
}



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
        exit_nydus_max_influence = kwargs.get("exit_nydus_max_influence", 10.0)
        spread_creep: bool = kwargs.get("spread_creep", True)

        ground_grid: np.ndarray = self.mediator.get_ground_grid
        avoid_grid: np.ndarray = self.mediator.get_ground_avoidance_grid
        all_close_enemy: Units = self.mediator.get_units_in_range(
            start_points=[Point2(cy_center(units))],
            distances=[13.5],
            query_tree=UnitTreeQueryType.AllEnemy,
            return_as_dict=False,
        )[0].filter(lambda u: u.type_id not in COMMON_UNIT_IGNORE_TYPES)
        only_enemy_units: list[Unit] = [
            u for u in all_close_enemy if u.type_id not in ALL_STRUCTURES
        ]
        ground, flying = self.ai.split_ground_fliers(
            only_enemy_units, return_as_lists=True
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
                    enemy_units=only_enemy_units,
                )
                in VICTORY_MARGINAL_OR_BETTER
            )
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

        placed_tumor: bool = False
        for queen in units:

            queen_pos: Point2 = queen.position
            maneuver: CombatManeuver = CombatManeuver()

            if (
                not placed_tumor
                and spread_creep
                and self.mediator.is_position_safe(grid=ground_grid, position=queen_pos)
                and self.ai.has_creep(queen_pos)
                and (
                    len(tumors) == 0
                    or not [
                        t
                        for t in tumors
                        if cy_distance_to_squared(t.position, queen_pos) < 144.0
                    ]
                )
                and not self.mediator.get_position_blocks_expansion(position=queen_pos)
            ):
                placed_tumor = True
                maneuver.add(
                    UseAbility(AbilityId.BUILD_CREEPTUMOR_QUEEN, queen, queen_pos)
                )
            maneuver.add(KeepUnitSafe(queen, avoid_grid))
            maneuver.add(UseTransfuse(queen, units))
            maneuver.add(ShootTargetInRange(queen, flying))
            maneuver.add(ShootTargetInRange(queen, ground))
            if not only_enemy_units or len([e for e in all_close_enemy if e.type_id in STATIC_DEFENCE]) > 0:
                maneuver.add(ShootTargetInRange(queen, all_close_enemy))
            if all_close_enemy:
                if can_engage and can_fight:
                    if only_enemy_units:
                        closest_enemy: Unit = cy_closest_to(queen_pos, only_enemy_units)
                    else:
                        closest_enemy: Unit = cy_closest_to(queen_pos, all_close_enemy)
                    if self.ai.has_creep(queen_pos) or (
                        closest_enemy.can_attack_ground
                        and closest_enemy.ground_range < 4
                    ):
                        maneuver.add(StutterUnitBack(queen, closest_enemy))
                    else:
                        maneuver.add(StutterUnitForward(queen, closest_enemy))
                else:
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
            else:
                maneuver.add(KeepUnitSafe(queen, ground_grid))
                if cy_distance_to_squared(queen_pos, target) > 36.0:
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
