from typing import Set
from sc2.bot_ai import BotAI
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.position import Point2, Point3
from sc2.units import Units


class CustomBotAI(BotAI):
    unselectable_worker_tags: Set[int]

    async def on_step(self, iteration: int):
        pass

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

    def draw_text_on_world(
        self,
        pos: Point2,
        text: str,
        size: int = 12,
        y_offset: int = 0,
        color=(0, 255, 255),
    ) -> None:
        """
        Will print out text in the game
        @param pos:
        @param text:
        @param size:
        @param y_offset:
        @param color:
        @return:
        """
        z_height: float = self.get_terrain_z_height(pos)
        self.client.debug_text_world(
            text,
            Point3((pos.x, pos.y + y_offset, z_height)),
            color=color,
            size=size,
        )

