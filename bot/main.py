from typing import Optional

from ares import AresBot
from ares.behaviors.macro import Mining
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.unit import Unit

from bot.production_manager import ProductionManager
from bot.terrain_manager import TerrainManager
from bot.unit_manager import UnitManager


class MyBot(AresBot):
    production_manager: ProductionManager
    unit_manager: UnitManager
    terrain_manager: TerrainManager

    def __init__(self, game_step_override: Optional[int] = None):
        """Initiate custom bot

        Parameters
        ----------
        game_step_override :
            If provided, set the game_step to this value regardless of how it was
            specified elsewhere
        """
        super().__init__(game_step_override)
        self.unselectable_worker_tags = set()
        self.sent_bm: bool = False

    async def on_step(self, iteration: int) -> None:
        await super(MyBot, self).on_step(iteration)

        self.register_behavior(Mining())

        await self.terrain_manager.update(iteration)
        await self.unit_manager.update(iteration)
        await self.production_manager.update(iteration)

        if (
            hasattr(self.unit_manager, "queens")
            and not self.sent_bm
            and self.unit_manager.queens.creep.creep_coverage > 85.0
        ):
            await self.chat_send("That's over 85% of the map covered in creep")
            await self.chat_send("How did you let that happen?!")
            self.sent_bm = True

    """
    Can use `python-sc2` hooks as usual, but make a call the inherited method in the superclass
    Examples:
    """

    async def on_start(self) -> None:
        await super(MyBot, self).on_start()

        self.terrain_manager = TerrainManager(self)
        self.unit_manager = UnitManager(self, self.terrain_manager)
        self.production_manager = ProductionManager(
            self, self.terrain_manager, self.unit_manager
        )

    async def on_unit_created(self, unit: Unit) -> None:
        await super(MyBot, self).on_unit_created(unit)

        if unit.type_id == UnitID.OVERLORD and self.time > 10.0:
            self.unit_manager.handle_overlord(unit)

    async def on_unit_destroyed(self, unit_tag: int) -> None:
        await super(MyBot, self).on_unit_destroyed(unit_tag)

        # checks if unit is a queen or th, lib then handles appropriately
        self.unit_manager.queens.remove_unit(unit_tag)
        if unit_tag in self.unit_manager.worker_defence_tags:
            self.unit_manager.worker_defence_tags.remove(unit_tag)
        if unit_tag in self.unit_manager.bunker_drone_tags:
            self.unit_manager.bunker_drone_tags.remove(unit_tag)

    async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float) -> None:
        await super(MyBot, self).on_unit_took_damage(unit, amount_damage_taken)

        compare_health: float = max(50.0, unit.health_max * 0.09)
        if unit.health < compare_health and unit.is_structure:
            unit(AbilityId.CANCEL_BUILDINPROGRESS)
