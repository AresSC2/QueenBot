from typing import Callable, Any

from cython_extensions import cy_towards, cy_unit_pending
from cython_extensions.geometry import cy_distance_to_squared
from sc2.ids.ability_id import AbilityId
from sc2.ids.upgrade_id import UpgradeId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

from ares.consts import UnitRole

from ares import AresBot
from ares.behaviors.macro import (
    MacroPlan,
    AutoSupply,
    BuildWorkers,
    UpgradeController,
    SpawnController,
    ExpansionController,
    GasBuildingController,
    TechUp,
    ProductionController,
)
from bot.consts import RequestType
from bot.managers.queen_bot_mediator import QueenBotMediator


class MacroManager:
    queen_bot_mediator: QueenBotMediator

    def __init__(self, ai: "AresBot"):
        self.ai: AresBot = ai

        self.queen_bot_requests_dict: dict[RequestType, Callable] = {}
        self.MACRO_HATCH_INTERVAL: float = 20.0
        self.last_macro_hatch_time: float = 0.0

    def manager_request(
        self,
        receiver: str,
        request: RequestType,
        reason: str = None,
        **kwargs,
    ) -> Any:
        """Fetch information from this Manager so another Manager can use it.

        Parameters
        ----------
        receiver :
            This Manager.
        request :
            What kind of request is being made
        reason :
            Why the reason is being made
        kwargs :
            Additional keyword args if needed for the specific request, as determined
            by the function signature (if appropriate)

        Returns
        -------
        Optional[Union[Dict, DefaultDict, Coroutine[Any, Any, bool]]] :
            Everything that could possibly be returned from the Manager fits in there

        """
        return self.queen_bot_requests_dict[request](kwargs)

    @property
    def max_workers(self) -> int:
        return min(70, len(self.ai.townhalls) * 22)

    @property
    def required_upgrades(self) -> list[UpgradeId]:
        return [
            UpgradeId.ZERGMISSILEWEAPONSLEVEL1,
            UpgradeId.ZERGMISSILEWEAPONSLEVEL2,
            UpgradeId.ZERGMISSILEWEAPONSLEVEL3,
            UpgradeId.ZERGGROUNDARMORSLEVEL1,
            UpgradeId.ZERGGROUNDARMORSLEVEL2,
            UpgradeId.ZERGGROUNDARMORSLEVEL3,
            UpgradeId.OVERLORDSPEED,
        ]

    @property
    def upgrades_enabled(self) -> bool:
        return (self.ai.vespene > 95) or (
            self.ai.minerals > 500 and self.ai.vespene > 350
        )

    async def update(self) -> None:
        if not self.ai.build_order_runner.build_completed:
            return

        # workers, supply, expand, queens, upgrades etc
        self._do_generic_macro_plan()

        await self._build_macro_hatcheries()
        # macro plan will add a evo, but we want 2 eventually
        await self._build_evos()
        await self._build_nydus_networks()
        # Nydus worm placement is handled by the dedicated NydusManager

        if self.ai.time < 300.0 and self.ai.mediator.get_did_enemy_rush:
            await self._build_spines()

    def _do_generic_macro_plan(self):
        macro_plan: MacroPlan = MacroPlan()
        macro_plan.add(AutoSupply(base_location=self.ai.start_location))
        macro_plan.add(BuildWorkers(to_count=70))
        if self.upgrades_enabled:
            macro_plan.add(
                UpgradeController(
                    upgrade_list=self.required_upgrades,
                    base_location=self.ai.start_location,
                )
            )
        if (
            self.ai.vespene >= 100
            and len(self.ai.mediator.get_own_army_dict[UnitID.QUEEN]) >= 4
        ):
            macro_plan.add(
                TechUp(desired_tech=UnitID.LAIR, base_location=self.ai.start_location)
            )
        if self.ai.supply_workers > 60:
            macro_plan.add(
                TechUp(desired_tech=UnitID.HIVE, base_location=self.ai.start_location)
            )
        macro_plan.add(
            SpawnController(
                army_composition_dict={
                    UnitID.QUEEN: {"proportion": 1.0, "priority": 0}
                },
            )
        )
        macro_plan.add(
            ProductionController(
                army_composition_dict={
                    UnitID.QUEEN: {"proportion": 1.0, "priority": 0}
                },
                base_location=self.ai.start_location,
            )
        )
        if self.ai.supply_workers > 18:
            _max: int = (
                3
                if self.ai.supply_workers > 70
                else (2 if self.ai.supply_workers > 40 else 1)
            )
            macro_plan.add(GasBuildingController(to_count=_max))
        if self.ai.mediator.get_did_enemy_rush and self.ai.supply_army < 16:
            max_pending: int = 0
        else:
            max_pending: int = 2 if self.ai.minerals < 1250 else 4
        macro_plan.add(ExpansionController(to_count=99, max_pending=max_pending))
        self.ai.register_behavior(macro_plan)

    async def _build_evos(self):
        if self.ai.supply_used < 130:
            return
        building_counter = self.ai.mediator.get_building_counter
        own_structures_dict = self.ai.mediator.get_own_structures_dict
        # evo chambers
        num_evos: int = building_counter[UnitID.EVOLUTIONCHAMBER]
        max_evos: int = 2
        worker_limit: int = 56

        if UnitID.EVOLUTIONCHAMBER in own_structures_dict:
            num_evos += len(own_structures_dict[UnitID.EVOLUTIONCHAMBER])

        if (
            int(self.ai.supply_workers) + cy_unit_pending(self.ai, UnitID.DRONE)
            > worker_limit
            and num_evos < max_evos
            and self.ai.can_afford(UnitID.EVOLUTIONCHAMBER)
            and building_counter[UnitID.EVOLUTIONCHAMBER] == 0
        ):
            await self._build_structure(
                UnitID.EVOLUTIONCHAMBER,
                Point2(
                    cy_towards(
                        self.ai.start_location, self.ai.game_info.map_center, 4.0
                    )
                ),
            )

    async def _build_macro_hatcheries(self):
        if (
            self.ai.minerals > 600
            and len(self.ai.townhalls) < 20
            and self.ai.time > self.last_macro_hatch_time + self.MACRO_HATCH_INTERVAL
        ):
            build_pos: Point2 = (
                self.ai.start_location
                if self.ai.time < 420.0
                else self.ai.game_info.map_center
            )
            await self._build_structure(UnitID.HATCHERY, build_pos, max_distance=50)
            self.last_macro_hatch_time = self.ai.time

    async def _build_structure(
        self,
        structure_type: UnitID,
        pos: Point2,
        max_distance: int = 20,
        random_alternative: bool = True,
    ) -> None:
        """Build a structure at a given position"""
        build_pos: Point2 = await self.ai.find_placement(
            structure_type,
            pos,
            random_alternative=random_alternative,
            max_distance=max_distance,
        )
        if build_pos and self.ai.mediator.is_position_safe(
            grid=self.ai.mediator.get_ground_grid, position=build_pos
        ):
            if worker := self.ai.mediator.select_worker(target_position=build_pos):
                self.ai.mediator.build_with_specific_worker(
                    worker=worker, structure_type=structure_type, pos=build_pos
                )
                self.ai.mediator.assign_role(tag=worker.tag, role=UnitRole.BUILDING)

    async def _build_nydus_networks(self):
        # place a network at each base
        building_counter = self.ai.mediator.get_building_counter
        own_structures_dict = self.ai.mediator.get_own_structures_dict
        tech_ready: bool = self.ai.townhalls(UnitID.LAIR).ready or self.ai.townhalls(
            UnitID.HIVE
        )

        if (
            building_counter[UnitID.NYDUSNETWORK] > 0
            or not self.ai.can_afford(UnitID.NYDUSNETWORK)
            or not tech_ready
        ):
            return

        for th in self.ai.townhalls:
            if not th.is_ready:
                continue

            if (
                len(
                    [
                        n
                        for n in own_structures_dict[UnitID.NYDUSNETWORK]
                        if cy_distance_to_squared(n.position, th.position) < 450.0
                    ]
                )
                > 0
            ):
                continue

            _building_pos: Point2 = Point2(
                cy_towards(
                    th.position,
                    self.ai.game_info.map_center,
                    4,
                )
            )

            await self._build_structure(UnitID.NYDUSNETWORK, _building_pos)
            # don't place multiple networks in same frame
            break

    async def _build_spines(self):
        if (
            self.ai.mediator.get_building_counter[UnitID.SPINECRAWLER] >= 1
            or len(self.ai.townhalls) < 2
        ):
            return

        build_pos: Point2 = Point2(
            cy_towards(self.ai.mediator.get_own_nat, self.ai.game_info.map_center, 5.9)
        )
        existing_spines: list[Unit] = [
            s
            for s in self.ai.mediator.get_own_structures_dict[UnitID.SPINECRAWLER]
            if cy_distance_to_squared(s.position, build_pos) < 81.0
        ]
        if len(existing_spines) < 2:
            await self._build_structure(UnitID.SPINECRAWLER, build_pos)
