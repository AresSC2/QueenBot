from typing import Optional

from ares import AresBot
from ares.behaviors.combat.individual.tumor_spread_creep import TumorSpreadCreep
from ares.behaviors.macro import Mining
from ares.consts import UnitRole
from cython_extensions.geometry import cy_distance_to_squared
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.unit import Unit

from bot.managers.combat_manager import CombatManager
from bot.managers.macro_manager import MacroManager
from bot.managers.nydus_manager import NydusManager
from bot.managers.queen_bot_mediator import QueenBotMediator
from bot.managers.queen_manager import QueenManager
from bot.managers.scout_manager import ScoutManager
from bot.managers.worker_defence_manager import WorkerDefenceManager
from bot.unit_control.base_control import BaseControl
from bot.unit_control.overlord_creep_spotters import OverlordCreepSpotters


class MyBot(AresBot):
    macro_manager: MacroManager
    queen_manager: QueenManager
    combat_manager: CombatManager
    scout_manager: ScoutManager
    nydus_manager: NydusManager
    worker_defence_manager: WorkerDefenceManager
    _overlord_creep_spotters: BaseControl

    def __init__(self, game_step_override: Optional[int] = None):
        """Initiate custom bot

        Parameters
        ----------
        game_step_override :
            If provided, set the game_step to this value regardless of how it was
            specified elsewhere
        """
        super().__init__(game_step_override)

        self._queen_bot_mediator: QueenBotMediator = QueenBotMediator()
        self.sent_bm: bool = False

    async def on_step(self, iteration: int) -> None:
        await super(MyBot, self).on_step(iteration)
        self.register_behavior(Mining())
        await self.macro_manager.update()
        self.queen_manager.update()
        self.combat_manager.update()
        self.scout_manager.update()
        self.worker_defence_manager.update()
        await self.nydus_manager.update()

        self._overlord_creep_spotters.execute(
            self.mediator.get_units_from_role(role=UnitRole.OVERLORD_CREEP_SPOTTER)
        )

        for tumor in self.structures(UnitID.CREEPTUMORBURROWED):
            self.register_behavior(
                TumorSpreadCreep(tumor, self.enemy_start_locations[0])
            )
        if not self.sent_bm and self.mediator.get_creep_coverage > 85.0:
            await self.chat_send("That's over 85% of the map covered in creep")
            await self.chat_send("How did you let that happen?!")
            self.sent_bm = True

        if "TORCHES" in self.game_info.map_name.upper() and self.supply_workers > 22:
            if mfs := [
                mf
                for mf in self.mineral_field
                if mf.type_id == UnitID.RICHMINERALFIELD
                and cy_distance_to_squared(self.mediator.get_own_nat, mf.position)
                < 2500.0
            ]:
                if clearers := self.mediator.get_units_from_role(
                    role=UnitRole.CONTROL_GROUP_EIGHT
                ):
                    for clearer in clearers:
                        if clearer.is_returning:
                            continue
                        if clearer.is_gathering and clearer.order_target in [
                            t.tag for t in mfs
                        ]:
                            continue
                        clearer.gather(mfs[0])

                elif worker := self.mediator.select_worker(
                    target_position=self.mediator.get_own_nat, force_close=True
                ):
                    self.mediator.assign_role(
                        tag=worker.tag, role=UnitRole.CONTROL_GROUP_EIGHT
                    )
            else:
                self.mediator.switch_roles(
                    from_role=UnitRole.CONTROL_GROUP_EIGHT,
                    to_role=UnitRole.GATHERING,
                )

    async def on_start(self) -> None:
        await super(MyBot, self).on_start()
        self.macro_manager = MacroManager(self)
        self.queen_manager = QueenManager(self)
        self.combat_manager = CombatManager(self)
        self.scout_manager = ScoutManager(self)
        self.nydus_manager = NydusManager(self)
        self.worker_defence_manager = WorkerDefenceManager(self)

        self._queen_bot_mediator.add_managers(
            [
                self.macro_manager,
                self.queen_manager,
                self.combat_manager,
                self.nydus_manager,
                self.scout_manager,
                self.worker_defence_manager,
            ]
        )
        self._overlord_creep_spotters: BaseControl = OverlordCreepSpotters(
            self, self.config, self.mediator
        )

        for unit in self.units(UnitID.OVERLORD):
            self.mediator.assign_role(
                tag=unit.tag, role=UnitRole.OVERLORD_CREEP_SPOTTER
            )

    async def on_unit_created(self, unit: Unit) -> None:
        await super(MyBot, self).on_unit_created(unit)
        if unit.type_id == UnitID.OVERLORD:
            self.mediator.assign_role(
                tag=unit.tag, role=UnitRole.OVERLORD_CREEP_SPOTTER
            )

        if unit.type_id == UnitID.QUEEN:
            self.queen_manager.assign_new_queen(unit)

    async def on_unit_destroyed(self, unit_tag: int) -> None:
        await super(MyBot, self).on_unit_destroyed(unit_tag)

    async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float) -> None:
        await super(MyBot, self).on_unit_took_damage(unit, amount_damage_taken)

        compare_health: float = max(50.0, unit.health_max * 0.09)
        if unit.health < compare_health and unit.is_structure:
            unit(AbilityId.CANCEL_BUILDINPROGRESS)

        # something attacking ol early, set build completed
        # so we can play generically
        if (
            unit.type_id == UnitID.OVERLORD
            and not self.build_order_runner.build_completed
        ):
            self.build_order_runner.set_build_completed()
