from typing import TYPE_CHECKING, Optional, Set

import numpy as np
from ares.behaviors.combat import CombatManeuver
from ares.behaviors.combat.individual import KeepUnitSafe, PathUnitToTarget
from ares.consts import UnitRole
from cython_extensions import cy_closest_to, cy_distance_to, cy_towards
from cython_extensions.general_utils import cy_unit_pending
from cython_extensions.units_utils import cy_find_units_center_mass
from sc2.data import Race
from sc2.game_info import Ramp
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from bot.managers.queen_bot_mediator import QueenBotMediator

if TYPE_CHECKING:
    from ares import AresBot

MELEE_TYPES: set[UnitID] = {UnitID.DRONE, UnitID.PROBE, UnitID.SCV, UnitID.ZERGLING}


class ScoutManager:
    queen_bot_mediator: QueenBotMediator
    initial_ol_spot: Point2

    def __init__(
        self,
        ai: "AresBot",
    ):
        self.ai: AresBot = ai

        self.overseer_tag: int = 0
        self.worker_scout_tag: int = 0
        self.issued_scout_command: bool = False

        self.nydus_overseer_tag: int = 0
        self.creep_queen_dropperlord_tags: Set[int] = set()
        self.cancelled_structures: bool = False
        self._sack_drone_scout: bool = False
        self._first_iteration: bool = True

    def update(self) -> None:
        if self._first_iteration:
            self.initial_ol_spot = self._calculate_first_ol_spot()
            self._first_iteration = False
        overlords: Units = self.ai.mediator.get_own_army_dict[UnitID.OVERLORD]

        # self._morph_dropperlord(overlords)
        _scout_time: float = 43.0 if self.ai.race != Race.Terran else 24.0
        if self.ai.enemy_race != Race.Zerg and self.ai.time > _scout_time:
            self._handle_worker_scout()
        self._manager_overseer(overlords)
        self._handle_nydus_overseer(overlords)

        if self.ai.time > 190.0 and not self._sack_drone_scout:
            if worker := self.ai.mediator.select_worker(
                target_position=self.ai.mediator.get_own_nat
            ):
                self.ai.mediator.assign_role(tag=worker.tag, role=UnitRole.SCOUTING)
                self._sack_drone_scout = True
                ramp: Ramp = self.ai.mediator.get_enemy_ramp
                move_to: Point2 = Point2(
                    cy_towards(ramp.top_center, ramp.bottom_center, 2)
                )
                worker.move(move_to)

        if (
            self.ai.build_order_runner.build_completed
            and self.ai.time > 120.0
            and (
                overlords := self.ai.mediator.get_units_from_role(
                    role=UnitRole.BUILD_RUNNER_SCOUT, unit_type=UnitID.OVERLORD
                )
            )
        ):
            for ol in overlords:
                maneuver: CombatManeuver = CombatManeuver()
                maneuver.add(KeepUnitSafe(ol, self.ai.mediator.get_air_grid))
                maneuver.add(
                    PathUnitToTarget(
                        ol, self.ai.mediator.get_air_grid, self.initial_ol_spot
                    )
                )
                self.ai.register_behavior(maneuver)

    def assign_drone_back_to_gathering(self, drone_tag: int) -> None:
        self.ai.mediator.assign_role(tag=drone_tag, role=UnitRole.GATHERING)

    def _handle_worker_scout(self) -> None:
        if self.worker_scout_tag == 0:
            if worker := self.ai.mediator.select_worker(
                target_position=self.ai.start_location
            ):
                self.worker_scout_tag = worker.tag
                self.ai.mediator.assign_role(tag=worker.tag, role=UnitRole.SCOUTING)
        else:
            scout_location: Point2 = self.ai.mediator.get_own_nat.towards(
                self.ai.game_info.map_center, 35
            )
            if scout := self.ai.unit_tag_dict.get(self.worker_scout_tag, None):
                enemy_structures: Units = self.ai.enemy_structures.filter(
                    lambda s: s.type_id
                    in {UnitID.BARRACKS, UnitID.FACTORY, UnitID.BUNKER, UnitID.PYLON}
                    and cy_distance_to(scout.position, s.position) < 40
                )
                enemy_workers: Units = self.ai.enemy_units(UnitID.SCV)
                if not self.issued_scout_command:
                    scout.move(scout_location)
                    for _ in range(2):
                        for el in self.ai.mediator.get_own_expansions[1:4]:
                            scout.move(el[0], queue=True)
                    self.issued_scout_command = True
                elif enemy_structures:
                    target: Unit = cy_closest_to(scout.position, enemy_structures)
                    if enemy_workers and enemy_workers.closer_than(25, target.position):
                        scout.attack(cy_closest_to(scout.position, enemy_workers))
                    else:
                        scout.attack(target)
                elif scout.is_idle and self.ai.mineral_field:
                    scout.gather(
                        cy_closest_to(self.ai.start_location, self.ai.mineral_field)
                    )
                    self.assign_drone_back_to_gathering(scout.tag)

    def _handle_nydus_overseer(self, overlords: Units) -> None:
        nydus_overseers: list[Unit] = [
            u
            for u in self.ai.mediator.get_own_army_dict[UnitID.OVERSEER]
            if u.tag == self.nydus_overseer_tag
        ]

        if not nydus_overseers:
            new_tag: Optional[int] = self._morph_overseer(overlords)
            if new_tag and new_tag != self.overseer_tag:
                self.nydus_overseer_tag = new_tag
                self.ai.mediator.assign_role(tag=new_tag, role=UnitRole.NYDUS_SPOTTER)

        else:
            air_grid: np.ndarray = self.ai.mediator.get_air_grid
            avoid_grid: np.ndarray = self.ai.mediator.get_air_avoidance_grid
            for overseer in nydus_overseers:
                spotter_maneuver: CombatManeuver = CombatManeuver()
                spotter_maneuver.add(KeepUnitSafe(overseer, avoid_grid))
                spotter_maneuver.add(KeepUnitSafe(overseer, air_grid))
                spotter_maneuver.add(
                    PathUnitToTarget(
                        overseer,
                        air_grid,
                        self.queen_bot_mediator.get_current_canal_target,
                    )
                )
                self.ai.register_behavior(spotter_maneuver)

    def _manager_overseer(self, overlords: Units) -> None:
        overseers: Units = self.ai.mediator.get_units_from_role(
            role=UnitRole.ATTACKING_MAIN_SQUAD, unit_type=UnitID.OVERSEER
        )

        if not overseers:
            new_tag: Optional[int] = self._morph_overseer(overlords)
            if new_tag and new_tag != self.nydus_overseer_tag:
                self.overseer_tag = new_tag
                self.ai.mediator.assign_role(
                    tag=new_tag, role=UnitRole.ATTACKING_MAIN_SQUAD
                )

        else:
            force: Units = self.ai.mediator.get_units_from_roles(
                roles={UnitRole.QUEEN_DEFENCE, UnitRole.QUEEN_OFFENSIVE}
            )
            if force:
                center, _ = cy_find_units_center_mass(force, 12.5)
                position: Point2 = Point2(center)
                grid: np.ndarray = self.ai.mediator.get_air_grid
                for overseer in overseers:
                    overseer_maneuver: CombatManeuver = CombatManeuver()
                    overseer_maneuver.add(KeepUnitSafe(overseer, grid))
                    overseer_maneuver.add(PathUnitToTarget(overseer, grid, position))
                    self.ai.register_behavior(overseer_maneuver)

    def _morph_dropperlord(self, overlords: Units) -> None:
        # morph a dropperlord so queens-sc2 can make use of the creep queen dropperlord
        dropperlords: Units = self.ai.units.tags_in(self.creep_queen_dropperlord_tags)
        if not dropperlords:
            if (
                overlords
                and self.ai.minerals >= 25
                and self.ai.vespene >= 25
                and (
                    self.ai.structures(UnitID.LAIR).ready
                    or self.ai.structures(UnitID.HIVE)
                )
            ):
                overlord: Unit = overlords.filter(
                    lambda u: u.tag != self.overseer_tag
                ).closest_to(self.ai.start_location)
                if overlord:
                    overlord(AbilityId.MORPH_OVERLORDTRANSPORT)
                    self.creep_queen_dropperlord_tags.add(overlord.tag)

    def _morph_overseer(self, overlords: Units) -> Optional[int]:
        """Returns the tag of the new overseer"""
        own_structures_dict: dict[
            UnitID, Units
        ] = self.ai.mediator.get_own_structures_dict
        if (
            overlords
            and own_structures_dict[UnitID.LAIR]
            and self.ai.can_afford(UnitID.OVERSEER)
            and (
                [s for s in own_structures_dict[UnitID.LAIR] if s.is_ready]
                or own_structures_dict[UnitID.HIVE]
            )
            and not cy_unit_pending(self.ai, UnitID.OVERSEER)
        ):
            if overlords := [
                ol
                for ol in overlords
                if ol.tag not in self.creep_queen_dropperlord_tags
            ]:
                overlord: Unit = cy_closest_to(self.ai.start_location, overlords)
                overlord(AbilityId.MORPH_OVERSEER, subtract_cost=True)
                return overlord.tag

    def _calculate_first_ol_spot(self):
        if self.ai.enemy_race == Race.Zerg:
            if path := self.ai.mediator.find_raw_path(
                start=self.ai.mediator.get_enemy_nat,
                target=self.ai.game_info.map_center,
                grid=self.ai.mediator.get_ground_grid,
                sensitivity=8,
            ):
                if len(path) >= 3:
                    return path[2]

        return self.ai.mediator.get_closest_overlord_spot(
            from_pos=Point2(
                cy_towards(
                    self.ai.mediator.get_enemy_nat,
                    self.ai.game_info.map_center,
                    10.0,
                )
            )
        )
