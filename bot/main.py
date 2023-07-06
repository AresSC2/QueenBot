from typing import Optional

from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.unit import Unit
from sc2.units import Units

from ares import AresBot

from bot.custom_bot_ai import CustomBotAI
from bot.production_manager import ProductionManager
from bot.terrain_manager import TerrainManager
from bot.unit_manager import UnitManager
from bot.worker_manager import WorkerManager


class MyBot(AresBot):

    production_manager: ProductionManager
    worker_manager: WorkerManager
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

    def unit_pending(self, unit_type: UnitID) -> int:
        """ This is faster then already pending but only works for units from eggs """
        eggs: Units = self.units(UnitID.EGG)
        return len(
            [
                egg
                for egg in eggs
                if egg.orders[0].ability.button_name.upper() == unit_type.name
            ]
        )

    async def on_step(self, iteration: int) -> None:
        await super(MyBot, self).on_step(iteration)

        await self.terrain_manager.update(iteration)
        await self.unit_manager.update(iteration)
        await self.production_manager.update(iteration)
        await self.worker_manager.update(iteration)

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
        self.worker_manager = WorkerManager(self)
        self.unit_manager = UnitManager(self, self.worker_manager, self.terrain_manager)
        self.production_manager = ProductionManager(
            self, self.worker_manager, self.terrain_manager, self.unit_manager
        )

    # async def on_end(self, game_result: Result) -> None:
    #     await super(MyBot, self).on_end(game_result)
    #
    #     # custom on_end logic here ...
    #
    # async def on_building_construction_complete(self, unit: Unit) -> None:
    #     await super(MyBot, self).on_building_construction_complete(unit)
    #
    #     # custom on_building_construction_complete logic here ...
    #
    async def on_unit_created(self, unit: Unit) -> None:
        await super(MyBot, self).on_unit_created(unit)

        if unit.type_id == UnitID.OVERLORD and self.time > 10.0:
            self.unit_manager.handle_second_overlord(unit)
    #
    async def on_unit_destroyed(self, unit_tag: int) -> None:
        await super(MyBot, self).on_unit_destroyed(unit_tag)

        # checks if unit is a queen or th, lib then handles appropriately
        self.unit_manager.queens.remove_unit(unit_tag)
        if unit_tag in self.unit_manager.worker_defence_tags:
            self.unit_manager.worker_defence_tags.remove(unit_tag)
        if unit_tag in self.unit_manager.bunker_drone_tags:
            self.unit_manager.bunker_drone_tags.remove(unit_tag)
        self.worker_manager.remove_worker(unit_tag)

    async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float) -> None:
        await super(MyBot, self).on_unit_took_damage(unit, amount_damage_taken)

        compare_health: float = max(50.0, unit.health_max * 0.09)
        if unit.health < compare_health and unit.is_structure:
            unit(AbilityId.CANCEL_BUILDINPROGRESS)
