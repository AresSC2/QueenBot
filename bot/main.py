from typing import Optional

from sc2.position import Point2
from sc2.units import Units

from ares import AresBot
from ares.behaviors.combat.individual.queen_spread_creep import QueenSpreadCreep
from ares.behaviors.combat.individual.tumor_spread_creep import TumorSpreadCreep
from ares.behaviors.macro import (
    Mining,
    ProductionController,
    MacroPlan,
    AutoSupply,
    BuildWorkers,
)
from ares.consts import UnitRole, CREEP_TUMOR_TYPES
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.ids.upgrade_id import UpgradeId
from sc2.unit import Unit

from bot.macro_manager import MacroManager
from bot.queen_manager import QueenManager


class MyBot(AresBot):
    macro_manager: MacroManager
    queen_manager: QueenManager

    def __init__(self, game_step_override: Optional[int] = None):
        """Initiate custom bot

        Parameters
        ----------
        game_step_override :
            If provided, set the game_step to this value regardless of how it was
            specified elsewhere
        """
        super().__init__(game_step_override)
        self.sent_bm: bool = False

    async def on_step(self, iteration: int) -> None:
        await super(MyBot, self).on_step(iteration)

        self.register_behavior(Mining())
        self.macro_manager.update()
        self.queen_manager.update()

        ols: Units = self.mediator.get_units_from_role(role=UnitRole.OVERLORD_CREEP_SPOTTER)

        spotter_positions: dict[int, Point2] = self.mediator.get_overlord_creep_spotter_positions(overlords=ols, target_pos=self.enemy_start_locations[0])

        for ol in ols:
            if AbilityId.BEHAVIOR_GENERATECREEPON in ol.abilities:
                ol(AbilityId.BEHAVIOR_GENERATECREEPON)
            elif ol.tag in spotter_positions:
                ol.move(spotter_positions[ol.tag])

        for tumor in self.structures(CREEP_TUMOR_TYPES):
            self.register_behavior(TumorSpreadCreep(tumor, self.enemy_start_locations[0]))


        # if (
        #     hasattr(self.unit_manager, "queens")
        #     and not self.sent_bm
        #     and self.unit_manager.queens.creep.creep_coverage > 85.0
        # ):
        #     await self.chat_send("That's over 85% of the map covered in creep")
        #     await self.chat_send("How did you let that happen?!")
        #     self.sent_bm = True

    """
    Can use `python-sc2` hooks as usual, but make a call the inherited method in the superclass
    Examples:
    """

    async def on_start(self) -> None:
        await super(MyBot, self).on_start()
        self.macro_manager = MacroManager(self)
        self.queen_manager = QueenManager(self)

        for unit in self.units(UnitID.OVERLORD):
            self.mediator.assign_role(tag=unit.tag, role=UnitRole.OVERLORD_CREEP_SPOTTER)

    async def on_unit_created(self, unit: Unit) -> None:
        await super(MyBot, self).on_unit_created(unit)
        if unit.type_id == UnitID.OVERLORD:
            self.mediator.assign_role(tag=unit.tag, role=UnitRole.OVERLORD_CREEP_SPOTTER)

    async def on_unit_destroyed(self, unit_tag: int) -> None:
        await super(MyBot, self).on_unit_destroyed(unit_tag)

    async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float) -> None:
        await super(MyBot, self).on_unit_took_damage(unit, amount_damage_taken)

        compare_health: float = max(50.0, unit.health_max * 0.09)
        if unit.health < compare_health and unit.is_structure:
            unit(AbilityId.CANCEL_BUILDINPROGRESS)
