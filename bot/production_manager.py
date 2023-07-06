from typing import List
import numpy as np

from bot.custom_bot_ai import CustomBotAI
from bot.consts import BUILD_ORDER
from bot.manager import Manager
from bot.terrain_manager import TerrainManager
from bot.unit_manager import UnitManager
from bot.worker_manager import WorkerManager
from sc2.data import Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2, Pointlike
from sc2.unit import Unit
from sc2.units import Units


class ProductionManager(Manager):
    def __init__(
        self,
        bot: CustomBotAI,
        worker_manager: WorkerManager,
        terrain_manager: TerrainManager,
        unit_manager: UnitManager,
    ):
        super().__init__(bot)
        self.worker_manager: WorkerManager = worker_manager
        self.terrain_manager: TerrainManager = terrain_manager
        self.unit_manager: UnitManager = unit_manager

        self.build_order: List[UnitID] = BUILD_ORDER
        self.bo_step: int = 0
        self.first_hatch: bool = True
        self.hatch_drone_tag: int = 0

    @property
    def need_overlord(self) -> bool:
        if self.bot.supply_cap < 200:
            # supply blocked / overlord killed, ok to get extra overlords
            if (
                self.bot.supply_left <= 0
                and self.bot.supply_used >= 28
                and self.bot.unit_pending(UnitID.OVERLORD)
                < (self.bot.townhalls.ready.amount + 1)
            ):
                return True
            # just one at a time at low supply counts
            elif (
                40 > self.bot.supply_used >= 13
                and self.bot.supply_left < 3
                and self.bot.unit_pending(UnitID.OVERLORD) < 1
            ):
                return True
            # overlord production scales up depending on bases taken
            elif (
                self.bot.supply_left < 3 * self.bot.townhalls.amount
                and self.bot.unit_pending(UnitID.OVERLORD)
                < (self.bot.townhalls.ready.amount - 1)
            ):
                return True
        return False

    @property
    def num_workers(self) -> int:
        return self.bot.supply_workers  # + int(self.bot.unit_pending(UnitID.DRONE))

    async def update(self, iteration: int) -> None:
        if self.unit_manager.enemy_committed_worker_rush:
            self.bo_step = len(self.build_order) + 1

        # we have a set build order at start of game to ensure we expand and get a pool at a good time
        if self.bo_step < len(self.build_order):
            await self._do_build_order()
            return

        idle_townhalls: Units = self.bot.townhalls.filter(
            lambda s: s.is_ready and s.is_idle
        )

        # if iteration % 8 == 0 and self.bot.enemy_race == Race.Zerg:
        #     await self._manage_static_defence(idle_townhalls)

        if (
            self.unit_manager.enemy_committed_worker_rush
            and self.bot.larva
            and self.bot.can_afford(UnitID.DRONE)
        ):
            self.bot.larva.first.train(UnitID.DRONE)
            return

        await self._place_nydus_worm()
        if not idle_townhalls:
            await self._build_macro_hatcheries()
        await self._upgrade_townhalls(idle_townhalls)
        await self._manage_upgrades()
        await self._manage_larva_production(idle_townhalls)
        if self.bot.supply_left >= 2:
            await self._manage_queen_production(idle_townhalls)
        await self._morph_core_structures()
        await self._research_overlord_speed(idle_townhalls)

    async def _build_structure(
        self, structure_type: UnitID, pos: Point2, random_alternative: bool = True
    ) -> None:
        build_pos: Point2 = await self.bot.find_placement(
            structure_type, pos, random_alternative=random_alternative
        )
        if build_pos:
            worker: Unit = self.worker_manager.select_worker(build_pos)
            if worker:
                worker.build(structure_type, build_pos)

    async def _build_macro_hatcheries(self) -> None:
        """Build extra hatches if mineral bank so we can build more queens"""
        if self.bot.minerals >= 600:
            num_hatches: int = self.bot.structures.filter(
                lambda s: s.type_id == UnitID.HATCHERY
            ).amount
            if num_hatches <= 14:
                np_pos = self.unit_manager.queens.creep.creep_map[
                    np.random.choice(
                        self.unit_manager.queens.creep.creep_map.shape[0],
                        1,
                        replace=False,
                    ),
                    :,
                ]
                pos: Point2 = Point2(Pointlike((np_pos[0][0], np_pos[0][1])))
                placement: Point2 = await self.bot.find_placement(UnitID.HATCHERY, pos)
                if placement:
                    await self._build_structure(UnitID.HATCHERY, placement)

    async def _do_build_order(self) -> None:
        """Static build order, only for the first segment of the game"""
        current_step: UnitID = self.build_order[self.bo_step]
        if (
            current_step in {UnitID.DRONE, UnitID.OVERLORD}
            and self.bot.larva
            and self.bot.can_afford(current_step)
        ):
            self.bot.larva.first.train(current_step)
            self.bo_step += 1
        elif (
            current_step == UnitID.HATCHERY
            and self.bot.minerals > 185
            and self.bot.workers
        ):
            pos: Point2 = self.terrain_manager.natural_location
            if not self.first_hatch:
                pos = self.terrain_manager.defensive_third

            if self.hatch_drone_tag == 0:
                worker: Unit = self.worker_manager.select_worker(pos)
                if worker:
                    self.hatch_drone_tag = worker.tag
                    worker.move(pos)
            elif self.bot.can_afford(UnitID.HATCHERY):
                workers: Units = self.bot.workers.tags_in([self.hatch_drone_tag])
                if workers and self.bot.in_placement_grid(pos):
                    workers.first.build(UnitID.HATCHERY, pos)
                    self.first_hatch = False
                    self.hatch_drone_tag = 0
                    self.bo_step += 1
                # worker is missing or pos not in placement grid, fall back option
                else:
                    await self.bot.expand_now(max_distance=0)
                    self.bo_step += 1

        elif (
            current_step == UnitID.SPAWNINGPOOL
            and self.bot.can_afford(UnitID.SPAWNINGPOOL)
            and self.bot.workers
        ):
            await self._build_structure(
                UnitID.SPAWNINGPOOL,
                self.bot.start_location.towards(self.bot.game_info.map_center, 4),
            )
            self.bo_step += 1
        elif current_step == UnitID.QUEEN and self.bot.can_afford(current_step):
            self.bot.townhalls.first.train(current_step)
            self.bo_step += 1

    async def _manage_larva_production(self, idle_townhalls: Units) -> None:
        # drones and overlords from larva
        if self.bot.larva and self.bot.minerals >= 50:
            # overlords
            if self.need_overlord and self.bot.can_afford(UnitID.OVERLORD):
                self.bot.larva.first.train(UnitID.OVERLORD)
            # build workers
            if self.bot.supply_left >= 1:
                max_workers: int = 35 if self.bot.townhalls.amount <= 3 else 65
                if (
                    self.num_workers <= max_workers
                    and self.bot.can_afford(UnitID.DRONE)
                    and idle_townhalls.amount == 0
                    and self.bot.time > 100
                ):
                    self.bot.larva.first.train(UnitID.DRONE)

    async def _manage_queen_production(self, idle_townhalls: Units) -> None:
        if (
            self.bot.structures(UnitID.SPAWNINGPOOL).ready
            and self.bot.minerals >= 150
            and idle_townhalls
        ):
            idle_townhalls.closest_to(self.bot.enemy_start_locations[0]).train(
                UnitID.QUEEN
            )

    async def _manage_upgrades(self) -> None:
        required_upgrades: List[AbilityId] = [
            AbilityId.RESEARCH_ZERGMISSILEWEAPONSLEVEL1,
            AbilityId.RESEARCH_ZERGMISSILEWEAPONSLEVEL2,
            AbilityId.RESEARCH_ZERGMISSILEWEAPONSLEVEL3,
            AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL1,
            AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL2,
            AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL3,
        ]
        idle_evos: Units = self.bot.structures(UnitID.EVOLUTIONCHAMBER).idle
        if idle_evos:
            for upgrade in required_upgrades:
                if upgrade not in self.bot.state.upgrades and self.bot.can_afford(
                    upgrade
                ):
                    idle_evos.first(upgrade)

    async def _manage_static_defence(self, idle_townhalls) -> None:
        """For each townhall, build a spore and a spine"""
        pool: Units = self.bot.structures(UnitID.SPAWNINGPOOL)
        townhalls: Units = self.bot.townhalls.ready
        if not townhalls or not pool.ready:
            return

        # 4 spine at the front of natural
        if (
            self.bot.can_afford(UnitID.SPINECRAWLER)
            and self.bot.supply_workers > 15
            and townhalls.closer_than(6, self.terrain_manager.natural_location)
        ):
            existing_spines: Units = self.bot.structures.filter(
                lambda s: s.type_id == UnitID.SPINECRAWLER
                and s.distance_to(self.terrain_manager.natural_location) < 10
            )
            if existing_spines.amount < 4:
                position: Point2 = self.terrain_manager.natural_location.towards(
                    self.bot.game_info.map_center, 6
                )
                if self.bot.enemy_units and self.bot.enemy_units.closer_than(
                    12, position
                ):
                    return
                if self.bot.already_pending(UnitID.SPINECRAWLER) < 4:
                    await self._build_structure(UnitID.SPINECRAWLER, position)

    async def _morph_core_structures(self) -> None:
        # spawning pool
        if (
            len(self.unit_manager.worker_defence_tags) == 0
            and not (self.bot.structures(UnitID.SPAWNINGPOOL))
            and self.bot.can_afford(UnitID.SPAWNINGPOOL)
        ):
            if (
                not self.bot.already_pending(UnitID.SPAWNINGPOOL)
                and self.bot.townhalls.ready
            ):
                await self._build_structure(
                    UnitID.SPAWNINGPOOL,
                    self.bot.townhalls.random.position.towards(
                        self.bot.game_info.map_center, 5
                    ),
                )

        # expand
        if self.bot.can_afford(UnitID.HATCHERY):
            location = await self.bot.get_next_expansion()
            if location:
                await self._build_structure(
                    UnitID.HATCHERY, location, random_alternative=False
                )

        # evo chambers
        max_evos: int = 2 if self.bot.supply_used > 124 else 1
        if (
            self.num_workers > 60
            and self.bot.structures(UnitID.EVOLUTIONCHAMBER).amount < max_evos
            and self.bot.can_afford(UnitID.EVOLUTIONCHAMBER)
        ):
            if not self.bot.already_pending(UnitID.EVOLUTIONCHAMBER):
                await self._build_structure(
                    UnitID.EVOLUTIONCHAMBER,
                    self.bot.start_location.towards(self.bot.game_info.map_center, 4),
                )

        # extractors
        max_extractors: int = (
            2 if self.num_workers >= 60 else (1 if self.num_workers > 44 else 0)
        )
        if (
            self.bot.structures(UnitID.EXTRACTOR).amount < max_extractors
            and not self.bot.already_pending(UnitID.EXTRACTOR)
            and self.bot.can_afford(UnitID.EXTRACTOR)
        ):
            worker: Unit = self.worker_manager.select_worker(self.bot.start_location)
            if worker:
                geysers: Units = self.bot.vespene_geyser.filter(
                    lambda vg: not self.bot.gas_buildings.closer_than(2, vg)
                )
                worker.build_gas(geysers.closest_to(self.bot.start_location))

        # nydus network
        if (
            (self.bot.structures(UnitID.LAIR).ready or self.bot.structures(UnitID.HIVE))
            and not self.bot.structures(UnitID.NYDUSNETWORK)
            and self.bot.can_afford(UnitID.NYDUSNETWORK)
        ):
            if not self.bot.already_pending(UnitID.NYDUSNETWORK):
                await self._build_structure(
                    UnitID.NYDUSNETWORK,
                    self.terrain_manager.natural_location.towards(
                        self.bot.game_info.map_center, 4
                    ),
                )

        # inf pit
        if (
            self.bot.supply_used > 128
            and (
                self.bot.structures(UnitID.LAIR).ready
                or self.bot.structures(UnitID.HIVE)
            )
            and not self.bot.structures(UnitID.INFESTATIONPIT)
            and self.bot.can_afford(UnitID.INFESTATIONPIT)
        ):
            if not self.bot.already_pending(UnitID.INFESTATIONPIT):
                await self._build_structure(
                    UnitID.INFESTATIONPIT,
                    self.bot.start_location.towards(self.bot.game_info.map_center, 4),
                )

    async def _upgrade_townhalls(self, idle_townhalls: Units) -> None:

        # lair
        if (
            idle_townhalls
            and self.bot.can_afford(UnitID.LAIR)
            and not self.bot.townhalls(UnitID.LAIR)
            and not self.bot.townhalls(UnitID.HIVE)
            and self.bot.supply_workers > 40
            and self.bot.units(UnitID.QUEEN).amount > 14
        ):
            if not self.bot.already_pending(UnitID.LAIR):
                # all townhalls will be a hatchery if got to here
                th: Unit = idle_townhalls.first
                th(AbilityId.UPGRADETOLAIR_LAIR)

        # hive
        if (
            self.bot.townhalls(UnitID.LAIR).idle
            and self.bot.can_afford(UnitID.HIVE)
            and not self.bot.townhalls(UnitID.HIVE)
            and self.bot.supply_workers > 40
            and self.bot.units(UnitID.QUEEN).amount > 14
            and not self.bot.already_pending(UnitID.HIVE)
        ):
            # all townhalls will be a hatchery if got to here
            th: Unit = self.bot.townhalls(UnitID.LAIR).idle.first
            th(AbilityId.UPGRADETOHIVE_HIVE)

    async def _research_overlord_speed(self, idle_townhalls: Units) -> None:
        if (
            idle_townhalls
            and UpgradeId.OVERLORDSPEED not in self.bot.state.upgrades
            and self.bot.can_afford(AbilityId.RESEARCH_PNEUMATIZEDCARAPACE)
        ):
            idle_townhalls.first(
                AbilityId.RESEARCH_PNEUMATIZEDCARAPACE, subtract_cost=True
            )

    async def _place_nydus_worm(self) -> None:
        networks: Units = self.bot.structures(UnitID.NYDUSNETWORK)
        if networks.ready and (not self.bot.structures(UnitID.NYDUSCANAL)):
            pos = self.terrain_manager.optimal_nydus_location
            if self.bot.is_visible(pos):
                placement = await self.bot.find_placement(
                    UnitID.NYDUSCANAL, pos, 3, False, 1
                )
                if placement:
                    networks.first(AbilityId.BUILD_NYDUSWORM, placement)
