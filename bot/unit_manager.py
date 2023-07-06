from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from scipy import spatial

from MapAnalyzer import MapData
from bot.consts import ATTACK_TARGET_IGNORE, PROXY_STATIC_DEFENCE
from bot.custom_bot_ai import CustomBotAI
from bot.manager import Manager
from bot.terrain_manager import TerrainManager
from bot.worker_manager import WorkerManager
from queens_sc2.consts import QueenRoles
from queens_sc2.queens import Queens
from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units


class UnitManager(Manager):
    queens: Queens

    def __init__(
        self,
        bot: CustomBotAI,
        worker_manager: WorkerManager,
        terrain_manager: TerrainManager,
    ):
        super().__init__(bot)
        self.map_data: MapData = MapData(bot)
        self.worker_manager: WorkerManager = worker_manager
        self.terrain_manager: TerrainManager = terrain_manager

        self.bunker_drone_tags: Set[int] = set()
        self.worker_defence_tags: List[int] = []
        self.enemy_committed_worker_rush: bool = False
        self.overseer_tag: int = 0
        self.worker_scout_tag: int = 0
        self.issued_scout_command: bool = False
        self.switched_queen_policy: bool = False

        self.nydus_overseer_tag: int = 0
        self.creep_queen_dropperlord_tags: Set[int] = set()
        self.cancelled_structures: bool = False
        self.ground_grid: Optional[np.ndarray] = None
        self.sent_second_overlord: bool = False

        self.offensive: bool = False

    @property
    def early_game_queen_policy(self) -> Dict:
        return {
            "creep_queens": {
                "active": True,
                "distance_between_queen_tumors": 3,
                "first_tumor_position": self.terrain_manager.natural_location.towards(
                    self.bot.game_info.map_center, 9
                ),
                "priority": True,
                "prioritize_creep": lambda: True,
                "max": 2,
                "defend_against_ground": True,
                "rally_point": self.terrain_manager.natural_location,
                "priority_defence_list": {
                    UnitID.ZERGLING,
                    UnitID.MARINE,
                    UnitID.ZEALOT,
                },
            },
            "creep_dropperlord_queens": {
                "active": True,
                "priority": True,
                "max": 1,
                "pass_own_threats": True,
                "target_expansions": [
                    el[0] for el in self.terrain_manager.expansions[-6:-3]
                ],
            },
            "defence_queens": {
                "attack_condition": lambda: self.offensive,
                "rally_point": self.terrain_manager.natural_location,
            },
            "inject_queens": {"active": False},
            "nydus_queens": {
                "active": True,
                "max": 12,
                "steal_from": {QueenRoles.Defence},
            },
        }

    @property
    def mid_game_queen_policy(self) -> Dict:
        return {
            "creep_queens": {
                "max": 2,
                "priority": True,
                "defend_against_ground": True,
                "distance_between_queen_tumors": 3,
                "priority_defence_list": {
                    UnitID.BATTLECRUISER,
                    UnitID.LIBERATOR,
                    UnitID.LIBERATORAG,
                    UnitID.VOIDRAY,
                },
            },
            "creep_dropperlord_queens": {
                "active": True,
                "priority": True,
                "max": 1,
                "pass_own_threats": True,
                "priority_defence_list": set(),
                "target_expansions": [el for el in self.bot.expansion_locations_list],
            },
            "defence_queens": {
                "attack_condition": lambda: self.offensive,
                "rally_point": self.terrain_manager.natural_location,
            },
            "inject_queens": {"active": False},
            "nydus_queens": {
                "active": True,
                "max": 12,
                "steal_from": {QueenRoles.Defence},
            },
        }

    def _on_first_iteration(self) -> None:
        # initiating queens-sc2` here
        self.queens = Queens(
            self.bot,
            debug=False,
            queen_policy=self.early_game_queen_policy,
            map_data=self.map_data,
        )
        self.bot.units(UnitID.OVERLORD).first.move(
            self.terrain_manager.natural_location
        )

    def _check_should_be_offensive(self) -> None:
        queens: Units = self.bot.units(UnitID.QUEEN)
        num_queens: int = queens.amount
        if num_queens < 6:
            self.offensive = False
            return
        if (
            (
                sum([unit.energy for unit in queens]) / num_queens >= 75
                and num_queens > 40
            )
            or self.bot.enemy_units.filter(
                lambda u: u.type_id == UnitID.WIDOWMINEBURROWED
                and u.distance_to(self.bot.enemy_start_locations[0]) > 50
                and not self.queens.defence.enemy_air_threats
                and not self.queens.defence.enemy_ground_threats
            )
            or self.bot.structures(UnitID.NYDUSCANAL)
        ):
            self.offensive = True
            return

        self.offensive = False

    async def update(self, iteration) -> None:
        if iteration == 0:
            self._on_first_iteration()

        if iteration % 4 == 0:
            self._check_should_be_offensive()

        # call the queen library to handle our queens
        if hasattr(self, "queens"):
            # get a new ground grid at certain invervals since it updates pathable areas
            if self.ground_grid is None or iteration % 32 == 0:
                self.ground_grid = self.map_data.get_pyastar_grid()
            await self.queens.manage_queens(
                iteration,
                creep_queen_dropperlord_tags=self.creep_queen_dropperlord_tags,
                grid=self.ground_grid,
            )
            self._manage_queen_policy(iteration)

        if iteration % 32 == 0:
            self._adjust_attack_target()

        overlords: Units = self.bot.units(UnitID.OVERLORD)

        await self._morph_dropperlord(overlords)
        if self.bot.enemy_race != Race.Zerg and self.bot.time > 43:
            await self._handle_worker_scout()
        await self._handle_worker_rush()
        await self._handle_proxy_rush()
        await self._manager_overseer(overlords)
        await self._handle_nydus_overseer(overlords)

    def _adjust_attack_target(self) -> None:
        """Update attack target for the queens"""
        if not hasattr(self, "queens"):
            return

        enemy_units: Units = self.bot.enemy_units.filter(
            lambda u: u.type_id not in ATTACK_TARGET_IGNORE
            and not u.is_flying
            # and not u.is_burrowed
            and not u.is_cloaked
            and not u.is_hallucination
        )
        num_units, center_mass = self._find_center_mass(enemy_units)
        enemy_structures: Units = self.bot.enemy_structures
        if enemy_units(UnitID.WIDOWMINEBURROWED):
            self.queens.update_attack_target(
                enemy_units.closest_to(self.bot.start_location).position
            )
        elif num_units > 6:
            self.queens.update_attack_target(center_mass)
        elif enemy_structures:
            self.queens.update_attack_target(
                enemy_structures.closest_to(self.bot.start_location).position
            )
        elif enemy_units:
            self.queens.update_attack_target(
                enemy_units.closest_to(self.bot.start_location).position
            )
        else:
            self.queens.update_attack_target(self.bot.enemy_start_locations[0])

    def _find_center_mass(self, units: Units, distance: int = 12) -> Tuple[int, Point2]:
        """
        Given a selection of units, find the point of biggest mass
        @param distance:
        @param units:
        @return:
        """
        max_units_found: int = 0
        position: Point2 = self.bot.start_location
        if units:
            points = np.array([u.position for u in units])
            distances = spatial.distance.cdist(points, points, "sqeuclidean")
            for i in range(distances.shape[0]):
                close_unit_array = distances[i][(distances[i] < distance)]
                if close_unit_array.shape[0] > max_units_found:
                    max_units_found = close_unit_array.shape[0]
                    position = points[i]
            position = Point2(position)
        return max_units_found, position

    def assign_drone_back_to_gathering(self, drone_tag: int) -> None:
        if drone_tag in self.bot.unselectable_worker_tags:
            self.bot.unselectable_worker_tags.remove(drone_tag)

    async def _handle_proxy_rush(self) -> None:
        bunkers: Units = self.bot.enemy_structures.filter(
            lambda s: s.type_id in PROXY_STATIC_DEFENCE
            and s.distance_to(self.bot.start_location) < 60
        )
        marines: Units = self.bot.enemy_units.filter(
            lambda s: s.type_id == UnitID.MARINE
            and s.distance_to(self.bot.start_location) < 60
        )
        scvs: Units = self.bot.enemy_units.filter(
            lambda s: s.type_id == UnitID.SCV
            and s.distance_to(self.bot.start_location) < 60
        )

        if len(self.bunker_drone_tags) < 7:
            if worker := self.worker_manager.select_worker(self.bot.start_location):
                self.bunker_drone_tags.add(worker.tag)

        if drones := self.bot.workers.tags_in(self.bunker_drone_tags):
            for drone in drones:
                if bunkers or marines or scvs:
                    if drone.health_percentage < 0.3:
                        drone.gather(
                            self.bot.mineral_field.closest_to(self.bot.start_location)
                        )
                        self.bunker_drone_tags.remove(drone.tag)
                        self.assign_drone_back_to_gathering(drone.tag)
                    elif marines:
                        drone.attack(marines.closest_to(drone))
                    elif scvs:
                        drone.attack(scvs.closest_to(drone))
                    else:
                        drone.attack(bunkers.closest_to(drone))
                else:
                    drone.gather(
                        self.bot.mineral_field.closest_to(self.bot.start_location)
                    )
                    self.bunker_drone_tags.remove(drone.tag)
                    self.assign_drone_back_to_gathering(drone.tag)

    async def _handle_worker_rush(self) -> None:
        """zerglings too !"""
        # got to a point in time we don't care about this anymore, hopefully there are Queens around
        if self.bot.time > 200.0 and not self.enemy_committed_worker_rush:
            return

        def stack_detected(_enemy_workers: Units) -> bool:
            if (
                not _enemy_workers
                or _enemy_workers.amount <= 5
                or self.bot.distance_math_hypot_squared(
                    _enemy_workers.center, self.bot.start_location
                )
                > 122
            ):
                return False
            return _enemy_workers.closer_than(0.5, _enemy_workers.center).amount > 5

        enemy_workers: Units = self.bot.enemy_units.filter(
            lambda u: u.type_id
            in {UnitID.DRONE, UnitID.PROBE, UnitID.SCV, UnitID.ZERGLING}
            and (
                u.distance_to(self.bot.start_location) < 25.0
                or u.distance_to(self.terrain_manager.natural_location) < 4.0
            )
        )

        enemy_lings: Units = enemy_workers(UnitID.ZERGLING)

        # this makes sure we go all in after defending
        if enemy_workers.amount > 8 and self.bot.time < 180:
            self.enemy_committed_worker_rush = True

        # cancel expansion, so we can build more drones
        if (
            self.enemy_committed_worker_rush
            and self.bot.time < 180
            and not self.cancelled_structures
        ):
            for structure in self.bot.structures(
                {UnitID.HATCHERY, UnitID.SPAWNINGPOOL}
            ):
                abilities = await self.bot.get_available_abilities(structure)
                if AbilityId.CANCEL_BUILDINPROGRESS in abilities:
                    structure(AbilityId.CANCEL_BUILDINPROGRESS)
            self.cancelled_structures = True

        # calculate how many workers we should use to defend
        num_enemy_workers: int = enemy_workers.amount
        if num_enemy_workers > 0 and self.bot.workers:
            workers_needed: int = (
                num_enemy_workers
                if num_enemy_workers <= 6 and enemy_lings.amount <= 3
                else self.bot.workers.amount
            )
            if len(self.worker_defence_tags) < workers_needed:
                workers_to_take: int = workers_needed - len(self.worker_defence_tags)
                unassigned_workers: Units = self.bot.workers.tags_not_in(
                    self.worker_defence_tags
                )
                if workers_to_take > 0:
                    workers: Units = unassigned_workers.take(workers_to_take)
                    self.bot.unselectable_worker_tags.update(workers.tags)
                    for worker in workers:
                        self.worker_manager.remove_worker_from_mineral(worker.tag)
                    self.worker_defence_tags.extend(workers.tags)

        # actually defend if there is a worker threat
        if len(self.worker_defence_tags) > 0 and self.bot.mineral_field:
            close_mfs: Units = self.bot.mineral_field.closer_than(
                8, self.bot.start_location
            )
            defence_workers: Units = self.bot.workers.tags_in(self.worker_defence_tags)
            close_mineral_patch: Unit = self.bot.mineral_field.closest_to(
                self.bot.start_location
            )
            if defence_workers and enemy_workers:
                for worker in defence_workers:
                    if (
                        enemy_workers
                        and enemy_workers.closest_to(close_mineral_patch).distance_to(
                            close_mineral_patch
                        )
                        > 2
                        and stack_detected(enemy_workers)
                    ):
                        worker.gather(close_mineral_patch)
                    # in attack range of enemy, prioritise attacking
                    elif (
                        worker.weapon_cooldown == 0
                        and enemy_workers.in_attack_range_of(worker)
                    ):
                        worker.attack(enemy_workers.closest_to(worker))
                    # attack the workers
                    elif worker.weapon_cooldown == 0 and enemy_workers:
                        worker.attack(enemy_workers.closest_to(worker))
                    else:
                        worker.gather(close_mineral_patch)
            # enemy worker rushed but they have no workers now, go for the kill
            elif (
                self.enemy_committed_worker_rush
                and defence_workers
                and self.bot.enemy_race != Race.Terran
            ):
                for worker in defence_workers:
                    if worker.weapon_cooldown == 0:
                        worker.attack(self.bot.enemy_start_locations[0])
                    elif close_mfs and worker.health_percentage < 0.4:
                        worker.gather(close_mineral_patch)
            elif defence_workers:
                for worker in defence_workers:
                    self.assign_drone_back_to_gathering(worker.tag)
                    worker.gather(close_mineral_patch)
                self.worker_defence_tags = []

    async def _handle_worker_scout(self) -> None:
        if self.worker_scout_tag == 0:
            worker: Unit = self.worker_manager.select_worker(self.bot.start_location)
            if worker:
                self.worker_scout_tag = worker.tag
        else:
            scout_location: Point2 = self.terrain_manager.natural_location.towards(
                self.bot.game_info.map_center, 35
            )
            scouts: Units = self.bot.workers.tags_in([self.worker_scout_tag])
            if scouts:
                scout: Unit = scouts.first
                enemy_structures: Units = self.bot.enemy_structures.filter(
                    lambda s: s.type_id
                    in {UnitID.BARRACKS, UnitID.FACTORY, UnitID.BUNKER, UnitID.PYLON}
                    and self.bot.distance_math_hypot(scout.position, s.position) < 40
                )
                enemy_workers: Units = self.bot.enemy_units(UnitID.SCV)
                if not self.issued_scout_command:
                    scout.move(scout_location)
                    for _ in range(2):
                        for el in self.terrain_manager.expansions[1:4]:
                            scout.move(el[0], queue=True)
                    self.issued_scout_command = True
                elif enemy_structures:
                    target: Unit = enemy_structures.closest_to(scout)
                    if enemy_workers and enemy_workers.closer_than(25, target.position):
                        scout.attack(enemy_workers.closest_to(scout))
                    else:
                        scout.attack(target)
                elif scout.is_idle and self.bot.mineral_field:
                    scout.gather(
                        self.bot.mineral_field.closest_to(self.bot.start_location)
                    )
                    self.assign_drone_back_to_gathering(scout.tag)

    async def _handle_nydus_overseer(self, overlords: Units) -> None:
        nydus_overseers: Units = self.bot.units.filter(
            lambda u: u.tag == self.nydus_overseer_tag
        )
        if not nydus_overseers:
            new_tag: Optional[int] = await self._morph_overseer(overlords)
            if new_tag and new_tag != self.overseer_tag:
                self.nydus_overseer_tag = new_tag

        else:
            for overseer in nydus_overseers:
                overseer.move(
                    self.terrain_manager.optimal_nydus_location.towards(
                        self.bot.enemy_start_locations[0], -5
                    )
                )

    async def _manager_overseer(self, overlords: Units) -> None:
        overseers: Units = self.bot.units.filter(lambda u: u.tag == self.overseer_tag)
        if not overseers:
            new_tag: Optional[int] = await self._morph_overseer(overlords)
            if new_tag and new_tag != self.nydus_overseer_tag:
                self.overseer_tag = new_tag

        else:
            queens: Units = self.bot.units(UnitID.QUEEN)
            if queens:
                position: Point2 = queens.closest_to(
                    self.queens.defence.policy.attack_target
                ).position
                for overseer in overseers:
                    overseer.move(position.towards(self.bot.start_location, 5))

    async def _morph_dropperlord(self, overlords: Units) -> None:
        # morph a dropperlord so queens-sc2 can make use of the creep queen dropperlord
        dropperlords: Units = self.bot.units.tags_in(self.creep_queen_dropperlord_tags)
        if not dropperlords:
            if (
                overlords
                and self.bot.minerals >= 25
                and self.bot.vespene >= 25
                and (
                    self.bot.structures(UnitID.LAIR).ready
                    or self.bot.structures(UnitID.HIVE)
                )
            ):
                overlord: Unit = overlords.filter(
                    lambda u: u.tag != self.overseer_tag
                ).closest_to(self.bot.start_location)
                if overlord:
                    overlord(AbilityId.MORPH_OVERLORDTRANSPORT)
                    self.creep_queen_dropperlord_tags.add(overlord.tag)

    async def _morph_overseer(self, overlords: Units) -> Optional[int]:
        """Returns the tag of the new overseer"""
        if (
            overlords
            and self.bot.can_afford(UnitID.OVERSEER)
            and (
                self.bot.structures(UnitID.LAIR).ready
                or self.bot.structures(UnitID.HIVE)
            )
            and not self.bot.already_pending(UnitID.OVERSEER)
        ):
            if overlords := overlords.filter(
                lambda u: u.tag not in self.creep_queen_dropperlord_tags
            ):
                overlord: Unit = overlords.closest_to(self.bot.start_location)
                overlord(AbilityId.MORPH_OVERSEER, subtract_cost=True)
                return overlord.tag

    def _manage_queen_policy(self, iteration: int) -> None:
        if iteration == 0:
            self.queens.set_new_policy(self.early_game_queen_policy, reset_roles=True)

        if not self.switched_queen_policy and self.bot.time > 420:
            self.switched_queen_policy = True
            self.queens.set_new_policy(self.mid_game_queen_policy, reset_roles=True)

    def handle_second_overlord(self, overlord: UnitID) -> None:
        if not self.sent_second_overlord:
            self.sent_second_overlord = True
            overlord.move(
                self.terrain_manager.natural_location.towards(
                    self.bot.game_info.map_center, 20
                )
            )
