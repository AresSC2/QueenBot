from typing import Optional

from sc2.units import Units

from ares import AresBot
from ares.behaviors.combat.individual.tumor_spread_creep import TumorSpreadCreep
from ares.behaviors.macro import Mining
from ares.consts import UnitRole
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.unit import Unit

from bot.managers.combat_manager import CombatManager
from bot.managers.macro_manager import MacroManager
from bot.managers.nydus_manager import NydusManager
from bot.managers.queen_bot_mediator import QueenBotMediator
from bot.managers.queen_manager import QueenManager
from bot.unit_control.base_control import BaseControl
from bot.unit_control.overlord_creep_spotters import OverlordCreepSpotters
from bot.managers.scout_manager import ScoutManager
from bot.managers.worker_defence_manager import WorkerDefenceManager


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

        tumors: list[Unit] = self.mediator.get_own_structures_dict[
            UnitID.CREEPTUMORBURROWED
        ]
        for tumor in tumors:
            self.register_behavior(
                TumorSpreadCreep(tumor, self.enemy_start_locations[0])
            )
        if not self.sent_bm and self.mediator.get_creep_coverage > 85.0:
            await self.chat_send("That's over 85% of the map covered in creep")
            await self.chat_send("How did you let that happen?!")
            self.sent_bm = True

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
