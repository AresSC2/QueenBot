from typing import List

from ares import AresBot
from ares.behaviors.macro import AutoSupply, SpawnController
from ares.consts import UnitRole
from cython_extensions import cy_closest_to, cy_distance_to, cy_towards, cy_unit_pending
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from bot.manager import Manager
from bot.terrain_manager import TerrainManager
from bot.unit_manager import UnitManager

MAX_SPINES: int = 4
REQUIRED_UPGRADES: List[AbilityId] = [
    AbilityId.RESEARCH_ZERGMISSILEWEAPONSLEVEL1,
    AbilityId.RESEARCH_ZERGMISSILEWEAPONSLEVEL2,
    AbilityId.RESEARCH_ZERGMISSILEWEAPONSLEVEL3,
    AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL1,
    AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL2,
    AbilityId.RESEARCH_ZERGGROUNDARMORLEVEL3,
]


class ProductionManager(Manager):
    def __init__(
        self,
        bot: AresBot,
        terrain_manager: TerrainManager,
        unit_manager: UnitManager,
    ):
        super().__init__(bot)
        self.MACRO_HATCH_INTERVAL: float = 20.0
        self.last_macro_hatch_time: float = 0.0
        self.terrain_manager: TerrainManager = terrain_manager
        self.unit_manager: UnitManager = unit_manager

        self._added_third_hatch: bool = False
        self._chosen_opening: str = self.bot.build_order_runner.chosen_opening.upper()

    @property
    def need_overlord(self) -> bool:
        if self.bot.supply_cap < 200:
            # supply blocked / overlord killed, ok to get extra overlords
            if (
                self.bot.supply_left <= 0
                and self.bot.supply_used >= 28
                and cy_unit_pending(self.bot, UnitID.OVERLORD)
                < (self.bot.townhalls.ready.amount + 1)
            ):
                return True
            # just one at a time at low supply counts
            elif (
                40 > self.bot.supply_used >= 13
                and self.bot.supply_left < 3
                and cy_unit_pending(self.bot, UnitID.OVERLORD) < 1
            ):
                return True
            # overlord production scales up depending on bases taken
            elif (
                self.bot.supply_left < 3 * self.bot.townhalls.amount
                and cy_unit_pending(self.bot, UnitID.OVERLORD)
                < (self.bot.townhalls.ready.amount - 1)
            ):
                return True
        return False

    @property
    def num_workers(self) -> int:
        return int(self.bot.supply_workers) + cy_unit_pending(self.bot, UnitID.DRONE)

    async def update(self, iteration: int) -> None:
        # we have a set build order we run through the `ares-sc2` build runner
        if (
            not self.bot.build_order_runner.build_completed
            and not self.unit_manager.enemy_committed_worker_rush
        ):
            return

        # add 3rd hatch to track soon as standard BO is done
        if not self._added_third_hatch and "STANDARD" in self._chosen_opening:
            loc: Point2 = self.bot.mediator.get_defensive_third
            if worker := self.bot.mediator.select_worker(target_position=loc):
                self.bot.mediator.build_with_specific_worker(
                    worker=worker, structure_type=UnitID.HATCHERY, pos=loc
                )
                self.bot.mediator.assign_role(tag=worker.tag, role=UnitRole.BUILDING)
                self._added_third_hatch = True

        if "SAFE" in self._chosen_opening and self.bot.time < 360.0:
            await self._manage_spines()

        idle_townhalls: Units = self.bot.townhalls.filter(
            lambda s: s.is_ready and s.is_idle
        )

        if (
            self.unit_manager.enemy_committed_worker_rush
            and self.bot.larva
            and self.bot.can_afford(UnitID.DRONE)
        ):
            self.bot.larva.first.train(UnitID.DRONE)
            return

        await self._place_nydus_worm()
        # don't need lair, but don't bother till a lair is ready
        if UnitID.LAIR in self.bot.mediator.get_own_structures_dict:
            await self._research_overlord_speed(idle_townhalls)
        await self._manage_larva_production(idle_townhalls)
        await self._upgrade_townhalls(idle_townhalls)
        await self._manage_upgrades()

        self.bot.register_behavior(
            SpawnController({UnitID.QUEEN: {"proportion": 1.0, "priority": 0}})
        )

        await self._morph_core_structures()

        if (
            self.bot.minerals > 600
            and len(self.bot.townhalls) < 20
            and self.bot.time > self.last_macro_hatch_time + self.MACRO_HATCH_INTERVAL
        ):
            await self._build_structure(
                UnitID.HATCHERY, self.bot.game_info.map_center, max_distance=50
            )
            self.last_macro_hatch_time = self.bot.time

    async def _build_structure(
        self,
        structure_type: UnitID,
        pos: Point2,
        max_distance: int = 20,
        random_alternative: bool = True,
    ) -> None:
        build_pos: Point2 = await self.bot.find_placement(
            structure_type,
            pos,
            random_alternative=random_alternative,
            max_distance=max_distance,
        )
        if build_pos:
            if worker := self.bot.mediator.select_worker(target_position=build_pos):
                self.bot.mediator.build_with_specific_worker(
                    worker=worker, structure_type=structure_type, pos=build_pos
                )
                self.bot.mediator.assign_role(tag=worker.tag, role=UnitRole.BUILDING)

    async def _manage_larva_production(self, idle_townhalls: Units) -> None:
        # drones and overlords from larva
        if self.bot.larva and self.bot.minerals >= 50:
            # overlords
            self.bot.register_behavior(AutoSupply(self.bot.start_location))
            # if self.need_overlord and self.bot.can_afford(UnitID.OVERLORD):
            #     self.bot.larva.first.train(UnitID.OVERLORD)
            # build workers
            if self.bot.supply_left >= 1 and self.bot.minerals < 800:
                max_workers: int = 38 if self.bot.townhalls.amount <= 2 else 65
                if (
                    self.num_workers <= max_workers
                    and self.bot.can_afford(UnitID.DRONE)
                    and (
                        idle_townhalls.amount == 0
                        or not self.bot.structures.filter(
                            lambda s: s.type_id == UnitID.SPAWNINGPOOL and s.is_ready
                        )
                    )
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
        idle_evos: Units = self.bot.structures(UnitID.EVOLUTIONCHAMBER).idle
        if idle_evos:
            for upgrade in REQUIRED_UPGRADES:
                if upgrade not in self.bot.state.upgrades and self.bot.can_afford(
                    upgrade
                ):
                    idle_evos.first(upgrade)

    async def _morph_core_structures(self) -> None:
        building_counter: dict[UnitID, int] = self.bot.mediator.get_building_counter
        own_structures_dict: dict[
            UnitID, Units
        ] = self.bot.mediator.get_own_structures_dict
        # spawning pool
        if (
            len(self.unit_manager.worker_defence_tags) == 0
            and UnitID.SPAWNINGPOOL not in own_structures_dict
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
        if (
            self.bot.can_afford(UnitID.HATCHERY)
            and self.bot.mediator.get_building_counter[UnitID.HATCHERY] == 0
        ):
            if location := await self.bot.get_next_expansion():
                await self._build_structure(
                    UnitID.HATCHERY, location, random_alternative=False
                )

        # evo chambers
        num_evos: int = building_counter[UnitID.EVOLUTIONCHAMBER]
        max_evos: int = 2
        worker_limit: int = 32 if self._chosen_opening == "SAFE" else 56

        if UnitID.EVOLUTIONCHAMBER in own_structures_dict:
            num_evos += len(own_structures_dict[UnitID.EVOLUTIONCHAMBER])

        if (
            self.num_workers > worker_limit
            and num_evos < max_evos
            and self.bot.can_afford(UnitID.EVOLUTIONCHAMBER)
            and building_counter[UnitID.EVOLUTIONCHAMBER] == 0
        ):
            await self._build_structure(
                UnitID.EVOLUTIONCHAMBER,
                Point2(
                    cy_towards(
                        self.bot.start_location, self.bot.game_info.map_center, 4.0
                    )
                ),
            )

        # extractors
        min_worker = 30 if self._chosen_opening == "SAFE" else 38
        max_extractors: int = 2 if self.num_workers >= min_worker else 0
        if (
            self.bot.gas_buildings.amount < max_extractors
            and self.bot.can_afford(UnitID.EXTRACTOR)
            and building_counter[UnitID.EXTRACTOR] == 0
        ):
            if worker := self.bot.mediator.select_worker(
                target_position=self.bot.start_location
            ):
                geysers: Units = self.bot.vespene_geyser.filter(
                    lambda vg: not self.bot.gas_buildings.closer_than(2, vg)
                )
                self.bot.mediator.build_with_specific_worker(
                    worker=worker,
                    structure_type=UnitID.EXTRACTOR,
                    pos=cy_closest_to(self.bot.start_location, geysers),
                )
                self.bot.mediator.assign_role(tag=worker.tag, role=UnitRole.BUILDING)

        # nydus network
        if (
            (self.bot.townhalls(UnitID.LAIR).ready or self.bot.townhalls(UnitID.HIVE))
            and UnitID.NYDUSNETWORK not in own_structures_dict
            and self.bot.can_afford(UnitID.NYDUSNETWORK)
            and building_counter[UnitID.NYDUSNETWORK] == 0
        ):
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
                self.bot.townhalls(UnitID.LAIR).ready or self.bot.townhalls(UnitID.HIVE)
            )
            and UnitID.INFESTATIONPIT not in own_structures_dict
            and self.bot.can_afford(UnitID.INFESTATIONPIT)
            and building_counter[UnitID.INFESTATIONPIT] == 0
        ):
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
            and self.bot.units(UnitID.QUEEN).amount > 10
            and not self.bot.already_pending(UnitID.LAIR)
        ):
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

    async def _manage_spines(self):
        if self.bot.time < 135.0:
            return

        own_structures_dict: dict[
            UnitID, Units
        ] = self.bot.mediator.get_own_structures_dict
        if UnitID.SPAWNINGPOOL not in own_structures_dict:
            return

        if own_structures_dict[UnitID.SPAWNINGPOOL].ready:
            own_nat: Point2 = self.bot.mediator.get_own_nat
            hatch_at_nat: list[Unit] = [
                h
                for h in self.bot.townhalls
                if cy_distance_to(h.position, own_nat) < 5.0 and h.is_ready
            ]
            if len(hatch_at_nat) == 0:
                return

            num_current_spines: int = self.bot.mediator.get_building_counter[
                UnitID.SPINECRAWLER
            ]
            # only have one drone on journey to position at a time
            if num_current_spines > 0:
                return

            if UnitID.SPINECRAWLER in own_structures_dict:
                num_current_spines += len(own_structures_dict[UnitID.SPINECRAWLER])

            if num_current_spines >= MAX_SPINES:
                return

            await self._build_structure(
                UnitID.SPINECRAWLER,
                own_nat.towards(self.bot.game_info.map_center, 5.9),
            )
