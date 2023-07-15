from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

import numpy as np
from ares.consts import UnitRole
from ares.cython_extensions.combat_utils import cy_attack_ready, cy_pick_enemy_target
from ares.cython_extensions.geometry import cy_distance_to
from ares.cython_extensions.units_utils import cy_closest_to, cy_in_attack_range
from MapAnalyzer import MapData
from queens_sc2.consts import QueenRoles
from queens_sc2.queens import Queens
from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units
from scipy import spatial

from bot.consts import ATTACK_TARGET_IGNORE, PROXY_STATIC_DEFENCE
from bot.manager import Manager
from bot.terrain_manager import TerrainManager

if TYPE_CHECKING:
    from ares import AresBot

MELEE_TYPES: set[UnitID] = {UnitID.DRONE, UnitID.PROBE, UnitID.SCV, UnitID.ZERGLING}


class UnitManager(Manager):
    queens: Queens

    def __init__(
        self,
        bot: "AresBot",
        terrain_manager: TerrainManager,
    ):
        super().__init__(bot)
        self.map_data: MapData = self.bot.mediator.get_map_data_object
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
        self.ol_spots_index: int = 0

    @property
    def defensive_queen_policy(self) -> Dict:
        return {
            "creep_queens": {
                "active": True,
                "distance_between_queen_tumors": 3,
                "first_tumor_position": self.terrain_manager.natural_location.towards(
                    self.bot.game_info.map_center, 9
                ),
                "priority": True,
                "prioritize_creep": lambda: True,
                "max": 1,
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
                "pass_own_threats": True,
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
    def early_game_queen_policy(self) -> Dict:
        return {
            "creep_queens": {
                "active": True,
                "distance_between_queen_tumors": 3,
                "priority": True,
                "prioritize_creep": lambda: True,
                "max": 4,
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
                "pass_own_threats": True,
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
                "pass_own_threats": True,
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
        policy = (
            self.defensive_queen_policy
            if self.bot.build_order_runner.chosen_opening == "Safe"
            else self.early_game_queen_policy
        )

        # initiating queens-sc2` here
        self.queens = Queens(
            self.bot,
            debug=False,
            queen_policy=policy,
            map_data=self.map_data,
        )
        self.bot.units(UnitID.OVERLORD).first.move(self.bot.mediator.get_own_nat)

    def _check_should_be_offensive(self) -> None:
        own_army_dict: dict[UnitID, Units] = self.bot.mediator.get_own_army_dict
        if UnitID.QUEEN not in own_army_dict:
            return

        queens: Units = self.bot.mediator.get_own_army_dict[UnitID.QUEEN]
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
                and cy_distance_to(u.position, self.bot.enemy_start_locations[0]) > 50
                and not self.bot.mediator.get_main_ground_threats_near_townhall
                and not self.bot.mediator.get_main_air_threats_near_townhall
            )
            or UnitID.NYDUSCANAL in self.bot.mediator.get_own_structures_dict
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
                # update desired creep paths
                self._update_creep_paths()

            await self.queens.manage_queens(
                iteration,
                creep_queen_dropperlord_tags=self.creep_queen_dropperlord_tags,
                grid=self.ground_grid,
                # these are optional, but prevents `queens-sc2` calculating them
                # since `ares` already does it :)
                ground_threats_near_bases=self.bot.mediator.get_main_ground_threats_near_townhall,
                air_threats_near_bases=self.bot.mediator.get_main_air_threats_near_townhall,
            )
            self._manage_queen_policy(iteration)

        if iteration % 32 == 0:
            self._adjust_attack_target()

        overlords: Units = self.bot.units(UnitID.OVERLORD)

        await self._morph_dropperlord(overlords)
        _scout_time: float = 43.0 if self.bot.race != Race.Terran else 24.0
        if self.bot.enemy_race != Race.Zerg and self.bot.time > _scout_time:
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
        self.bot.mediator.assign_role(tag=drone_tag, role=UnitRole.GATHERING)

    async def _handle_proxy_rush(self) -> None:
        bunkers: Units = self.bot.enemy_structures.filter(
            lambda s: s.type_id in PROXY_STATIC_DEFENCE
            and cy_distance_to(s.position, self.bot.start_location) < 60.0
        )
        marines: Units = self.bot.enemy_units.filter(
            lambda s: s.type_id == UnitID.MARINE
            and cy_distance_to(s.position, self.bot.start_location) < 60.0
        )
        scvs: Units = self.bot.enemy_units.filter(
            lambda s: s.type_id == UnitID.SCV
            and cy_distance_to(s.position, self.bot.start_location) < 60.0
        )

        if bunkers and len(self.bunker_drone_tags) < 7:
            if worker := self.bot.mediator.select_worker(
                target_position=self.bot.start_location
            ):
                self.bunker_drone_tags.add(worker.tag)
                self.bot.mediator.assign_role(tag=worker.tag, role=UnitRole.DEFENDING)

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
                        cy_closest_to(drone.position, marines)
                        drone.attack(cy_closest_to(drone.position, marines))
                    elif scvs:
                        drone.attack(cy_closest_to(drone.position, scvs))
                    else:
                        drone.attack(cy_closest_to(drone.position, bunkers))
                else:
                    drone.gather(
                        cy_closest_to(self.bot.start_location, self.bot.mineral_field)
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
            lambda u: u.type_id in MELEE_TYPES
            and (
                cy_distance_to(u.position, self.bot.start_location) < 25.0
                or cy_distance_to(u.position, self.bot.mediator.get_own_nat) < 4.0
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
                if structure.build_progress < 1.0:
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
                for _ in range(workers_to_take):
                    if worker := self.bot.mediator.select_worker(
                        target_position=self.bot.start_location
                    ):
                        self.bot.mediator.assign_role(
                            tag=worker.tag, role=UnitRole.DEFENDING
                        )
                        self.worker_defence_tags.append(worker.tag)

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
                    in_attack_range: list[Unit] = cy_in_attack_range(
                        worker, enemy_workers
                    )
                    in_range_target: Optional[Unit] = None
                    if in_attack_range:
                        in_range_target = cy_pick_enemy_target(in_attack_range)
                    closest_enemy: Unit = cy_closest_to(worker.position, enemy_workers)
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
                    elif in_range_target and cy_attack_ready(
                        self.bot, worker, in_range_target
                    ):
                        worker.attack(in_range_target)
                    # attack the workers
                    elif cy_attack_ready(self.bot, worker, closest_enemy):
                        worker.attack(closest_enemy)
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
            if worker := self.bot.mediator.select_worker(
                target_position=self.bot.start_location
            ):
                self.worker_scout_tag = worker.tag
                self.bot.mediator.assign_role(tag=worker.tag, role=UnitRole.SCOUTING)
        else:
            scout_location: Point2 = self.terrain_manager.natural_location.towards(
                self.bot.game_info.map_center, 35
            )
            if scout := self.bot.unit_tag_dict.get(self.worker_scout_tag, None):
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
        if not self.switched_queen_policy and self.bot.time > 420:
            self.switched_queen_policy = True
            self.queens.set_new_policy(self.mid_game_queen_policy, reset_roles=True)

    def handle_overlord(self, overlord: Unit) -> None:
        if not self.sent_second_overlord:
            self.sent_second_overlord = True
            overlord.move(
                self.terrain_manager.natural_location.towards(
                    self.bot.game_info.map_center, 20
                )
            )
        else:
            ol_spots: list[Point2] = self.bot.mediator.get_ol_spots

            if self.ol_spots_index != len(ol_spots):
                grid = (
                    self.bot.mediator.get_ground_grid
                    if self.bot.enemy_race == Race.Zerg
                    else self.bot.mediator.get_air_vs_ground_grid
                )
                ol_spot: Point2 = ol_spots[self.ol_spots_index]
                if path := self.bot.mediator.find_low_priority_path(
                    start=overlord.position,
                    target=ol_spot,
                    grid=grid,
                ):
                    for point in path:
                        overlord.move(point, queue=True)

                self.ol_spots_index += 1

    def _update_creep_paths(self) -> None:
        creep_paths = []
        # early game, concentrate pushing creep out of the natural
        if self.bot.time < 210.0:
            own_nat: Point2 = self.bot.mediator.get_own_nat
            hatchery_at_nat = self.bot.townhalls.filter(
                lambda _th: _th.is_ready
                and cy_distance_to(_th.position, own_nat) < 10.0
            )
            if hatchery_at_nat:
                creep_paths.append((own_nat, self.bot.mediator.get_enemy_nat))
                creep_paths.append((own_nat, self.bot.mediator.get_defensive_third))
                self.queens.update_creep_targets(creep_paths)
                return

        for th in self.bot.ready_townhalls:
            for el in self.bot.expansion_locations_list:
                if th.position != el and el != self.bot.start_location:
                    creep_paths.append((th.position, el))
        self.queens.update_creep_targets(creep_paths)
